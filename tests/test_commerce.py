"""Real PostgreSQL acceptance for commerce races, money, ownership and rollback."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app import auth
from app.commerce import agent
from app.main import app


@pytest.fixture
async def commerce(database):
    # Database fixture only destroys the explicitly named _test database.
    db = database
    await db.execute("""TRUNCATE commerce_requests,commerce_rate_limits,commerce_audit,decision_feedback,
        agent_runs,checkout_sessions,purchase_orders,market_observations RESTART IDENTITY CASCADE""")
    await db.execute("UPDATE store_settings SET commerce_enabled=true WHERE id")
    await db.execute("UPDATE products SET online=true WHERE product_id='SKU-001'")
    user = auth.CurrentUser("owner", "Owner", "manager", "owner@example.com")
    app.dependency_overrides[auth.current_user] = lambda: user
    app.dependency_overrides[agent.Actor.__metadata__[0].dependency] = lambda: "owner"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, db, user
    app.dependency_overrides.clear()


def headers(key=None, token="a" * 48):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key or str(uuid4())}


def cart(qty=2):
    return {
        "line_items": [{"item": {"id": "SKU-001"}, "quantity": qty}],
        "buyer": {"email": "buyer@example.com"},
        "fulfillment": {"method": "pickup"},
        "channel": "storefront",
    }


async def create(client, qty=2):
    r = await client.post(
        "/api/storefront/checkout-sessions", json=cart(qty), headers=headers()
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def complete(client, sid, key=None):
    return await client.post(
        f"/api/storefront/checkout-sessions/{sid}/complete",
        json={"payment_token": "test_approved"},
        headers=headers(key),
    )


async def test_checkout_ownership_idempotency_and_stock_race(commerce):
    client, db, _ = commerce
    key = str(uuid4())
    a = await client.post(
        "/api/storefront/checkout-sessions", json=cart(), headers=headers(key)
    )
    b = await client.post(
        "/api/storefront/checkout-sessions", json=cart(), headers=headers(key)
    )
    assert a.json() == b.json()
    assert (
        await client.post(
            "/api/storefront/checkout-sessions", json=cart(3), headers=headers(key)
        )
    ).status_code == 409
    sid = a.json()["id"]
    assert (
        await client.get(
            "/api/storefront/checkout-sessions/" + sid, headers=headers(token="b" * 48)
        )
    ).status_code == 404
    first = await complete(client, sid, key="complete-0001")
    assert first.status_code == 200, first.text
    retry = await complete(client, sid, key="complete-0001")
    assert retry.json() == first.json()
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 198
    )
    assert await db.fetchval("SELECT count(*) FROM ledger_entries") == 1
    oid = first.json()["order"]["id"]
    assert (
        await client.get("/api/storefront/orders/" + oid, headers=headers(token="b" * 48))
    ).status_code == 404
    public = (await client.get("/api/storefront/orders/" + oid, headers=headers())).json()
    assert "unit_cost_minor" not in public["line_items"][0]
    await db.execute("UPDATE products SET stock_on_hand=1 WHERE product_id='SKU-001'")
    sids = await asyncio.gather(create(client, 1), create(client, 1))
    results = await asyncio.gather(*(complete(client, s) for s in sids))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 0
    )


async def test_fulfillment_return_accounting_and_captured_cost(commerce):
    client, db, _ = commerce
    sid = await create(client, 4)
    sale = await complete(client, sid)
    oid = sale.json()["order"]["id"]
    await db.execute("UPDATE products SET unit_cost=9 WHERE product_id='SKU-001'")

    async def event(kind, n):
        return await client.post(
            "/api/orders/" + oid + "/events",
            json={"type": kind, "line_items": [{"id": "SKU-001", "quantity": n}]},
            headers=headers(),
        )

    assert (await event("shipped", 3)).status_code == 200
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 196
    )
    assert (await event("delivered", 4)).status_code == 409
    assert (await event("delivered", 3)).status_code == 200
    assert (await event("canceled", 2)).status_code == 409
    assert (await event("canceled", 1)).status_code == 200
    assert (await event("returned_to_sender", 2)).status_code == 200
    assert (await event("returned_to_sender", 2)).status_code == 409
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 199
    )
    report = (await client.get("/api/accounting/summary")).json()
    assert report["revenue"] == "10.40"
    assert report["cogs"] == "8.00"
    assert report["gross_profit"] == "2.40"
    assert report["refunds"] == "31.20"
    assert report["units"] == 1


async def test_payment_expiry_and_closed_store(commerce):
    client, db, _ = commerce
    sid = await create(client)
    response = await client.post(
        "/api/storefront/checkout-sessions/" + sid + "/complete",
        json={"payment_token": "test_declined"},
        headers=headers(),
    )
    assert response.status_code == 402
    assert await db.fetchval("SELECT count(*) FROM orders") == 0
    await db.execute(
        "UPDATE checkout_sessions SET expires_at=now()-interval '1 second' WHERE checkout_id=$1",
        sid,
    )
    assert (await complete(client, sid)).status_code == 409
    await db.execute("UPDATE store_settings SET commerce_enabled=false WHERE id")
    assert (await client.post("/api/storefront/catalog/search", json={})).status_code == 503


async def test_purchase_receipts_are_atomic_and_idempotent(commerce):
    client, db, _ = commerce
    supplier = await client.post("/api/suppliers", json={"name": "Synthetic warehouse"})
    assert supplier.status_code == 200, supplier.text
    supplier_id = supplier.json()["supplier_id"]
    await db.execute(
        "INSERT INTO supplier_items(supplier_id,product_id,title,unit_cost) VALUES($1,'SKU-001','Milk',9)",
        supplier_id,
    )
    await db.execute(
        "UPDATE products SET supplier_id=$1 WHERE product_id='SKU-001'", supplier_id
    )
    purchase = await client.post(
        "/api/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "lines": [{"product_id": "SKU-001", "quantity": 5, "unit_cost": "9.00"}],
        },
        headers=headers(),
    )
    assert purchase.status_code == 200, purchase.text
    po = purchase.json()["po_id"]
    assert (
        await client.post(
            "/api/purchase-orders/" + po + "/receive",
            json={"lines": [{"product_id": "SKU-001", "quantity_received": 1}]},
            headers=headers(),
        )
    ).status_code == 409
    ordered = await client.post(
        "/api/purchase-orders/" + po + "/order", headers=headers()
    )
    assert ordered.status_code == 200, ordered.text
    assert ordered.json()["supplier_order_id"].startswith("sim-")
    key = str(uuid4())
    body = {"lines": [{"product_id": "SKU-001", "quantity_received": 3}]}
    first = await client.post(
        "/api/purchase-orders/" + po + "/receive", json=body, headers=headers(key)
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "partially_received"
    assert (
        await client.post(
            "/api/purchase-orders/" + po + "/receive", json=body, headers=headers(key)
        )
    ).json() == first.json()
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 203
    )
    assert (
        await client.post(
            "/api/purchase-orders/" + po + "/receive", json=body, headers=headers()
        )
    ).status_code == 409
    body["lines"][0]["quantity_received"] = 2
    final = await client.post(
        "/api/purchase-orders/" + po + "/receive", json=body, headers=headers()
    )
    assert final.json()["status"] == "received"
    assert await db.fetchval(
        "SELECT sum(amount) FROM ledger_entries WHERE type='purchase'"
    ) == Decimal("45")


async def test_offer_gate_counter_accept_and_feedback(commerce):
    client, db, _ = commerce
    sid = await create(client, 5)
    offer = await client.post(
        "/api/storefront/checkout-sessions/" + sid + "/offers",
        json={
            "line_items": [
                {
                    "id": "SKU-001",
                    "quantity": 5,
                    "unit_price": {"amount": 950, "currency": "USD"},
                }
            ]
        },
        headers=headers(),
    )
    assert offer.status_code == 200, offer.text
    data = offer.json()
    assert data["status"] == "pending_review"
    assert (await complete(client, sid)).status_code == 409
    inbox = await client.get("/api/decisions?kind=deal")
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["total"] == 1
    did = data["decision_id"]
    verdict = await client.post(
        "/api/decisions/" + did + "/verdict",
        json={
            "action": "override",
            "changes": {
                "counter_line_items": [
                    {
                        "id": "SKU-001",
                        "quantity": 5,
                        "unit_price": {"amount": 1000, "currency": "USD"},
                    }
                ]
            },
            "reason": "Keep the margin floor.",
            "tags": ["margin_too_low"],
        },
    )
    assert verdict.status_code == 200, verdict.text
    accepted = await client.post(
        "/api/storefront/checkout-sessions/" + sid + "/offers/" + data["offer_id"] + "/accept",
        headers=headers(),
    )
    assert accepted.status_code == 200, accepted.text
    paid = await complete(client, sid)
    assert paid.status_code == 200, paid.text
    assert paid.json()["totals"][-1]["amount"] == 5000
    feedback = await client.get("/api/agent/feedback")
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["rows"][0]["verdict"]["tags"] == ["margin_too_low"]


async def test_commerce_observation_does_not_overwrite_inventory(commerce):
    _, db, _ = commerce
    from app.ingest import _state

    await _state(
        {
            "state_id": str(uuid4()),
            "product_id": "SKU-001",
            "ts": datetime.now(timezone.utc).isoformat(),
            "units_on_hand": 999,
            "source": "simulator",
        }
    )
    assert (
        await db.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 200
    )


async def test_roles_and_service_key_cannot_approve(commerce, monkeypatch):
    client, db, user = commerce
    from app.config import settings
    from app.commerce.decisions import manager_or_service

    app.dependency_overrides.pop(manager_or_service, None)
    monkeypatch.setattr(settings, "commerce_agent_key", "service-secret-" + "x" * 32)
    user.role = "viewer"
    assert (await client.put("/api/rules/autonomy", json={})).status_code == 403
    assert (
        await client.post(
            "/api/inventory/movements",
            json={
                "product_id": "SKU-001",
                "type": "adjustment",
                "quantity": 1,
                "note": "count",
            },
            headers=headers(),
        )
    ).status_code == 403
    assert (await client.get("/api/accounting/summary")).status_code == 403
    assert (
        await client.get("/api/agent/feedback", headers={"X-Service-Key": "wrong"})
    ).status_code == 401
    assert (
        await client.get(
            "/api/agent/feedback",
            headers={"X-Service-Key": settings.commerce_agent_key},
        )
    ).status_code == 200


async def test_listing_verdict_concurrency_and_restart_queue(commerce):
    client, db, _ = commerce
    from app.commerce.decisions import Submission, Explanation, submit

    async with db.pool().acquire() as conn, conn.transaction():
        decision = await submit(
            conn,
            Submission(
                kind="listing",
                proposal={"product_id": "SKU-001", "action": "delist", "fields": {}},
                explanation=Explanation(
                    summary="No available stock; delist for review."
                ),
            ),
        )
    did = str(decision["decision_id"])
    results = await asyncio.gather(
        *(
            client.post(
                "/api/decisions/" + did + "/verdict", json={"action": "approve"}
            )
            for _ in range(2)
        )
    )
    assert sorted(x.status_code for x in results) == [200, 409]
    assert not await db.fetchval(
        "SELECT online FROM products WHERE product_id='SKU-001'"
    )
    first = await agent.enqueue("test")
    second = await agent.enqueue("test")
    assert first["run_id"] == second["run_id"]
    # Persisted running step is resumable; the worker owns status changes.
    await db.execute(
        "UPDATE agent_runs SET status='running' WHERE run_id=$1", first["run_id"]
    )
    assert (await client.get("/api/agent/runs/" + first["run_id"])).json()[
        "status"
    ] == "running"


async def test_daily_cutover_keeps_approval_distinct_from_publication(commerce):
    from app import decisions
    from app.commerce.cadence import release

    client, db, _ = commerce
    await db.execute("UPDATE store_settings SET price_delivery_mode='daily' WHERE id")
    did = uuid4()
    await db.execute(
        """INSERT INTO proposals(decision_id,product_id,proposed_price,current_price,reason_tag,agent_version,expires_at,ts)
        VALUES($1,'SKU-001',10.50,10.40,'Test cutover','test',now()+interval '1 hour',now())""",
        did,
    )
    result = await decisions.decide(did, actor="owner")
    assert result["delivery"] == "daily"
    assert await db.fetchval("SELECT count(*) FROM price_history") == 1
    assert (
        await db.fetchval(
            "SELECT status FROM scheduled_price_publications WHERE decision_id=$1", did
        )
        == "queued"
    )
    await db.execute(
        "UPDATE scheduled_price_publications SET release_at=now()-interval '1 second'"
    )
    await release()
    assert await db.fetchval("SELECT count(*) FROM price_history") == 2
    assert await db.fetchval(
        "SELECT price FROM price_history ORDER BY id DESC LIMIT 1"
    ) == Decimal("10.50")
    await release()
    assert await db.fetchval("SELECT count(*) FROM price_history") == 2


async def test_nightly_run_through_real_pricing_worker(commerce, monkeypatch):
    import os
    import aiomqtt
    from app import bus, outbox
    from app.config import settings
    from app.ingest import _state, _inventory
    from retail import workers

    client, db, _ = commerce
    monkeypatch.setattr(settings, "mqtt_host", "localhost")
    monkeypatch.setattr(settings, "mqtt_port", 11883)
    monkeypatch.setattr(settings, "mqtt_client_id", "commerce-test-api")
    monkeypatch.setattr(settings, "mqtt_enabled", True)
    monkeypatch.setenv("MQTT_PORT", "11883")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("PRICING_POLICY", "rule")
    state = {
        "state_id": str(uuid4()),
        "product_id": "SKU-001",
        "ts": datetime.now(timezone.utc).isoformat(),
        "unit_cost": 8,
        "current_price": 10.4,
        "competitor_price": 10.5,
        "units_on_hand": 200,
        "day_of_week": 1,
        "time_of_day_bucket": 1,
        "sales_history": [18] * 28,
        "source": "simulator",
    }
    supplier_id = uuid4()
    await db.execute(
        "INSERT INTO suppliers(supplier_id,name) VALUES($1,'Nightly supplier')",
        supplier_id,
    )
    await db.execute(
        "UPDATE products SET supplier_id=$1,stock_on_hand=20 WHERE product_id='SKU-001'",
        supplier_id,
    )
    await db.execute(
        "INSERT INTO supplier_items(supplier_id,product_id,title,unit_cost) VALUES($1,'SKU-001','Milk',8)",
        supplier_id,
    )
    await _state(state)
    await _inventory(
        {
            "product_id": "SKU-001",
            "ts": state["ts"],
            "dead_stock_risk": 0.2,
            "predicted_sellby": "2026-12-01",
            "units_on_hand": 200,
            "model_version": "seasonal-test",
        }
    )
    # Retained input from earlier tests must not contaminate this independent run.
    async with aiomqtt.Client("localhost", port=11883) as clean:
        for family in ["price", "state", "inventory", "guardrail"]:
            await clean.publish(f"retail/{family}/SKU-001", b"", qos=1, retain=True)
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(bus.run(stop)),
        asyncio.create_task(outbox.run(stop)),
        asyncio.create_task(workers.run("pricing")),
    ]
    try:
        run = await agent.enqueue("test")
        async with db.pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agent_runs WHERE run_id=$1", run["run_id"]
            )
            await agent.execute_run(conn, row)
        finished = await db.fetchrow(
            "SELECT * FROM agent_runs WHERE run_id=$1", run["run_id"]
        )
        assert finished["status"] == "succeeded", finished["summary"]
        assert [s["name"] for s in finished["steps"]] == agent.STEPS
        assert all(s["status"] == "succeeded" for s in finished["steps"])
        assert (
            await db.fetchval(
                "SELECT count(*) FROM commerce_decisions WHERE kind='purchase_order' AND status='pending'"
            )
            == 1
        )
        assert await db.fetchval("SELECT count(*) FROM agent_price_jobs") == 1
        assert await db.fetchval("SELECT count(*) FROM shap_vectors") == 1
        linked = (
            await client.get(
                "/api/decisions?kind=price&status=&run_id=" + run["run_id"]
            )
        ).json()
        assert linked["total"] == 1
        assert "feedback" in linked["rows"][0]["agent_version"]
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bus._client = None


async def test_tax_shipping_and_fractional_refunds_reconcile(commerce):
    client, db, _ = commerce
    await db.execute(
        "UPDATE store_settings SET tax_bps=825,shipping_minor=500 WHERE id"
    )
    body = cart(3)
    body["fulfillment"] = {
        "method": "shipping",
        "address": "Demo address, local test only",
    }
    created = await client.post(
        "/api/storefront/checkout-sessions", json=body, headers=headers()
    )
    sid = created.json()["id"]
    paid = await complete(client, sid)
    assert paid.status_code == 200, paid.text
    assert paid.json()["totals"][-1]["amount"] == 3877
    oid = paid.json()["order"]["id"]
    for _ in range(3):
        event = await client.post(
            "/api/orders/" + oid + "/events",
            json={"type": "canceled", "line_items": [{"id": "SKU-001", "quantity": 1}]},
            headers=headers(),
        )
        assert event.status_code == 200, event.text
    result = (await client.get("/api/accounting/summary")).json()
    assert result["revenue"] == "0.00"
    assert result["cogs"] == "0.00"
    assert result["tax_collected"] == "0.00"
    assert result["shipping_income"] == "5.00"
    assert await db.fetchval(
        "SELECT sum(amount) FROM ledger_entries WHERE account='tax_payable'"
    ) == Decimal(0)


async def test_autonomy_purchase_uses_real_supplier_quote_and_rollback(commerce):
    client, db, _ = commerce
    from app.commerce.decisions import Submission, Explanation, submit

    sid = uuid4()
    await db.execute(
        "INSERT INTO suppliers(supplier_id,name) VALUES($1,'Test supplier')", sid
    )
    await db.execute(
        "UPDATE products SET supplier_id=$1 WHERE product_id='SKU-001'", sid
    )
    await db.execute(
        "INSERT INTO supplier_items(supplier_id,product_id,title,unit_cost,available) VALUES($1,'SKU-001','Milk',8,10)",
        sid,
    )
    policy = {
        "purchase_order": {"mode": "auto", "auto_max_total": "100.00"},
        "deal": {
            "mode": "ask",
            "min_margin_pct": 20,
            "max_discount_pct": 10,
            "min_quantity": 5,
        },
        "listing": {"mode": "ask"},
    }
    assert (await client.put("/api/rules/autonomy", json=policy)).status_code == 200
    async with db.pool().acquire() as conn, conn.transaction():
        decision = await submit(
            conn,
            Submission(
                kind="purchase_order",
                proposal={
                    "supplier_id": str(sid),
                    "lines": [
                        {"product_id": "SKU-001", "quantity": 5, "unit_cost": "8.00"}
                    ],
                },
                explanation=Explanation(
                    summary="Replenish five units from the simulated supplier."
                ),
            ),
        )
    assert decision["status"] == "auto_approved"
    po = await db.fetchrow(
        "SELECT * FROM purchase_orders WHERE decision_id=$1", decision["decision_id"]
    )
    assert po["status"] == "ordered"
    assert (
        await db.fetchval(
            "SELECT available FROM supplier_items WHERE supplier_id=$1", sid
        )
        == 5
    )
    canceled = await client.post(
        "/api/purchase-orders/" + str(po["po_id"]) + "/cancel", headers=headers()
    )
    assert canceled.status_code == 200, canceled.text
    assert (
        await db.fetchval(
            "SELECT available FROM supplier_items WHERE supplier_id=$1", sid
        )
        == 10
    )
    key = str(uuid4())
    bad = await client.post(
        "/api/purchase-orders",
        json={
            "supplier_id": str(sid),
            "lines": [{"product_id": "SKU-001", "quantity": 1, "unit_cost": "1.00"}],
        },
        headers=headers(key),
    )
    assert bad.status_code == 409
    assert await db.fetchval("SELECT count(*) FROM purchase_orders") == 1


async def test_listing_price_cannot_bypass_guardrails(commerce):
    client, db, _ = commerce
    response = await client.put(
        "/api/commerce/products/SKU-001", json={"online": True, "online_price": "1.00"}
    )
    assert response.status_code == 409
    assert (
        await db.fetchval(
            "SELECT online_price FROM products WHERE product_id='SKU-001'"
        )
        is None
    )


async def test_feedback_damping_explanation_is_additive():
    import numpy as np
    from retail.pricing import PricingPolicy, FeedbackAdjustedPolicy
    from retail.explain import explain_price

    obs = np.array([0.6, 0.7, 0.65, 0.45, 0.5, 0.5])
    raw = PricingPolicy()
    shaped = FeedbackAdjustedPolicy(raw, 0.5)
    kwargs = {"cost": 8, "minimum": 8.4, "maximum": 15.2}
    amount = shaped.price(obs, **kwargs)
    base, shap = explain_price(shaped, obs, **kwargs)
    assert abs(base + sum(x["value"] for x in shap) - amount) < 0.00001
    assert abs(amount - 10.4) <= abs(raw.price(obs, **kwargs) - 10.4) + 0.01


async def test_signed_webhooks_tamper_replay_expiry_and_delivery(commerce, monkeypatch):
    import json, time
    from app.config import settings
    from app.commerce import webhooks

    client, db, _ = commerce
    monkeypatch.setattr(settings, "commerce_webhook_key", "local-test-key-" + "z" * 32)
    await db.execute("TRUNCATE commerce_webhook_outbox,commerce_webhook_receipts")
    sid = await create(client)
    paid = await complete(client, sid)
    assert paid.status_code == 200
    assert await webhooks.drain() == 1
    assert await db.fetchval("SELECT count(*) FROM commerce_webhook_receipts") == 1
    received = await db.fetchval(
        "SELECT payload FROM commerce_webhook_receipts LIMIT 1"
    )
    assert "unit_cost_minor" not in received["line_items"][0]
    event_id = uuid4()
    raw = json.dumps({"id": str(uuid4()), "line_items": []}).encode()
    url = "http://test/ucp/v1/webhooks/orders"
    signed = webhooks.signed_headers(url, raw, event_id, settings.commerce_webhook_key)
    first = await client.post("/ucp/v1/webhooks/orders", content=raw, headers=signed)
    assert first.status_code == 200, first.text
    replay = await client.post("/ucp/v1/webhooks/orders", content=raw, headers=signed)
    assert replay.json()["duplicate"] is True
    tampered = await client.post(
        "/ucp/v1/webhooks/orders", content=raw + b" ", headers=signed
    )
    assert tampered.status_code == 401
    expired = webhooks.signed_headers(
        url, raw, uuid4(), settings.commerce_webhook_key, int(time.time()) - 301
    )
    assert (
        await client.post("/ucp/v1/webhooks/orders", content=raw, headers=expired)
    ).status_code == 401
