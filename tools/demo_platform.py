"""Isolated public test-platform profile and authenticated receipt inspection.

This is a ShelfIQ project test fixture, not a commercial-platform integration.
Never mount a merchant key here or expose receipt payloads without the token.
"""

import asyncio
import hashlib
import hmac
import json
import os
import ssl
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from app.ucp.signatures import jwk, verify
from app.ucp.schemas import validate

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
lock = asyncio.Lock()
RECEIPTS = Path("/srv/receipts")
PLATFORM_KEY = "/srv/platform.pem"


async def merchant():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    async with httpx.AsyncClient(
        verify=context, timeout=20, follow_redirects=False
    ) as client:
        response = await client.get(
            os.environ["DEMO_MERCHANT_URL"] + "/.well-known/ucp"
        )
        response.raise_for_status()
        return response.json()


@app.get("/health")
async def health():
    return {"status": "ok", "purpose": "synthetic platform fixture"}


@app.get("/.well-known/ucp")
async def profile():
    business = await merchant()
    caps = business["ucp"]["capabilities"]
    caps["dev.ucp.shopping.order"][0]["config"] = {
        "webhook_url": os.environ["DEMO_PLATFORM_URL"] + "/orders"
    }
    result = {
        "ucp": {
            "version": business["ucp"]["version"],
            "services": {},
            "capabilities": caps,
            "payment_handlers": business["ucp"]["payment_handlers"],
        },
        "keys": [jwk(PLATFORM_KEY, "shelfiq-platform")],
    }
    validate("ucp.json#/$defs/platform_schema", result["ucp"])
    return result


@app.post("/orders")
async def receive(request: Request):
    raw = await request.body()
    if len(raw) > 1000000:
        raise HTTPException(413, "Notification too large.")
    business = await merchant()
    values = {
        "@method": "POST",
        "@target-uri": os.environ["DEMO_PLATFORM_URL"] + "/orders",
        "webhook-id": request.headers.get("webhook-id", ""),
        "webhook-timestamp": request.headers.get("webhook-timestamp", ""),
    }
    verify(raw, values, request.headers, business["keys"])
    try:
        event_id = UUID(values["webhook-id"])
        payload = json.loads(raw)
        validate("shopping/order.json", payload)
    except (ValueError, TypeError):
        raise HTTPException(422, "A valid event ID and standard order are required.")
    digest = hashlib.sha256(raw).hexdigest()
    target = RECEIPTS / (str(event_id) + ".json")
    async with lock:
        old = json.loads(target.read_text()) if target.exists() else None
        if old and old["sha256"] != digest:
            raise HTTPException(409, "Event ID reused for a different payload.")
        if not old:
            temporary = target.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "event_id": str(event_id),
                        "sha256": digest,
                        "order_id": payload["id"],
                    }
                )
            )
            temporary.replace(target)
    return {"received": True, "duplicate": bool(old)}


@app.get("/receipts/{event_id}")
async def receipt(event_id: UUID, request: Request):
    expected = "Bearer " + os.environ["DEMO_RECEIVER_TOKEN"]
    if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
        raise HTTPException(401, "Receiver inspection token required.")
    target = RECEIPTS / (str(event_id) + ".json")
    if not target.exists():
        raise HTTPException(404, "Receipt not yet delivered.")
    return json.loads(target.read_text())
