from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from .. import auth, bus, db
from .common import Model, audit, dollars, ledger, movement, once, wire

router = APIRouter(tags=["commerce-operations"])
Reader = Annotated[auth.CurrentUser, Depends(auth.current_user)]
Editor = Annotated[auth.CurrentUser, Depends(auth.require_editor)]
Manager = Annotated[auth.CurrentUser, Depends(auth.require_manager)]


async def listing(table, page=1, page_size=25, status=None):
    # table is an internal constant, never a request parameter.
    where = " WHERE status=$1" if status else ""
    args = [status] if status else []
    total = await db.fetchval(f"SELECT count(*) FROM {table}" + where, *args)
    rows = await db.fetch(
        f"SELECT * FROM {table}"
        + where
        + f" ORDER BY created_at DESC LIMIT {page_size} OFFSET {(page - 1) * page_size}",
        *args,
    )
    return {
        "rows": wire(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/api/orders")
async def orders(
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: str | None = None,
):
    result = await listing("orders", page, page_size, status)
    for row in result["rows"]:
        row.pop("owner_hash", None)
    return result


@router.get("/api/orders/{oid}")
async def order(oid: UUID, user: Reader):
    row = await db.fetchrow(
        "SELECT order_id,checkout_id,status,channel,payload,created_at FROM orders WHERE order_id=$1",
        oid,
    )
    if not row:
        raise HTTPException(404, "Order not found.")
    return wire(row)


class EventLine(Model):
    id: str
    quantity: int = Field(ge=1, le=10000, strict=True)


class OrderEvent(Model):
    type: Literal["shipped", "delivered", "canceled", "returned_to_sender"]
    line_items: list[EventLine] = Field(min_length=1, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=200)


@router.post("/api/orders/{oid}/events")
async def order_event(oid: UUID, body: OrderEvent, request: Request, user: Editor):
    async with db.pool().acquire() as conn, conn.transaction():

        async def apply():
            row = await conn.fetchrow("SELECT * FROM orders WHERE order_id=$1 FOR UPDATE", oid)
            if not row:
                raise HTTPException(404, "Order not found.")
            payload = row["payload"]
            lines = {x["id"]: x for x in payload["line_items"]}
            ids = [x.id for x in body.line_items]
            if len(set(ids)) != len(ids) or any(id not in lines for id in ids):
                raise HTTPException(
                    400, "Event line ids must be unique and belong to this order."
                )
            products = await conn.fetch(
                "SELECT * FROM products WHERE product_id=ANY($1::text[]) ORDER BY product_id FOR UPDATE",
                ids,
            )
            products = {x["product_id"]: x for x in products}
            refund_merchandise = refund_tax = 0
            for change in body.line_items:
                line = lines[change.id]
                n = change.quantity
                remaining = line["quantity"] - line["canceled"] - line["fulfilled"]
                if body.type == "shipped":
                    if n > remaining:
                        raise HTTPException(
                            409, "Cannot ship more than the unfulfilled quantity."
                        )
                    line["fulfilled"] += n
                elif body.type == "delivered":
                    if n > line["fulfilled"] - line["delivered"]:
                        raise HTTPException(409, "Only shipped units can be delivered once.")
                    line["delivered"] += n
                else:
                    available = (
                        remaining
                        if body.type == "canceled"
                        else line["fulfilled"] - line["returned"]
                    )
                    if n > available:
                        raise HTTPException(
                            409, "The cancellation/return exceeds the remaining quantity."
                        )
                    line["canceled" if body.type == "canceled" else "returned"] += n
                    await movement(
                        conn, change.id, n, "return", user.username, body.type, order_id=oid
                    )
                    await ledger(
                        conn,
                        "refund",
                        -dollars(line["item"]["price"] * n),
                        products[change.id],
                        -n,
                        body.type,
                        order_id=oid,
                        channel=row["channel"],
                        unit_cost=dollars(line["unit_cost_minor"]),
                    )
                    tax_total = line.get("tax_total_minor", 0)
                    refunded = (
                        tax_total * (line["canceled"] + line["returned"]) // line["quantity"]
                    )
                    tax_delta = refunded - line.get("tax_refunded_minor", 0)
                    refund_merchandise += line["item"]["price"] * n
                    refund_tax += tax_delta
                    line["tax_refunded_minor"] = refunded
                    if tax_delta:
                        await conn.execute(
                            "INSERT INTO ledger_entries(entry_id,type,amount,product_id,order_id,channel,memo,account) VALUES($1,'refund',$2,$3,$4,$5,'Refund test sales tax','tax_payable')",
                            uuid4(),
                            -dollars(tax_delta),
                            change.id,
                            oid,
                            row["channel"],
                        )
            status = (
                "canceled"
                if all(x["canceled"] == x["quantity"] for x in lines.values())
                else (
                    "fulfilled"
                    if all(
                        x["fulfilled"] + x["canceled"] == x["quantity"] for x in lines.values()
                    )
                    else (
                        "partial"
                        if any(x["fulfilled"] or x["canceled"] for x in lines.values())
                        else "processing"
                    )
                )
            )
            event = {
                "id": str(uuid4()),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                **body.model_dump(),
            }
            if refund_merchandise or refund_tax:
                payload.setdefault("adjustments", []).append(
                    {
                        "id": event["id"],
                        "type": "refund",
                        "occurred_at": event["occurred_at"],
                        "status": "completed",
                        "description": body.type,
                        "line_items": [
                            {"id": x.id, "quantity": -x.quantity} for x in body.line_items
                        ],
                        "totals": [
                            {"type": "subtotal", "amount": -refund_merchandise},
                            {"type": "tax", "amount": -refund_tax},
                            {"type": "total", "amount": -(refund_merchandise + refund_tax)},
                        ],
                    }
                )
            payload["fulfillment"]["events"].append(event)
            payload["status"] = status
            await conn.execute(
                "INSERT INTO order_events(event_id,order_id,type,payload,actor) VALUES($1,$2,$3,$4,$5)",
                UUID(event["id"]),
                oid,
                body.type,
                event,
                user.username,
            )
            await conn.execute(
                "UPDATE orders SET status=$2,payload=$3 WHERE order_id=$1", oid, status, payload
            )
            from .webhooks import notify

            await notify(conn, payload)
            await audit(conn, "order_event", oid, user.username, event)
            return payload

        result = await once(conn, request, user.username, body.model_dump(), apply)
    bus.emit("order", id=str(oid))
    bus.emit("products")
    return wire(result)


class Adjustment(Model):
    product_id: str
    type: Literal["adjustment", "shrink"]
    quantity: int = Field(ge=-100000, le=100000, strict=True)
    note: str = Field(min_length=3, max_length=500)


@router.post("/api/inventory/movements")
async def adjust(body: Adjustment, request: Request, user: Editor):
    if body.quantity == 0 or (body.type == "shrink" and body.quantity > 0):
        raise HTTPException(400, "Adjustment must be nonzero; shrink must be negative.")
    async with db.pool().acquire() as conn, conn.transaction():

        async def apply():
            p = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id=$1 FOR UPDATE", body.product_id
            )
            if not p:
                raise HTTPException(404, "Product not found.")
            stock = await movement(
                conn, body.product_id, body.quantity, body.type, user.username, body.note
            )
            await ledger(
                conn,
                "adjustment",
                p["unit_cost"] * body.quantity,
                p,
                0,
                body.note,
                account="inventory_adjustment",
            )
            await audit(
                conn, "inventory_adjustment", body.product_id, user.username, body.model_dump()
            )
            return {"stock_on_hand": stock}

        result = await once(conn, request, user.username, body.model_dump(), apply)
    bus.emit("products")
    return result


@router.get("/api/inventory/movements")
async def movements(
    user: Reader,
    product_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    rows = await db.fetch(
        """SELECT * FROM inventory_movements WHERE ($1::text IS NULL OR product_id=$1)
        ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
        product_id,
        page_size,
        (page - 1) * page_size,
    )
    total = await db.fetchval(
        "SELECT count(*) FROM inventory_movements WHERE ($1::text IS NULL OR product_id=$1)",
        product_id,
    )
    return {
        "rows": wire(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


class POLine(Model):
    product_id: str
    quantity: int = Field(ge=1, le=100000, strict=True)
    unit_cost: Decimal = Field(ge=0, lt=1000000, decimal_places=2)


class Purchase(Model):
    supplier_id: UUID
    lines: list[POLine] = Field(min_length=1, max_length=100)
    deliver_by: date | None = None


async def create_po(conn, body, actor, decision_id=None):
    supplier = await conn.fetchrow(
        "SELECT * FROM suppliers WHERE supplier_id=$1", body.supplier_id
    )
    if not supplier or not supplier["simulated"]:
        raise HTTPException(
            409,
            "Only an explicitly simulated supplier can be ordered in this development environment.",
        )
    ids = [x.product_id for x in body.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Duplicate purchase-order products.")
    products = await conn.fetch(
        "SELECT * FROM products WHERE product_id=ANY($1::text[]) ORDER BY product_id FOR UPDATE",
        ids,
    )
    if len(products) != len(ids) or any(p["supplier_id"] != body.supplier_id for p in products):
        raise HTTPException(409, "Every product must belong to this supplier.")
    po = uuid4()
    lines = [{**wire(x.model_dump()), "received": 0} for x in body.lines]
    checkout = uuid4()
    from .supplier import quote

    supplier_quote = await quote(conn, body.supplier_id, lines)
    await conn.execute(
        """INSERT INTO purchase_orders(po_id,supplier_id,decision_id,status,lines,deliver_by,created_by,supplier_checkout_id)
        VALUES($1,$2,$3,'draft',$4,$5,$6,$7)""",
        po,
        body.supplier_id,
        decision_id,
        lines,
        body.deliver_by,
        actor,
        checkout,
    )
    await conn.execute(
        "INSERT INTO supplier_checkouts(checkout_id,po_id,status,payload) VALUES($1,$2,'ready_for_complete',$3)",
        checkout,
        po,
        supplier_quote,
    )
    await audit(
        conn,
        "purchase_created",
        po,
        actor,
        {"supplier_id": str(body.supplier_id), "lines": lines},
    )
    return dict(await conn.fetchrow("SELECT * FROM purchase_orders WHERE po_id=$1", po))


@router.post("/api/purchase-orders")
async def purchase(body: Purchase, request: Request, user: Manager):
    async with db.pool().acquire() as conn, conn.transaction():

        async def create():
            return await create_po(conn, body, user.username)

        result = await once(conn, request, user.username, body.model_dump(), create)
    bus.emit("purchase_order")
    return result


@router.get("/api/purchase-orders")
async def purchases(
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: str | None = None,
):
    return await listing("purchase_orders", page, page_size, status)


@router.get("/api/purchase-orders/{po}")
async def purchase_detail(po: UUID, user: Reader):
    row = await db.fetchrow("SELECT * FROM purchase_orders WHERE po_id=$1", po)
    if not row:
        raise HTTPException(404, "Purchase order not found.")
    return wire(row)


@router.post("/api/purchase-orders/{po}/order")
async def place_purchase(po: UUID, request: Request, user: Manager):
    async with db.pool().acquire() as conn, conn.transaction():

        async def apply():
            row = await conn.fetchrow(
                "SELECT * FROM purchase_orders WHERE po_id=$1 FOR UPDATE", po
            )
            if not row or row["status"] != "draft":
                raise HTTPException(409, "Only a draft purchase can be ordered.")
            checkout = await conn.fetchrow(
                "SELECT * FROM supplier_checkouts WHERE po_id=$1 FOR UPDATE", po
            )
            from .supplier import complete

            oid = await complete(conn, row)
            await conn.execute(
                "UPDATE purchase_orders SET status='ordered',supplier_order_id=$2 WHERE po_id=$1",
                po,
                "sim-" + str(oid),
            )
            await audit(
                conn,
                "simulated_supplier_checkout",
                po,
                user.username,
                {
                    "checkout_id": str(checkout["checkout_id"]),
                    "order_id": str(oid),
                    "test_only": True,
                },
            )
            return wire(await conn.fetchrow("SELECT * FROM purchase_orders WHERE po_id=$1", po))

        result = await once(conn, request, user.username, {}, apply)
    bus.emit("purchase_order")
    return result


class ReceiptLine(Model):
    product_id: str
    quantity_received: int = Field(ge=1, le=100000, strict=True)


class Receipt(Model):
    lines: list[ReceiptLine] = Field(min_length=1, max_length=100)


@router.post("/api/purchase-orders/{po}/receive")
async def receive(po: UUID, body: Receipt, request: Request, user: Editor):
    async with db.pool().acquire() as conn, conn.transaction():

        async def apply():
            row = await conn.fetchrow(
                "SELECT * FROM purchase_orders WHERE po_id=$1 FOR UPDATE", po
            )
            if not row or row["status"] not in ("ordered", "partially_received"):
                raise HTTPException(409, "Purchase is not awaiting delivery.")
            lines = {x["product_id"]: x for x in row["lines"]}
            ids = [x.product_id for x in body.lines]
            if len(set(ids)) != len(ids) or any(id not in lines for id in ids):
                raise HTTPException(
                    400, "Receipt lines must be unique and belong to the purchase."
                )
            products = await conn.fetch(
                "SELECT * FROM products WHERE product_id=ANY($1::text[]) ORDER BY product_id FOR UPDATE",
                ids,
            )
            products = {p["product_id"]: p for p in products}
            for entry in body.lines:
                line = lines[entry.product_id]
                n = entry.quantity_received
                if n > line["quantity"] - line["received"]:
                    raise HTTPException(409, "Receipt exceeds outstanding quantity.")
                line["received"] += n
                await movement(
                    conn,
                    entry.product_id,
                    n,
                    "receipt",
                    user.username,
                    "Supplier receipt",
                    po_id=po,
                )
                await ledger(
                    conn,
                    "purchase",
                    Decimal(line["unit_cost"]) * n,
                    products[entry.product_id],
                    0,
                    "Supplier receipt",
                    po_id=po,
                )
                # Weighted acquisition cost, while preserving historical sale costs.
                p = await conn.fetchrow(
                    "SELECT stock_on_hand,unit_cost FROM products WHERE product_id=$1",
                    entry.product_id,
                )
                cost = (
                    (p["stock_on_hand"] - n) * p["unit_cost"] + n * Decimal(line["unit_cost"])
                ) / p["stock_on_hand"]
                await conn.execute(
                    "UPDATE products SET unit_cost=$2 WHERE product_id=$1",
                    entry.product_id,
                    cost.quantize(Decimal(".01")),
                )
            status = (
                "received"
                if all(x["received"] == x["quantity"] for x in lines.values())
                else "partially_received"
            )
            await conn.execute(
                "UPDATE purchase_orders SET status=$2,lines=$3 WHERE po_id=$1",
                po,
                status,
                list(lines.values()),
            )
            await audit(conn, "purchase_received", po, user.username, body.model_dump())
            return wire(await conn.fetchrow("SELECT * FROM purchase_orders WHERE po_id=$1", po))

        result = await once(conn, request, user.username, body.model_dump(), apply)
    bus.emit("purchase_order")
    bus.emit("products")
    return result


@router.post("/api/purchase-orders/{po}/cancel")
async def cancel_purchase(po: UUID, request: Request, user: Manager):
    async with db.pool().acquire() as conn, conn.transaction():

        async def apply():
            row = await conn.fetchrow(
                "SELECT * FROM purchase_orders WHERE po_id=$1 FOR UPDATE", po
            )
            if not row or row["status"] not in ("draft", "ordered"):
                raise HTTPException(409, "Only a draft or unreceived purchase can be canceled.")
            await conn.execute(
                "UPDATE purchase_orders SET status='canceled' WHERE po_id=$1", po
            )
            from .supplier import cancel

            await cancel(conn, row)
            await audit(conn, "purchase_canceled", po, user.username, {})
            return {"po_id": str(po), "status": "canceled"}

        return await once(conn, request, user.username, {}, apply)


@router.get("/api/accounting/ledger")
async def ledger_list(
    user: Manager,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    where = " WHERE ($1::date IS NULL OR created_at >= $1::date) AND ($2::date IS NULL OR created_at < $2::date+1)"
    rows = await db.fetch(
        "SELECT * FROM ledger_entries" + where + " ORDER BY created_at DESC LIMIT $3 OFFSET $4",
        start,
        end,
        page_size,
        (page - 1) * page_size,
    )
    total = await db.fetchval("SELECT count(*) FROM ledger_entries" + where, start, end)
    return {
        "rows": wire(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/api/accounting/summary")
async def summary(
    user: Manager,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    group_by: Literal["day", "category", "channel"] = "day",
):
    expr = {
        "day": "to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD')",
        "category": "coalesce(category,'Uncategorized')",
        "channel": "coalesce(channel,'store')",
    }[group_by]
    rows = await db.fetch(
        f"""SELECT {expr} AS label,
        coalesce(sum(amount) FILTER(WHERE type IN ('sale','refund') AND account='merchandise'),0) AS revenue,
        coalesce(sum(cogs) FILTER(WHERE type IN ('sale','refund') AND account='merchandise'),0) AS cogs,
        coalesce(-sum(amount) FILTER(WHERE type='refund' AND account='merchandise'),0) AS refunds,
        coalesce(sum(amount) FILTER(WHERE type='purchase'),0) AS purchases,
        coalesce(sum(units) FILTER(WHERE type IN ('sale','refund') AND account='merchandise'),0) AS units,
        coalesce(sum(amount) FILTER(WHERE account='tax_payable'),0) AS tax_collected,
        coalesce(sum(amount) FILTER(WHERE account='shipping_income'),0) AS shipping_income,
        coalesce(sum(amount) FILTER(WHERE account='inventory_adjustment'),0) AS inventory_adjustments,
        count(DISTINCT order_id) AS orders
        FROM ledger_entries WHERE ($1::date IS NULL OR created_at >= $1::date)
        AND ($2::date IS NULL OR created_at < $2::date+1) GROUP BY 1 ORDER BY 1""",
        start,
        end,
    )
    result = {
        key: sum((r[key] for r in rows), Decimal(0) if key not in ("units", "orders") else 0)
        for key in (
            "revenue",
            "cogs",
            "refunds",
            "purchases",
            "units",
            "orders",
            "tax_collected",
            "shipping_income",
            "inventory_adjustments",
        )
    }
    result["gross_profit"] = result["revenue"] - result["cogs"]
    result["margin_pct"] = (
        round(float(result["gross_profit"] / result["revenue"] * 100), 2)
        if result["revenue"]
        else None
    )
    # Distinct orders across groups must not double count multi-category orders.
    result["orders"] = await db.fetchval(
        """SELECT count(DISTINCT order_id) FROM ledger_entries WHERE ($1::date IS NULL OR created_at >= $1::date) AND ($2::date IS NULL OR created_at < $2::date+1)""",
        start,
        end,
    )
    return wire(
        {
            **result,
            "groups": rows,
            "basis": "Merchandise revenue, net of refunds; excludes collected tax and shipping. Test payments only.",
        }
    )
