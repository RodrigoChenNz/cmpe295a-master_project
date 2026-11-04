"""Independent platform contract tests against vendored official UCP schemas."""

import json
import time
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import auth
from app.config import settings
from app.main import app
from app.ucp import profiles, signatures
from app.ucp.schemas import validate


def key(path):
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(path)


@pytest.fixture
async def platform(database, monkeypatch, tmp_path):
    monkeypatch.setattr(
        settings, "ucp_signing_key_file", key(tmp_path / "business.pem")
    )
    monkeypatch.setattr(
        settings, "ucp_platform_key_file", key(tmp_path / "platform.pem")
    )
    monkeypatch.setattr(settings, "ucp_base_url", "https://test")
    monkeypatch.setattr(settings, "ucp_public_mode", False)
    monkeypatch.setattr(settings, "ucp_schema_origin", "https://schemas.shelfiq.test")
    await database.execute("TRUNCATE checkout_sessions CASCADE")
    await database.execute("UPDATE store_settings SET commerce_enabled=true WHERE id")
    await database.execute("UPDATE products SET online=true WHERE product_id='SKU-001'")
    app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
        "owner", "Owner", "manager", "owner@example.test"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        yield client, database
    app.dependency_overrides.clear()


def headers(
    method,
    path,
    body=b"",
    *,
    signed=True,
    profile=None,
    created=None,
    key_id="shelfiq-platform",
):
    result = {
        "UCP-Agent": 'profile="'
        + (profile or settings.ucp_base_url + "/.well-known/ucp-platform")
        + '"',
        "Request-Id": str(uuid4()),
        "Idempotency-Key": str(uuid4()),
        "Authorization": "Bearer " + "buyer-secret-" + "x" * 32,
        "Content-Type": "application/json",
    }
    if signed:
        result.update(
            signatures.sign(
                body,
                {
                    "@method": method,
                    "@target-uri": "https://test" + path,
                    "ucp-agent": result["UCP-Agent"],
                    "request-id": result["Request-Id"],
                },
                settings.ucp_platform_key_file,
                kid=key_id,
                created=created,
            )
        )
    return result


async def call(client, method, path, body=None, **options):
    raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
    return await client.request(
        method, path, content=raw, headers=headers(method, path, raw, **options)
    )


def cart():
    return {
        "line_items": [{"item": {"id": "SKU-001"}, "quantity": 2}],
        "buyer": {"email": "buyer@example.test"},
        "fulfillment": {
            "methods": [
                {
                    "id": "pickup",
                    "type": "pickup",
                    "line_item_ids": ["SKU-001"],
                    "selected_destination_id": "store",
                    "groups": [
                        {
                            "id": "group-1",
                            "line_item_ids": ["SKU-001"],
                            "selected_option_id": "pickup",
                        }
                    ],
                }
            ]
        },
    }


def payment(token="test_approved"):
    return {
        "payment": {
            "instruments": [
                {
                    "id": "test-card",
                    "handler_id": "shelfiq-test",
                    "type": "test_token",
                    "selected": True,
                    "credential": {"type": "test_token", "token": token},
                }
            ]
        }
    }


async def test_signed_official_checkout_order_fulfillment_and_lookup(platform):
    client, db = platform
    discovery = await client.get("/.well-known/ucp")
    assert discovery.status_code == 200, discovery.text
    validate("ucp.json#/$defs/business_schema", discovery.json()["ucp"])
    profiles.validate_authorities(discovery.json())
    signatures.verify(
        discovery.content,
        {"@status": "200"},
        discovery.headers,
        discovery.json()["keys"],
    )
    lookup = await call(
        client, "POST", "/ucp/v1/catalog/lookup", {"ids": ["SKU-001", "SKU-404"]}
    )
    assert lookup.status_code == 200, lookup.text
    validate("shopping/catalog_lookup.json#/$defs/lookup_response", lookup.json())
    assert lookup.json()["products"][0]["variants"][0]["inputs"] == [
        {"id": "SKU-001", "match": "exact"}
    ]
    created = await call(client, "POST", "/ucp/v1/checkout-sessions", cart())
    assert created.status_code == 201, created.text
    data = created.json()
    validate("shopping/checkout.json", data)
    validate("shopping/fulfillment.json#/$defs/dev.ucp.shopping.checkout", data)
    assert data["status"] == "ready_for_complete"
    sid = data["id"]
    paid = await call(
        client, "POST", f"/ucp/v1/checkout-sessions/{sid}/complete", payment()
    )
    assert paid.status_code == 200, paid.text
    validate("shopping/checkout.json", paid.json())
    oid = paid.json()["order"]["id"]
    order = await call(client, "GET", "/ucp/v1/orders/" + oid)
    assert order.status_code == 200, order.text
    validate("shopping/order.json", order.json())
    assert order.json()["line_items"][0]["quantity"] == {
        "original": 2,
        "total": 2,
        "fulfilled": 0,
    }
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 198
    )
    assert "unit_cost_minor" not in json.dumps(order.json())


async def test_signature_tampering_stale_keys_and_errors(platform):
    client, _ = platform
    path = "/ucp/v1/catalog/search"
    raw = b"{}"
    h = headers("POST", path, raw)
    tampered = await client.post(path, content=b'{"query":"milk"}', headers=h)
    assert tampered.status_code == 400, tampered.text
    assert tampered.json()["ucp"]["status"] == "error"
    expired = await call(client, "POST", path, {}, created=int(time.time()) - 301)
    assert expired.status_code == 401
    unknown = await call(client, "POST", path, {}, key_id="wrong-key")
    assert unknown.status_code == 401
    assert (await client.post(path, json={})).status_code == 400


async def test_exact_version_negotiation_and_namespace_binding(platform, monkeypatch):
    client, _ = platform
    profile = profiles.platform_profile()
    for entries in profile["ucp"]["capabilities"].values():
        for entry in entries:
            entry["version"] = "2026-01-11"
    url = "https://platform.example/profile"
    monkeypatch.setattr(
        settings, "ucp_platform_profiles_json", json.dumps({url: profile})
    )
    response = await call(
        client, "POST", "/ucp/v1/catalog/search", {}, signed=False, profile=url
    )
    assert response.status_code == 200
    assert response.json()["messages"][0]["code"] == "capabilities_incompatible"
    profile = profiles.platform_profile()
    profile["ucp"]["capabilities"]["dev.ucp.shopping.checkout"][0]["schema"] = (
        "https://evil.example/schema.json"
    )
    monkeypatch.setattr(
        settings, "ucp_platform_profiles_json", json.dumps({url: profile})
    )
    response = await call(
        client, "POST", "/ucp/v1/catalog/search", {}, signed=False, profile=url
    )
    assert response.status_code == 422
    assert not profiles.authority("com.examplecorp.pay", "https://example.com/schema")
    assert not profiles.authority(
        "dev.ucp.shopping.checkout", "https://ucp.dev@evil.example/x"
    )
    assert not profiles.authority("localhost.pay", "https://localhost/schema")
    assert profiles.authority("com.example.pay", "https://example.com/schema")


async def test_public_profile_fetch_rejects_private_destinations(monkeypatch):
    monkeypatch.setattr(
        profiles.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        profiles.fetch_https("https://evil.example/private")
    assert error.value.status_code == 400
    with pytest.raises(HTTPException):
        profiles.fetch_https("http://example.com/profile")


async def test_fulfillment_selection_replace_decline_and_cancel(platform):
    client, db = platform
    data = cart()
    data.pop("buyer")
    data.pop("fulfillment")
    response = await call(client, "POST", "/ucp/v1/checkout-sessions", data)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "incomplete"
    sid = response.json()["id"]
    updated = await call(client, "PUT", "/ucp/v1/checkout-sessions/" + sid, cart())
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "ready_for_complete"
    declined = await call(
        client,
        "POST",
        f"/ucp/v1/checkout-sessions/{sid}/complete",
        payment("test_declined"),
    )
    assert declined.status_code == 402
    assert await db.fetchval("SELECT count(*) FROM orders") == 0
    canceled = await call(client, "POST", f"/ucp/v1/checkout-sessions/{sid}/cancel", {})
    assert canceled.json()["status"] == "canceled"
    assert (
        await call(
            client, "POST", f"/ucp/v1/checkout-sessions/{sid}/complete", payment()
        )
    ).status_code == 409


async def test_shipping_partial_fulfillment_and_refund_adjustment(platform):
    client, db = platform
    await db.execute(
        "UPDATE store_settings SET shipping_minor=499,tax_bps=1000 WHERE id"
    )
    body = cart()
    body["fulfillment"]["methods"] = [
        {
            "id": "shipping",
            "type": "shipping",
            "line_item_ids": ["SKU-001"],
            "selected_destination_id": "home",
            "destinations": [
                {
                    "id": "home",
                    "type": "shipping_address",
                    "street_address": "123 Test St",
                    "address_locality": "San Jose",
                    "address_country": "US",
                    "postal_code": "95112",
                }
            ],
            "groups": [
                {
                    "id": "group-1",
                    "line_item_ids": ["SKU-001"],
                    "selected_option_id": "standard",
                }
            ],
        }
    ]
    created = await call(client, "POST", "/ucp/v1/checkout-sessions", body)
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert created.json()["totals"][-1]["amount"] == 2787
    assert (
        created.json()["fulfillment"]["methods"][0]["groups"][0]["options"][0][
            "totals"
        ][0]["amount"]
        == 499
    )
    paid = await call(
        client, "POST", f"/ucp/v1/checkout-sessions/{sid}/complete", payment()
    )
    assert paid.status_code == 200, paid.text
    oid = paid.json()["order"]["id"]
    for kind in ("shipped", "returned_to_sender", "canceled"):
        result = await client.post(
            f"/api/orders/{oid}/events",
            json={"type": kind, "line_items": [{"id": "SKU-001", "quantity": 1}]},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert result.status_code == 200, result.text
    result = await call(client, "GET", "/ucp/v1/orders/" + oid)
    assert result.status_code == 200, result.text
    data = result.json()
    validate("shopping/order.json", data)
    assert data["line_items"][0]["quantity"] == {
        "original": 2,
        "total": 0,
        "fulfilled": 0,
    }
    assert len(data["adjustments"]) == 2
    assert all("refund USD 11.44" in x["description"] for x in data["adjustments"])
    assert await db.fetchval(
        "SELECT sum(amount) FROM ledger_entries WHERE order_id=$1 AND type='refund'",
        UUID(oid),
    ) == Decimal("-22.88")
    assert (
        data["totals"][-1]["amount"] == 2787
    )  # original charge; shipping nonrefundable
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 200
    )


async def test_sparse_filtered_catalogue_pagination_and_lookup(platform):
    client, db = platform
    await db.execute("""INSERT INTO products(product_id,name,display_name,unit_cost,min_price,max_price,stock_on_hand,online,online_price,category)
        SELECT 'SKU-'||lpad(n::text,4,'0'),'Product '||n,'ITEM',8,8.4,15.2,20,true,10.4,
        CASE WHEN n>1100 THEN 'target' ELSE 'other' END FROM generate_series(1,1103) n""")
    body = {
        "filters": {"categories": ["target"], "price": {"min": 1000, "max": 1100}},
        "context": {"currency": "USD"},
        "pagination": {"limit": 2},
    }
    result = await call(client, "POST", "/ucp/v1/catalog/search", body)
    assert result.status_code == 200, result.text
    assert [p["id"] for p in result.json()["products"]] == ["SKU-1101", "SKU-1102"]
    body["pagination"]["cursor"] = result.json()["pagination"]["cursor"]
    result = await call(client, "POST", "/ucp/v1/catalog/search", body)
    assert [p["id"] for p in result.json()["products"]] == ["SKU-1103"]
    assert not result.json()["pagination"]["has_next_page"]
    result = await call(
        client,
        "POST",
        "/ucp/v1/catalog/lookup",
        {"ids": ["SKU-1101"], "filters": {"price": {"max": 100}}},
    )
    assert result.status_code == 200 and not result.json()["products"]
    body["context"]["currency"] = "EUR"
    assert (
        await call(client, "POST", "/ucp/v1/catalog/search", body)
    ).status_code == 422


async def test_no_shared_handler_and_private_context_rejected(platform, monkeypatch):
    client, _ = platform
    profile = profiles.platform_profile()
    profile["ucp"]["payment_handlers"] = {}
    url = "https://platform.example/profile"
    monkeypatch.setattr(
        settings, "ucp_platform_profiles_json", json.dumps({url: profile})
    )
    response = await call(
        client, "POST", "/ucp/v1/checkout-sessions", cart(), profile=url
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "requires_escalation"
    sid = response.json()["id"]
    assert (
        await call(
            client,
            "POST",
            f"/ucp/v1/checkout-sessions/{sid}/complete",
            payment(),
            profile=url,
        )
    ).status_code == 422
    response = await client.post(
        "/api/storefront/checkout-sessions",
        json={
            "line_items": [{"item": {"id": "SKU-001"}, "quantity": 1}],
            "ucp_context": {"order_webhook_url": "https://evil.example/hook"},
        },
        headers={
            "Authorization": "Bearer " + "x" * 40,
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 422


async def test_platform_order_notifications_are_signed_and_retryable(
    platform, monkeypatch
):
    from app.commerce import webhooks
    from app.ucp import notifications

    client, db = platform
    await db.execute("TRUNCATE commerce_webhook_outbox,commerce_webhook_receipts")
    profile = profiles.platform_profile()
    profile["ucp"]["capabilities"]["dev.ucp.shopping.order"][0]["config"] = {
        "webhook_url": "https://platform.example/orders"
    }
    url = "https://platform.example/profile"
    monkeypatch.setattr(
        settings, "ucp_platform_profiles_json", json.dumps({url: profile})
    )
    created = await call(
        client, "POST", "/ucp/v1/checkout-sessions", cart(), profile=url
    )
    sid = created.json()["id"]
    paid = await call(
        client,
        "POST",
        f"/ucp/v1/checkout-sessions/{sid}/complete",
        payment(),
        profile=url,
    )
    assert paid.status_code == 200, paid.text
    delivered = []
    public_delivery = notifications.deliver_https

    def receiver(target, raw, headers):
        assert target == "https://platform.example/orders"
        signatures.verify(
            raw,
            {
                "@method": "POST",
                "@target-uri": target,
                "webhook-id": headers["Webhook-Id"],
                "webhook-timestamp": headers["Webhook-Timestamp"],
            },
            headers,
            profiles.business_profile()["keys"],
        )
        validate("shopping/order.json", json.loads(raw))
        delivered.append(headers["Webhook-Id"])

    def unavailable(*args):
        raise ValueError("Synthetic unavailable receiver")

    monkeypatch.setattr(notifications, "deliver_https", unavailable)
    assert await webhooks.drain() == 0
    job = await db.fetchrow("SELECT * FROM commerce_webhook_outbox")
    assert job["attempts"] == 1 and not job["delivered_at"]
    monkeypatch.setattr(notifications, "deliver_https", receiver)
    count = await webhooks.drain()
    assert count == 1, await db.fetchval(
        "SELECT last_error FROM commerce_webhook_outbox"
    )
    assert await webhooks.drain() == 0
    assert delivered == [str(job["event_id"])]
    monkeypatch.setattr(
        notifications.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="Private/special"):
        public_delivery("https://platform.example/orders", b"{}", {})


async def test_valid_protocol_requests_outside_store_limits_fail_cleanly(platform):
    client, _ = platform
    body = cart()
    body["line_items"][0]["quantity"] = 10001
    response = await call(client, "POST", "/ucp/v1/checkout-sessions", body)
    assert response.status_code == 422, response.text
    body["line_items"][0]["quantity"] = 1
    body["line_items"][0]["item"]["id"] = "other-store-id"
    assert (
        await call(client, "POST", "/ucp/v1/checkout-sessions", body)
    ).status_code == 422
    response = await call(
        client, "POST", "/ucp/v1/catalog/lookup", {"ids": [str(n) for n in range(101)]}
    )
    assert response.status_code == 422, response.text
