from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .. import db

VERSION = "2026-08-25"
ENVELOPE = {
    "version": VERSION,
    "capabilities": {"dev.ucp.shopping.checkout": [{"version": VERSION}]},
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def wire(value):
    if isinstance(value, (dict,)) or hasattr(value, "items"):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def minor(value):
    return int(Decimal(str(value)) * 100)


def dollars(value):
    return Decimal(value) / 100


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def buyer(request: Request):
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer ") or not 32 <= len(token[7:]) <= 256:
        raise HTTPException(
            401,
            "Supply a random buyer Bearer token of at least 32 characters, including on checkout creation.",
        )
    return digest(token[7:])


async def enabled(conn):
    if not await conn.fetchval("SELECT commerce_enabled FROM store_settings WHERE id"):
        raise HTTPException(
            503, "The development store is closed. A manager must enable commerce."
        )


async def audit(conn, event, subject, actor, detail):
    await conn.execute(
        "INSERT INTO commerce_audit(event,subject,actor,detail) VALUES($1,$2,$3,$4)",
        event,
        str(subject),
        actor,
        wire(detail),
    )


async def once(conn, request: Request, caller, body, operation):
    key = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(key) <= 200:
        raise HTTPException(
            400, "An Idempotency-Key of 8–200 characters is required for mutations."
        )
    fingerprint = digest(
        json.dumps([request.method, request.url.path, wire(body)], sort_keys=True)
    )
    # The row may not exist yet; serialise first writers as well as retries.
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", caller + ":" + key
    )
    old = await conn.fetchrow(
        "SELECT * FROM commerce_requests WHERE caller_hash=$1 AND key=$2", caller, key
    )
    if old:
        if old["fingerprint"] != fingerprint:
            raise HTTPException(409, "That idempotency key was used for another request.")
        return old["response"]
    result = wire(await operation())
    await conn.execute(
        "INSERT INTO commerce_requests(caller_hash,key,fingerprint,response) VALUES($1,$2,$3,$4)",
        caller,
        key,
        fingerprint,
        result,
    )
    return result


async def rate_limit(request: Request):
    # Network identity, never the caller-controlled UCP-Agent header.
    caller = request.client.host if request.client else "unknown"
    n = await db.fetchval(
        """INSERT INTO commerce_rate_limits(caller,minute,count)
        VALUES($1,date_trunc('minute',now()),1) ON CONFLICT(caller,minute)
        DO UPDATE SET count=commerce_rate_limits.count+1 RETURNING count""",
        caller,
    )
    if n > 180:
        raise HTTPException(429, "Please retry in a minute.", headers={"Retry-After": "60"})


async def movement(conn, product_id, quantity, kind, actor, note, order_id=None, po_id=None):
    # All callers hold the product lock; conditional update is defence in depth.
    await conn.execute(
        """INSERT INTO inventory_opening_balances(product_id,quantity)
        SELECT product_id,stock_on_hand-coalesce((SELECT sum(quantity) FROM inventory_movements WHERE product_id=$1),0)
        FROM products WHERE product_id=$1 AND stock_on_hand IS NOT NULL ON CONFLICT DO NOTHING""",
        product_id,
    )
    stock = await conn.fetchval(
        """UPDATE products SET stock_on_hand=stock_on_hand+$2,updated_at=now()
        WHERE product_id=$1 AND stock_on_hand IS NOT NULL AND stock_on_hand+$2>=0 RETURNING stock_on_hand""",
        product_id,
        quantity,
    )
    if stock is None:
        raise HTTPException(409, f"{product_id}: stock is unknown or insufficient.")
    await conn.execute(
        """INSERT INTO inventory_movements(movement_id,product_id,type,quantity,order_id,po_id,note,actor)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
        uuid4(),
        product_id,
        kind,
        quantity,
        order_id,
        po_id,
        note,
        actor,
    )
    return stock


async def ledger(
    conn,
    kind,
    amount,
    product,
    quantity,
    memo,
    *,
    order_id=None,
    po_id=None,
    channel=None,
    unit_cost=None,
    account="merchandise",
):
    await conn.execute(
        """INSERT INTO ledger_entries(entry_id,type,amount,cogs,units,product_id,category,channel,order_id,po_id,memo,account)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        uuid4(),
        kind,
        amount,
        (unit_cost if unit_cost is not None else product["unit_cost"]) * quantity,
        quantity,
        product["product_id"],
        product["category"],
        channel,
        order_id,
        po_id,
        memo,
        account,
    )
