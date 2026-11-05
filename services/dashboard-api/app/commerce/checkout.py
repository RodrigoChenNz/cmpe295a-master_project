"""Checkout, orders and buyer ownership. Test payments never charge money.

Stock moves once at completion. Fulfillment never decrements it again; bounded
cancellations/returns reverse the captured sale and its captured cost.
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, PrivateAttr

from .. import db, bus
from .common import (
    Model,
    ENVELOPE,
    audit,
    buyer,
    dollars,
    enabled,
    ledger,
    minor,
    movement,
    once,
    rate_limit,
    wire,
)

router = APIRouter(tags=["development-commerce"], dependencies=[Depends(rate_limit)])


class Item(Model):
    id: str = Field(pattern=r"^SKU-[0-9]{3,}$")


class CartLine(Model):
    item: Item
    quantity: int = Field(ge=1, le=10000, strict=True)
    id: str | None = None


class Buyer(Model):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)


class Fulfillment(Model):
    method: Literal["pickup", "shipping"]
    address: str | None = Field(default=None, max_length=500)


class Cart(Model):
    _ucp_context: dict | None = PrivateAttr(default=None)
    line_items: list[CartLine] = Field(min_length=1, max_length=100)
    buyer: Buyer | None = None
    fulfillment: Fulfillment | None = None
    channel: Literal["storefront", "ucp"] = "ucp"


class Complete(Model):
    payment_token: Literal["test_approved", "test_declined"]


async def lock_session(conn, sid, owner, *, active=True):
    row = await conn.fetchrow(
        "SELECT * FROM checkout_sessions WHERE checkout_id=$1 FOR UPDATE", sid
    )
    if not row or row["owner_hash"] != owner:
        raise HTTPException(404, "Checkout not found.")
    if active and (
        row["status"] in ("completed", "canceled")
        or row["expires_at"] <= datetime.now(timezone.utc)
    ):
        raise HTTPException(409, "Checkout is completed, canceled or expired.")
    return row


async def quote(conn, cart, sid):
    ids = [x["item"]["id"] for x in cart["line_items"]]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Each product must occur once in the cart.")
    products = await conn.fetch(
        """SELECT p.*, (SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1) AS shelf_price
        FROM products p WHERE p.product_id=ANY($1::text[]) ORDER BY p.product_id FOR UPDATE""",
        ids,
    )
    by_id = {p["product_id"]: p for p in products}
    lines = []
    for entry in cart["line_items"]:
        p = by_id.get(entry["item"]["id"])
        if (
            not p
            or not p["online"]
            or p["archived_at"]
            or (p["online_price"] or p["shelf_price"]) is None
        ):
            raise HTTPException(409, f"{entry['item']['id']} is unavailable online.")
        if p["stock_on_hand"] is None or entry["quantity"] > p["stock_on_hand"]:
            raise HTTPException(409, f"{p['product_id']}: requested quantity is unavailable.")
        price = minor(p["online_price"] or p["shelf_price"])
        qty = entry["quantity"]
        lines.append(
            {
                "id": p["product_id"],
                "item": {"id": p["product_id"], "title": p["name"], "price": price},
                "quantity": qty,
                "totals": [
                    {"type": "subtotal", "amount": price * qty},
                    {"type": "total", "amount": price * qty},
                ],
            }
        )
    payload = {
        "ucp": ENVELOPE,
        "id": str(sid),
        "currency": "USD",
        "status": "incomplete",
        "line_items": lines,
        "buyer": cart.get("buyer"),
        "fulfillment": cart.get("fulfillment"),
        "channel": cart["channel"],
        "payment": {"handler": "shelfiq_local_test", "test_only": True},
        "messages": [],
    }
    if cart.get("ucp_context"):
        payload["_ucp"] = cart["ucp_context"]
    if not payload["buyer"]:
        payload["messages"].append(
            {
                "type": "error",
                "code": "missing",
                "path": "$.buyer",
                "content": "Buyer email is required.",
                "severity": "recoverable",
            }
        )
    fulfillment = payload["fulfillment"]
    if not fulfillment or (
        fulfillment["method"] == "shipping" and not fulfillment.get("address")
    ):
        payload["messages"].append(
            {
                "type": "error",
                "code": "missing",
                "path": "$.fulfillment",
                "content": "Choose pickup or provide a shipping address.",
                "severity": "recoverable",
            }
        )
    payload["status"] = "incomplete" if payload["messages"] else "ready_for_complete"
    await totals(conn, payload)
    return payload


async def totals(conn, payload):
    setting = await conn.fetchrow(
        "SELECT shipping_minor,tax_bps,currency FROM store_settings WHERE id"
    )
    if setting["currency"] != "USD":
        raise HTTPException(409, "This development payment handler supports USD only.")
    subtotal = sum(x["item"]["price"] * x["quantity"] for x in payload["line_items"])
    tax = (subtotal * setting["tax_bps"] + 5000) // 10000
    shipping = (
        setting["shipping_minor"]
        if (payload.get("fulfillment") or {}).get("method") == "shipping"
        else 0
    )
    payload["totals"] = [
        {"type": "subtotal", "amount": subtotal},
        {"type": "tax", "amount": tax},
        {"type": "shipping", "amount": shipping},
        {"type": "total", "amount": subtotal + tax + shipping},
    ]


@router.post("/api/storefront/checkout-sessions")
async def create_cart(body: Cart, request: Request):
    owner = buyer(request)
    cart_data = {**body.model_dump(), "ucp_context": body._ucp_context}
    async with db.pool().acquire() as conn, conn.transaction():
        await enabled(conn)

        async def create():
            sid = uuid4()
            payload = await quote(conn, cart_data, sid)
            expiry = await conn.fetchval(
                """INSERT INTO checkout_sessions(checkout_id,owner_hash,status,payload)
                VALUES($1,$2,$3,$4) RETURNING expires_at""",
                sid,
                owner,
                payload["status"],
                payload,
            )
            payload["expires_at"] = expiry.isoformat()
            await conn.execute(
                "UPDATE checkout_sessions SET payload=$2 WHERE checkout_id=$1", sid, payload
            )
            if body._ucp_context:
                await conn.execute(
                    "INSERT INTO ucp_sessions(checkout_id,profile_url,capabilities,context) VALUES($1,$2,$3,$4)",
                    sid,
                    body._ucp_context["profile_url"],
                    body._ucp_context["capabilities"],
                    body._ucp_context,
                )
            return payload

        return await once(conn, request, owner, cart_data, create)


@router.get("/api/storefront/checkout-sessions/{sid}")
async def get_cart(sid: UUID, request: Request):
    async with db.pool().acquire() as conn:
        row = await lock_session(conn, sid, buyer(request), active=False)
        result = row["payload"]
        if row["expires_at"] <= datetime.now(timezone.utc) and row["status"] not in (
            "completed",
            "canceled",
        ):
            result = {
                **result,
                "status": "canceled",
                "messages": [
                    {
                        "type": "error",
                        "code": "expired",
                        "content": "Checkout expired.",
                        "severity": "unrecoverable",
                    }
                ],
            }
        return result


@router.put("/api/storefront/checkout-sessions/{sid}")
async def update_cart(sid: UUID, body: Cart, request: Request):
    owner = buyer(request)
    cart_data = {**body.model_dump(), "ucp_context": body._ucp_context}
    async with db.pool().acquire() as conn, conn.transaction():
        await enabled(conn)

        async def update():
            row = await lock_session(conn, sid, owner)
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM offers WHERE checkout_id=$1 AND status IN ('accepted','countered','pending_review'))",
                sid,
            ):
                raise HTTPException(
                    409, "Withdraw the active offer before replacing this cart."
                )
            payload = await quote(conn, cart_data, sid)
            payload["expires_at"] = row["expires_at"].isoformat()
            await conn.execute(
                "UPDATE checkout_sessions SET payload=$2,status=$3,updated_at=now() WHERE checkout_id=$1",
                sid,
                payload,
                payload["status"],
            )
            return payload

        return await once(conn, request, owner, cart_data, update)


@router.post("/api/storefront/checkout-sessions/{sid}/cancel")
async def cancel_cart(sid: UUID, request: Request):
    owner = buyer(request)
    async with db.pool().acquire() as conn, conn.transaction():

        async def cancel():
            row = await lock_session(conn, sid, owner)
            payload = {**row["payload"], "status": "canceled"}
            await conn.execute(
                "UPDATE offers SET status='withdrawn' WHERE checkout_id=$1 AND status IN ('pending_review','countered','accepted')",
                sid,
            )
            await conn.execute(
                "UPDATE commerce_decisions SET status='rejected',verdict=$2 WHERE decision_id IN (SELECT decision_id FROM offers WHERE checkout_id=$1) AND status='pending'",
                sid,
                {"action": "reject", "actor": "buyer", "reason": "Cart canceled"},
            )
            await conn.execute(
                "UPDATE checkout_sessions SET status='canceled',payload=$2 WHERE checkout_id=$1",
                sid,
                payload,
            )
            return payload

        return await once(conn, request, owner, {}, cancel)


@router.post("/api/storefront/checkout-sessions/{sid}/complete")
async def complete_cart(sid: UUID, body: Complete, request: Request):
    owner = buyer(request)
    async with db.pool().acquire() as conn, conn.transaction():
        await enabled(conn)

        async def complete():
            row = await lock_session(conn, sid, owner)
            payload = row["payload"]
            if row["status"] != "ready_for_complete":
                raise HTTPException(409, "Buyer and fulfillment information are required.")
            if body.payment_token == "test_declined":
                raise HTTPException(402, "Test payment declined. No money was charged.")
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM offers WHERE checkout_id=$1 AND status IN ('pending_review','countered'))",
                sid,
            ):
                raise HTTPException(
                    409, "Resolve or withdraw the offer before completing checkout."
                )
            # Lock products in canonical order before consuming the quoted prices.
            products = await conn.fetch(
                "SELECT * FROM products WHERE product_id=ANY($1::text[]) ORDER BY product_id FOR UPDATE",
                [x["item"]["id"] for x in payload["line_items"]],
            )
            by_id = {p["product_id"]: p for p in products}
            oid = uuid4()
            order = {
                **payload,
                "id": str(oid),
                "checkout_id": str(sid),
                "status": "processing",
                "payment": {"status": "test_paid", "test_only": True},
                "fulfillment": {**payload["fulfillment"], "events": []},
            }
            tax_remaining = next(x["amount"] for x in payload["totals"] if x["type"] == "tax")
            merchandise_total = sum(
                x["item"]["price"] * x["quantity"] for x in order["line_items"]
            )
            for index, line in enumerate(order["line_items"]):
                tax = (
                    tax_remaining
                    if index == len(order["line_items"]) - 1
                    else (
                        next(x["amount"] for x in payload["totals"] if x["type"] == "tax")
                        * line["item"]["price"]
                        * line["quantity"]
                        // merchandise_total
                    )
                )
                tax_remaining -= tax
                line["tax_total_minor"] = tax
                line["tax_refunded_minor"] = 0
                p = by_id[line["item"]["id"]]
                if not p["online"] or p["archived_at"]:
                    raise HTTPException(409, "An item is no longer available online.")
                # Current cost is captured once; future cost edits cannot rewrite profit.
                line["unit_cost_minor"] = minor(p["unit_cost"])
                line["fulfilled"] = 0
                line["delivered"] = 0
                line["returned"] = 0
                line["canceled"] = 0
            await conn.execute(
                "INSERT INTO orders(order_id,checkout_id,owner_hash,channel,payload) VALUES($1,$2,$3,$4,$5)",
                oid,
                sid,
                owner,
                payload["channel"],
                order,
            )
            for line in order["line_items"]:
                p = by_id[line["item"]["id"]]
                await movement(
                    conn,
                    p["product_id"],
                    -line["quantity"],
                    "sale",
                    "buyer",
                    "Test checkout completed",
                    order_id=oid,
                )
                await ledger(
                    conn,
                    "sale",
                    dollars(line["item"]["price"] * line["quantity"]),
                    p,
                    line["quantity"],
                    "Test sale",
                    order_id=oid,
                    channel=payload["channel"],
                )
            for line in order["line_items"]:
                if line["tax_total_minor"]:
                    await conn.execute(
                        "INSERT INTO ledger_entries(entry_id,type,amount,product_id,order_id,channel,memo,account) VALUES($1,'sale',$2,$3,$4,$5,'Collected test sales tax','tax_payable')",
                        uuid4(),
                        dollars(line["tax_total_minor"]),
                        line["item"]["id"],
                        oid,
                        payload["channel"],
                    )
            shipping = next(x["amount"] for x in payload["totals"] if x["type"] == "shipping")
            if shipping:
                await conn.execute(
                    "INSERT INTO ledger_entries(entry_id,type,amount,order_id,channel,memo,account) VALUES($1,'sale',$2,$3,$4,'Test shipping, nonrefundable','shipping_income')",
                    uuid4(),
                    dollars(shipping),
                    oid,
                    payload["channel"],
                )
            from .webhooks import notify

            await notify(conn, order)
            charges = sum(
                t["amount"] for t in payload["totals"] if t["type"] in ("tax", "shipping")
            )
            # Tax/shipping are accounted separately from merchandise revenue/cost.
            await audit(
                conn,
                "checkout_completed",
                oid,
                "buyer",
                {"test_only": True, "non_merchandise_minor": charges},
            )
            result = {
                **payload,
                "status": "completed",
                "order": {"id": str(oid), "permalink_url": f"/shop/orders/{oid}"},
            }
            await conn.execute(
                "UPDATE checkout_sessions SET status='completed',payload=$2 WHERE checkout_id=$1",
                sid,
                result,
            )
            return result

        result = await once(conn, request, owner, body.model_dump(), complete)
    bus.emit("order", id=result["order"]["id"])
    bus.emit("products")
    return result


def buyer_order(payload):
    result = wire(payload)
    result.pop("_ucp", None)
    for line in result["line_items"]:
        line.pop("unit_cost_minor", None)
    return result


@router.get("/api/storefront/orders/{oid}")
async def get_order(oid: UUID, request: Request):
    row = await db.fetchrow(
        "SELECT * FROM orders WHERE order_id=$1 AND owner_hash=$2", oid, buyer(request)
    )
    if not row:
        raise HTTPException(404, "Order not found.")
    return buyer_order(row["payload"])
