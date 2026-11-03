"""Local supplier business: separate stock, immutable quotes, idempotent orders.

The same business operations back the supplier REST simulator and procurement.
There is no arbitrary URL fetch and no route to a real payment provider.
"""

from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException
from .common import minor, wire
from .decisions import Actor
from .. import db

router = APIRouter(prefix="/api/simulator/supplier", tags=["simulated-supplier"])


async def quote(conn, supplier_id, lines):
    ids = [x["product_id"] for x in lines]
    rows = await conn.fetch(
        "SELECT * FROM supplier_items WHERE supplier_id=$1 AND product_id=ANY($2::text[]) ORDER BY product_id FOR UPDATE",
        supplier_id,
        ids,
    )
    by_id = {x["product_id"]: x for x in rows}
    for line in lines:
        item = by_id.get(line["product_id"])
        if not item or line["quantity"] > item["available"]:
            raise HTTPException(409, "The simulated supplier has insufficient stock.")
        if str(line["unit_cost"]) != str(item["unit_cost"]):
            from decimal import Decimal

            if Decimal(str(line["unit_cost"])) != item["unit_cost"]:
                raise HTTPException(
                    409,
                    "Purchase cost differs from the supplier quote. Refresh the supplier catalogue.",
                )
    return {
        "currency": "USD",
        "test_only": True,
        "line_items": [
            {
                "id": x["product_id"],
                "quantity": x["quantity"],
                "item": {
                    "id": x["product_id"],
                    "title": by_id[x["product_id"]]["title"],
                    "price": minor(by_id[x["product_id"]]["unit_cost"]),
                },
            }
            for x in lines
        ],
    }


async def complete(conn, po):
    row = await conn.fetchrow(
        "SELECT * FROM supplier_checkouts WHERE po_id=$1 FOR UPDATE", po["po_id"]
    )
    if row["status"] == "completed":
        return row["order_id"]
    if row["status"] != "ready_for_complete":
        raise HTTPException(409, "Supplier checkout is not active.")
    payload = row["payload"]
    ids = [x["id"] for x in payload["line_items"]]
    await conn.fetch(
        "SELECT product_id FROM supplier_items WHERE supplier_id=$1 AND product_id=ANY($2::text[]) ORDER BY product_id FOR UPDATE",
        po["supplier_id"],
        ids,
    )
    for line in payload["line_items"]:
        success = await conn.fetchval(
            """UPDATE supplier_items SET available=available-$3 WHERE supplier_id=$1 AND product_id=$2
            AND available>=$3 RETURNING available""",
            po["supplier_id"],
            line["id"],
            line["quantity"],
        )
        if success is None:
            raise HTTPException(409, "Supplier stock changed; checkout cannot complete.")
    oid = uuid4()
    await conn.execute(
        "UPDATE supplier_checkouts SET status='completed',order_id=$2 WHERE po_id=$1",
        po["po_id"],
        oid,
    )
    return oid


async def cancel(conn, po):
    row = await conn.fetchrow(
        "SELECT * FROM supplier_checkouts WHERE po_id=$1 FOR UPDATE", po["po_id"]
    )
    if row["status"] == "completed":
        for line in sorted(row["payload"]["line_items"], key=lambda x: x["id"]):
            await conn.execute(
                "UPDATE supplier_items SET available=available+$3 WHERE supplier_id=$1 AND product_id=$2",
                po["supplier_id"],
                line["id"],
                line["quantity"],
            )
    await conn.execute(
        "UPDATE supplier_checkouts SET status='canceled' WHERE po_id=$1", po["po_id"]
    )


@router.get("/{sid}/catalog")
async def catalog(sid: UUID, actor: Actor):
    rows = await db.fetch(
        "SELECT * FROM supplier_items WHERE supplier_id=$1 ORDER BY product_id", sid
    )
    return {
        "test_only": True,
        "products": [
            {
                "id": x["product_id"],
                "title": x["title"],
                "price": {"amount": minor(x["unit_cost"]), "currency": "USD"},
                "available": x["available"],
            }
            for x in rows
        ],
    }


@router.get("/checkouts/{cid}")
async def checkout(cid: UUID, actor: Actor):
    row = await db.fetchrow("SELECT * FROM supplier_checkouts WHERE checkout_id=$1", cid)
    if not row:
        raise HTTPException(404, "Simulated supplier checkout not found.")
    return wire(row)


@router.get("/orders/{oid}")
async def order(oid: UUID, actor: Actor):
    row = await db.fetchrow(
        "SELECT order_id,checkout_id,status,payload FROM supplier_checkouts WHERE order_id=$1 AND status='completed'",
        oid,
    )
    if not row:
        raise HTTPException(404, "Simulated supplier order not found.")
    return wire(row)


async def order_purchase(conn, po, actor):
    from .common import audit

    oid = await complete(conn, po)
    await conn.execute(
        "UPDATE purchase_orders SET status='ordered',supplier_order_id=$2 WHERE po_id=$1",
        po["po_id"],
        "sim-" + str(oid),
    )
    await audit(
        conn,
        "simulated_supplier_checkout",
        po["po_id"],
        actor,
        {
            "checkout_id": str(po["supplier_checkout_id"]),
            "order_id": str(oid),
            "test_only": True,
        },
    )
    return dict(
        await conn.fetchrow("SELECT * FROM purchase_orders WHERE po_id=$1", po["po_id"])
    )
