from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from .. import bus, db
from .checkout import lock_session, totals
from .common import Model, audit, buyer, minor, once, rate_limit, wire
from .decisions import Reader
from .operations import listing

router = APIRouter(tags=["local-negotiation"])
public = APIRouter(tags=["local-negotiation"], dependencies=[Depends(rate_limit)])


class Money(Model):
    amount: int = Field(ge=1, le=100000000, strict=True)
    currency: str = Field(default="USD", pattern="^USD$")


class OfferLine(Model):
    id: str
    quantity: int = Field(ge=1, le=10000, strict=True)
    unit_price: Money


class Offer(Model):
    line_items: list[OfferLine] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


async def apply_prices(conn, session, lines):
    payload = session["payload"]
    prices = {x["id"]: x["unit_price"]["amount"] for x in lines}
    for line in payload["line_items"]:
        line["item"]["price"] = prices[line["id"]]
        value = prices[line["id"]] * line["quantity"]
        line["totals"] = [
            {"type": "subtotal", "amount": value},
            {"type": "total", "amount": value},
        ]
    await totals(conn, payload)
    await conn.execute(
        "UPDATE checkout_sessions SET payload=$2,updated_at=now() WHERE checkout_id=$1",
        session["checkout_id"],
        payload,
    )


@public.post("/api/storefront/checkout-sessions/{sid}/offers")
async def make_offer(sid: UUID, body: Offer, request: Request):
    owner = buyer(request)
    async with db.pool().acquire() as conn, conn.transaction():

        async def create():
            session = await lock_session(conn, sid, owner)
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM offers WHERE checkout_id=$1 AND status IN ('pending_review','countered','accepted'))",
                sid,
            ):
                raise HTTPException(409, "Resolve or withdraw the existing offer first.")
            cart = {x["id"]: x for x in session["payload"]["line_items"]}
            ids = [x.id for x in body.line_items]
            if (
                len(set(ids)) != len(ids)
                or set(ids) != set(cart)
                or any(x.quantity != cart[x.id]["quantity"] for x in body.line_items)
            ):
                raise HTTPException(
                    400, "Offers must cover every cart line with its current quantity."
                )
            products = await conn.fetch(
                "SELECT * FROM products WHERE product_id=ANY($1::text[]) ORDER BY product_id FOR UPDATE",
                ids,
            )
            costs = {p["product_id"]: minor(p["unit_cost"]) for p in products}
            policy = (await conn.fetchval("SELECT policy FROM commerce_policy WHERE id"))[
                "deal"
            ]
            okay = policy["mode"] == "auto"
            for line in body.line_items:
                proposed = line.unit_price.amount
                price = cart[line.id]["item"]["price"]
                if proposed > price:
                    raise HTTPException(400, "An offer cannot exceed the current quoted price.")
                margin = Decimal(proposed - costs[line.id]) / proposed * 100
                discount = Decimal(price - proposed) / price * 100
                okay = (
                    okay
                    and margin >= Decimal(str(policy["min_margin_pct"]))
                    and discount <= Decimal(str(policy["max_discount_pct"]))
                    and line.quantity >= policy["min_quantity"]
                )
            oid, did = uuid4(), uuid4()
            expiry = min(session["expires_at"], datetime.now(timezone.utc) + timedelta(hours=2))
            value = {
                **body.model_dump(),
                "counter": None,
                "response_note": "Accepted by owner policy."
                if okay
                else "Awaiting owner review.",
                "decided_by": "policy" if okay else None,
            }
            proposal = {"offer_id": str(oid), "line_items": body.model_dump()["line_items"]}
            await conn.execute(
                """INSERT INTO commerce_decisions(decision_id,kind,status,subject,proposal,context,explanation,gate,agent_version,expires_at,verdict)
                VALUES($1,'deal',$2,$3,$4,$5,$6,$7,'negotiation-1',$8,$9)""",
                did,
                "auto_approved" if okay else "pending",
                {"checkout_id": str(sid), "offer_id": str(oid)},
                proposal,
                {"policy": policy},
                {
                    "summary": f"Buyer requested a bulk price for {sum(x.quantity for x in body.line_items)} units. "
                    + (
                        "The request is within delegated limits."
                        if okay
                        else "Owner review is required by margin, discount or quantity policy."
                    ),
                    "factors": [],
                    "evidence": [],
                },
                {"auto": okay, "reasons": [] if okay else ["Owner review is required."]},
                expiry,
                {
                    "action": "approve",
                    "actor": "policy",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                if okay
                else None,
            )
            await conn.execute(
                """INSERT INTO offers(offer_id,checkout_id,status,payload,decision_id,expires_at)
                VALUES($1,$2,$3,$4,$5,$6)""",
                oid,
                sid,
                "accepted" if okay else "pending_review",
                value,
                did,
                expiry,
            )
            if okay:
                await apply_prices(conn, session, value["line_items"])
            await audit(
                conn,
                "offer_created",
                oid,
                "buyer",
                {"decision_id": str(did), "automatic": okay},
            )
            return wire(await conn.fetchrow("SELECT * FROM offers WHERE offer_id=$1", oid))

        result = await once(conn, request, owner, body.model_dump(), create)
    bus.emit("offer")
    bus.emit("decision")
    return result


async def decide_offer(conn, decision, verdict, session, actor):
    if (
        not session
        or session["status"] not in ("incomplete", "ready_for_complete")
        or session["expires_at"] <= datetime.now(timezone.utc)
    ):
        raise HTTPException(409, "Checkout is no longer active.")
    oid = UUID(decision["subject"]["offer_id"])
    row = await conn.fetchrow("SELECT * FROM offers WHERE offer_id=$1 FOR UPDATE", oid)
    if row["status"] != "pending_review" or row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, "Offer is no longer pending.")
    payload = row["payload"]
    if verdict.action == "reject":
        status = "declined"
    elif verdict.action == "override":
        if set(verdict.changes) != {"counter_line_items"}:
            raise HTTPException(
                400,
                "A deal override requires counter_line_items with integer minor-unit prices.",
            )
        counter = [
            OfferLine.model_validate(x).model_dump()
            for x in verdict.changes["counter_line_items"]
        ]
        original = {x["id"]: x for x in payload["line_items"]}
        if (
            len(counter) != len(original)
            or {x["id"] for x in counter} != set(original)
            or any(x["quantity"] != original[x["id"]]["quantity"] for x in counter)
        ):
            raise HTTPException(400, "Counter must preserve all cart lines and quantities.")
        payload["counter"] = {"line_items": counter}
        status = "countered"
    else:
        status = "accepted"
    selected = (
        payload["counter"]["line_items"] if status == "countered" else payload["line_items"]
    )
    if status in ("accepted", "countered"):
        costs = await conn.fetch(
            "SELECT product_id,unit_cost FROM products WHERE product_id=ANY($1::text[]) ORDER BY product_id FOR UPDATE",
            [x["id"] for x in selected],
        )
        costs = {p["product_id"]: minor(p["unit_cost"]) for p in costs}
        if any(x["unit_price"]["amount"] < costs[x["id"]] for x in selected):
            raise HTTPException(
                409, "Accepted prices and counters must cover acquisition cost."
            )
    if status == "accepted":
        await apply_prices(conn, session, payload["line_items"])
    payload["decided_by"] = actor
    payload["response_note"] = verdict.reason or status
    await conn.execute(
        "UPDATE offers SET status=$2,payload=$3 WHERE offer_id=$1", oid, status, payload
    )
    return {"offer_id": str(oid), "status": status}


@public.get("/api/storefront/checkout-sessions/{sid}/offers/{oid}")
async def get_offer(sid: UUID, oid: UUID, request: Request):
    async with db.pool().acquire() as conn:
        await lock_session(conn, sid, buyer(request), active=False)
        row = await conn.fetchrow(
            "SELECT * FROM offers WHERE offer_id=$1 AND checkout_id=$2", oid, sid
        )
    if not row:
        raise HTTPException(404, "Offer not found.")
    return wire(row)


@public.post("/api/storefront/checkout-sessions/{sid}/offers/{oid}/{action}")
async def respond_offer(sid: UUID, oid: UUID, action: str, request: Request):
    if action not in ("accept", "withdraw"):
        raise HTTPException(404, "Unknown offer action.")
    owner = buyer(request)
    async with db.pool().acquire() as conn, conn.transaction():

        async def apply():
            session = await lock_session(conn, sid, owner)
            info = await conn.fetchrow(
                "SELECT * FROM offers WHERE offer_id=$1 AND checkout_id=$2", oid, sid
            )
            if not info:
                raise HTTPException(404, "Offer not found.")
            decision = await conn.fetchrow(
                "SELECT * FROM commerce_decisions WHERE decision_id=$1 FOR UPDATE",
                info["decision_id"],
            )
            row = await conn.fetchrow("SELECT * FROM offers WHERE offer_id=$1 FOR UPDATE", oid)
            if row["status"] not in ("countered", "pending_review", "accepted") or row[
                "expires_at"
            ] <= datetime.now(timezone.utc):
                raise HTTPException(409, "Offer is no longer active.")
            payload = row["payload"]
            if action == "accept":
                if row["status"] != "countered":
                    raise HTTPException(409, "Only a counter can be accepted by the buyer.")
                await apply_prices(conn, session, payload["counter"]["line_items"])
                status = "accepted"
            else:
                if row["status"] == "accepted":
                    # Restore original live catalogue quote; accepted prices must
                    # never survive a withdrawal unnoticed.
                    from .checkout import quote

                    original = session["payload"]
                    cart = {
                        "line_items": [
                            {"item": {"id": x["item"]["id"]}, "quantity": x["quantity"]}
                            for x in original["line_items"]
                        ],
                        "buyer": original["buyer"],
                        "fulfillment": original["fulfillment"],
                        "channel": original["channel"],
                    }
                    quoted = await quote(conn, cart, sid)
                    quoted["expires_at"] = session["expires_at"].isoformat()
                    await conn.execute(
                        "UPDATE checkout_sessions SET payload=$2,status=$3 WHERE checkout_id=$1",
                        sid,
                        quoted,
                        quoted["status"],
                    )
                status = "withdrawn"
                if decision["status"] == "pending":
                    await conn.execute(
                        "UPDATE commerce_decisions SET status='rejected',verdict=$2 WHERE decision_id=$1",
                        decision["decision_id"],
                        {"action": "reject", "actor": "buyer", "reason": "Offer withdrawn"},
                    )
            await conn.execute("UPDATE offers SET status=$2 WHERE offer_id=$1", oid, status)
            await audit(conn, "offer_" + status, oid, "buyer", {})
            return wire(await conn.fetchrow("SELECT * FROM offers WHERE offer_id=$1", oid))

        result = await once(conn, request, owner, {}, apply)
    bus.emit("offer")
    bus.emit("decision")
    return result


@router.get("/api/offers")
async def offers(
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: str | None = None,
):
    return await listing("offers", page, page_size, status)
