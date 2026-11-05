"""Public test-platform signatures, replay handling and receipt confidentiality."""

import json
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.ucp import signatures
from tools import demo_platform as fixture


@pytest.fixture
async def receiver(tmp_path, monkeypatch):
    key = tmp_path / "test.pem"
    key.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setattr(fixture, "RECEIPTS", tmp_path)
    monkeypatch.setattr(fixture, "PLATFORM_KEY", str(key))
    monkeypatch.setenv("DEMO_PLATFORM_URL", "https://platform.example")
    monkeypatch.setenv("DEMO_RECEIVER_TOKEN", "synthetic-receipt-inspection-secret")

    async def merchant():
        return {
            "keys": [signatures.jwk(str(key), "business")],
            "ucp": {
                "version": "2026-08-25",
                "payment_handlers": {},
                "capabilities": {
                    "dev.ucp.shopping.order": [
                        {
                            "version": "2026-08-25",
                            "spec": "https://ucp.dev/2026-08-25/specification/shopping/order",
                            "schema": "https://ucp.dev/2026-08-25/schemas/shopping/order.json",
                        }
                    ]
                },
            },
        }

    monkeypatch.setattr(fixture, "merchant", merchant)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app),
        base_url="https://platform.example",
    ) as client:
        yield client, key


def notification(key, event_id, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Webhook-Id": str(event_id), "Webhook-Timestamp": "synthetic"}
    headers.update(
        signatures.sign(
            raw,
            {
                "@method": "POST",
                "@target-uri": "https://platform.example/orders",
                "webhook-id": headers["Webhook-Id"],
                "webhook-timestamp": headers["Webhook-Timestamp"],
            },
            str(key),
            kid="business",
        )
    )
    return raw, headers


def order():
    return {
        "ucp": {
            "version": "2026-08-25",
            "capabilities": {"dev.ucp.shopping.order": [{"version": "2026-08-25"}]},
        },
        "id": str(uuid4()),
        "checkout_id": str(uuid4()),
        "permalink_url": "https://merchant.example/shop/orders/test",
        "currency": "USD",
        "line_items": [],
        "fulfillment": {"events": [], "expectations": []},
        "totals": [{"type": "subtotal", "amount": 0}, {"type": "total", "amount": 0}],
    }


async def test_signed_delivery_deduplicates_and_protects_receipts(receiver):
    client, key = receiver
    event_id = uuid4()
    payload = order()
    raw, headers = notification(key, event_id, payload)
    first = await client.post("/orders", content=raw, headers=headers)
    assert first.status_code == 200, first.text
    assert not first.json()["duplicate"]
    assert (await client.post("/orders", content=raw, headers=headers)).json()[
        "duplicate"
    ]
    assert (await client.get("/receipts/" + str(event_id))).status_code == 401
    receipt = await client.get(
        "/receipts/" + str(event_id),
        headers={"Authorization": "Bearer synthetic-receipt-inspection-secret"},
    )
    assert receipt.status_code == 200 and receipt.json()["order_id"] == payload["id"]
    changed, signed = notification(key, event_id, {**payload, "id": str(uuid4())})
    assert (
        await client.post("/orders", content=changed, headers=signed)
    ).status_code == 409


async def test_altered_unsigned_and_invalid_payloads_are_rejected(receiver, tmp_path):
    client, key = receiver
    raw, headers = notification(key, uuid4(), order())
    assert (
        await client.post("/orders", content=raw + b" ", headers=headers)
    ).status_code == 400
    assert (await client.post("/orders", content=raw)).status_code == 401
    raw, headers = notification(key, uuid4(), {"id": "not-an-order"})
    assert (
        await client.post("/orders", content=raw, headers=headers)
    ).status_code == 422
    assert not list(tmp_path.glob("*.json"))


async def test_platform_profile_publishes_public_key_and_callback(receiver):
    client, _ = receiver
    response = await client.get("/.well-known/ucp")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keys"][0]["kid"] == "shelfiq-platform"
    assert "PRIVATE KEY" not in response.text
    assert (
        body["ucp"]["capabilities"]["dev.ucp.shopping.order"][0]["config"][
            "webhook_url"
        ]
        == "https://platform.example/orders"
    )
