"""Persisted order notifications with an RFC 9421 HMAC-SHA256 signature.

Local mode delivers through the ASGI HTTP adapter to the signed test receiver.
An optional deployment target is an operator-configured HTTPS URL. No buyer can
supply a callback URL; redirect following and profile URL fetching are disabled.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import ssl
import time
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request

from .. import bus, db
from ..config import settings

router = APIRouter(tags=["signed-order-notifications"])
log = logging.getLogger("shelfiq.webhooks")
LOCAL_URL = "http://shelfiq.local/ucp/v1/webhooks/orders"
COMPONENTS = '("@method" "@target-uri" "content-digest" "webhook-id" "webhook-timestamp")'


def signing_base(url, headers, params):
    return "\n".join(
        [
            '"@method": POST',
            '"@target-uri": ' + url,
            '"content-digest": ' + headers["Content-Digest"],
            '"webhook-id": ' + headers["Webhook-Id"],
            '"webhook-timestamp": ' + headers["Webhook-Timestamp"],
            '"@signature-params": ' + params,
        ]
    ).encode()


def signed_headers(url, body, event_id, key, created=None):
    created = int(time.time()) if created is None else created
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    params = COMPONENTS + f';created={created};keyid="shelfiq-order-hook";alg="hmac-sha256"'
    headers = {
        "Content-Type": "application/json",
        "Content-Digest": f"sha-256=:{digest}:",
        "Webhook-Id": str(event_id),
        "Webhook-Timestamp": str(created),
        "Signature-Input": "sig1=" + params,
    }
    signature = hmac.new(
        key.encode(), signing_base(url, headers, params), hashlib.sha256
    ).digest()
    headers["Signature"] = "sig1=:" + base64.b64encode(signature).decode() + ":"
    return headers


def verify(url, body, headers, key):
    normalized = {
        name: headers.get(name, "")
        for name in [
            "Content-Digest",
            "Webhook-Id",
            "Webhook-Timestamp",
            "Signature-Input",
            "Signature",
        ]
    }
    pattern = (
        r"^sig1="
        + re.escape(COMPONENTS)
        + r';created=([0-9]{1,12});keyid="shelfiq-order-hook";alg="hmac-sha256"$'
    )
    match = re.match(pattern, normalized["Signature-Input"])
    if (
        not match
        or abs(time.time() - int(match[1])) > 300
        or normalized["Webhook-Timestamp"] != match[1]
    ):
        raise HTTPException(401, "Missing, unsupported or expired webhook signature.")
    if not re.fullmatch(r"sig1=:[A-Za-z0-9+/]+={0,2}:", normalized["Signature"]):
        raise HTTPException(401, "Invalid signature encoding.")
    expected = signed_headers(url, body, normalized["Webhook-Id"], key, int(match[1]))
    if not hmac.compare_digest(
        normalized["Content-Digest"], expected["Content-Digest"]
    ) or not hmac.compare_digest(normalized["Signature"], expected["Signature"]):
        raise HTTPException(401, "Webhook signature verification failed.")
    try:
        return UUID(normalized["Webhook-Id"])
    except ValueError:
        raise HTTPException(400, "Webhook-Id must be a UUID.")


async def notify(conn, payload):
    from .checkout import buyer_order

    payload = buyer_order(payload)
    # No silent notification claim when no receiver key was configured.
    await conn.execute(
        "INSERT INTO commerce_webhook_outbox(event_id,payload) VALUES($1,$2)", uuid4(), payload
    )


@router.post("/ucp/v1/webhooks/orders")
async def receive(request: Request):
    key = settings.commerce_webhook_key
    if len(key) < 32:
        raise HTTPException(503, "The signed webhook receiver is not configured.")
    raw = await request.body()
    if len(raw) > 1000000:
        raise HTTPException(413, "Notification is too large.")
    event_id = verify(str(request.url), raw, request.headers, key)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Notification must be JSON.")
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("id"), str)
        or "line_items" not in payload
    ):
        raise HTTPException(422, "An order snapshot is required.")
    fingerprint = hashlib.sha256(raw).hexdigest()
    async with db.pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "webhook:" + str(event_id)
        )
        old = await conn.fetchval(
            "SELECT payload_hash FROM commerce_webhook_receipts WHERE event_id=$1", event_id
        )
        if old and old != fingerprint:
            raise HTTPException(409, "Webhook-Id was already used with a different payload.")
        if not old:
            await conn.execute(
                "INSERT INTO commerce_webhook_receipts(event_id,payload_hash,payload) VALUES($1,$2,$3)",
                event_id,
                fingerprint,
                payload,
            )
    bus.emit("order", id=payload["id"])
    return {"received": True, "duplicate": bool(old)}


async def drain():
    key = settings.commerce_webhook_key
    if len(key) < 32:
        return 0
    url = settings.commerce_webhook_url or LOCAL_URL
    if settings.commerce_webhook_url and (not url.startswith("https://") or "#" in url):
        raise ValueError("COMMERCE_WEBHOOK_URL must be an operator-configured HTTPS URL.")
    transport = None
    if not settings.commerce_webhook_url:
        from ..main import app

        transport = httpx.ASGITransport(app=app)
    count = 0
    # Session advisory lock avoids holding a SQL transaction over network IO;
    # receiver and sender can safely use separate database connections.
    async with db.pool().acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock(2952044)"):
            return 0
        try:
            jobs = await conn.fetch(
                "SELECT * FROM commerce_webhook_outbox WHERE delivered_at IS NULL ORDER BY created_at LIMIT 20"
            )
            tls = ssl.create_default_context()
            tls.minimum_version = ssl.TLSVersion.TLSv1_3
            async with httpx.AsyncClient(
                transport=transport, timeout=10, follow_redirects=False, verify=tls
            ) as client:
                for job in jobs:
                    raw = json.dumps(
                        job["payload"], sort_keys=True, separators=(",", ":")
                    ).encode()
                    try:
                        result = await client.post(
                            url,
                            content=raw,
                            headers=signed_headers(url, raw, job["event_id"], key),
                        )
                        result.raise_for_status()
                        await conn.execute(
                            "UPDATE commerce_webhook_outbox SET delivered_at=now(),attempts=attempts+1,last_error=NULL WHERE event_id=$1",
                            job["event_id"],
                        )
                        count += 1
                    except Exception as exc:
                        await conn.execute(
                            "UPDATE commerce_webhook_outbox SET attempts=attempts+1,last_error=$2 WHERE event_id=$1",
                            job["event_id"],
                            str(exc)[:1000],
                        )
        finally:
            await conn.execute("SELECT pg_advisory_unlock(2952044)")
    return count


async def run(stop):
    while not stop.is_set():
        try:
            await drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Order notification delivery failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)
