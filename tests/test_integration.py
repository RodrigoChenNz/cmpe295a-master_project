import asyncio
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from app import ingest, decisions, outbox, bus
from retail.contracts import dumps
from retail.device import render_lines


def proposal(**changes):
    now = datetime.now(timezone.utc)
    return (
        dict(
            product_id="SKU-001",
            decision_id=str(uuid4()),
            current_price=10.4,
            proposed_price=11.4,
            currency="USD",
            reason_tag="Inventory high",
            agent_version="test",
            ts=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
        | changes
    )


async def submit(p):
    assert await ingest.handle("retail/proposal/SKU-001", dumps(p))


async def test_redelivery_does_not_supersede_itself(database):
    p = proposal()
    await submit(p)
    await submit(p)
    assert (
        await database.fetchval(
            "SELECT status FROM proposals WHERE decision_id=$1", p["decision_id"]
        )
        == "pending"
    )
    assert (
        await database.fetchval(
            "SELECT count(*) FROM agent_audit_log WHERE event_type='proposal_received'"
        )
        == 1
    )


async def test_expired_and_stale_reference_cannot_publish(database):
    now = datetime.now(timezone.utc)
    old = proposal(
        ts=(now - timedelta(minutes=10)).isoformat(),
        expires_at=(now - timedelta(minutes=1)).isoformat(),
    )
    await submit(old)
    assert (
        await database.fetchval(
            "SELECT status FROM proposals WHERE decision_id=$1", old["decision_id"]
        )
        == "expired"
    )
    p = proposal(current_price=9.9, proposed_price=10)
    await submit(p)
    with pytest.raises(HTTPException):
        await decisions.decide(p["decision_id"], actor="manager")
    assert await database.fetchval("SELECT count(*) FROM price_history") == 1


async def test_explanation_before_proposal_is_persisted(database):
    p = proposal()
    e = dict(
        product_id="SKU-001",
        decision_id=p["decision_id"],
        base_value=10.4,
        shap=[{"feature": "inventory_level", "value": 1}],
        ts=p["ts"],
    )
    assert await ingest.handle("retail/explain/SKU-001", dumps(e))
    await submit(p)
    assert await database.fetchval("SELECT count(*) FROM shap_vectors") == 1
    assert await database.fetchval("SELECT count(*) FROM pending_explanations") == 0


async def test_nonadditive_and_cross_product_explanations_rejected(database):
    p = proposal()
    await submit(p)
    e = dict(
        product_id="SKU-001",
        decision_id=p["decision_id"],
        base_value=1,
        shap=[{"feature": "inventory_level", "value": 1}],
        ts=p["ts"],
    )
    assert not await ingest.handle("retail/explain/SKU-001", dumps(e))
    assert await database.fetchval("SELECT count(*) FROM shap_vectors") == 0


async def test_concurrent_approvals_have_one_price_and_durable_job(database):
    p = proposal()
    await submit(p)
    results = await asyncio.gather(
        decisions.decide(p["decision_id"], actor="manager-a"),
        decisions.decide(p["decision_id"], actor="manager-b"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, HTTPException) for r in results) == 1
    assert (
        await database.fetchval(
            "SELECT count(*) FROM price_history WHERE decision_id=$1", p["decision_id"]
        )
        == 1
    )
    assert (
        await database.fetchval(
            "SELECT count(*) FROM message_outbox WHERE topic='retail/price/SKU-001'"
        )
        == 1
    )
    assert (
        await database.fetchval(
            "SELECT count(*) FROM message_outbox WHERE sent_at IS NULL"
        )
        >= 1
    )


async def test_publication_failure_rolls_back_entire_decision(database, monkeypatch):
    p = proposal()
    await submit(p)

    async def fail(*args, **kwargs):
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(outbox, "enqueue", fail)
    with pytest.raises(RuntimeError):
        await decisions.decide(p["decision_id"], actor="manager")
    assert (
        await database.fetchval(
            "SELECT status FROM proposals WHERE decision_id=$1", p["decision_id"]
        )
        == "pending"
    )
    assert await database.fetchval("SELECT count(*) FROM price_history") == 1


async def test_ack_requires_exact_current_publication(database):
    p = proposal()
    await submit(p)
    result = await decisions.decide(p["decision_id"], actor="manager")
    line1, line2 = render_lines("MILK", 11.4, "Inventory high")
    ack = dict(
        label_id="SHLF-001",
        product_id="SKU-001",
        event="rendered",
        publication_id=result["history_id"],
        price=11.4,
        line1=line1,
        line2=line2,
        rendered_at=datetime.now(timezone.utc).isoformat(),
        ts=datetime.now(timezone.utc).isoformat(),
        firmware="test",
        emulated=True,
    )
    assert not await ingest.handle(
        "retail/shelf/SHLF-001", dumps(ack | {"price": 9.99})
    )
    assert await ingest.handle("retail/shelf/SHLF-001", dumps(ack))
    assert (
        await database.fetchval(
            "SELECT result FROM label_pushes WHERE publication_id=$1",
            result["history_id"],
        )
        == "confirmed"
    )
    assert (
        await database.fetchval(
            "SELECT status FROM shelf_labels WHERE label_id='SHLF-001'"
        )
        == "online"
    )


async def test_outbox_retry_preserves_jobs(database, monkeypatch):
    p = proposal()
    await submit(p)
    await decisions.decide(p["decision_id"], actor="manager")
    monkeypatch.setitem(bus._broker_state, "connected", True)

    async def fail(*args, **kwargs):
        return False

    monkeypatch.setattr(bus, "send_now", fail)
    assert await outbox.drain() == 0
    assert (
        await database.fetchval(
            "SELECT count(*) FROM message_outbox WHERE sent_at IS NULL"
        )
        == 2
    )
    sent = []

    async def success(topic, payload, **kwargs):
        sent.append(topic)
        return True

    monkeypatch.setattr(bus, "send_now", success)
    assert await outbox.drain() == 2
    assert sent[0] == "retail/price/SKU-001"
    assert (
        await database.fetchval(
            "SELECT count(*) FROM message_outbox WHERE sent_at IS NULL"
        )
        == 0
    )


async def test_schema_rejects_malformed_money_and_identity(database):
    assert not await ingest.handle(
        "retail/proposal/SKU-001", dumps(proposal(proposed_price=11.123))
    )
    assert not await ingest.handle("retail/proposal/SKU-002", dumps(proposal()))
    assert await database.fetchval("SELECT count(*) FROM proposals") == 0


async def test_http_auth_decision_and_rbac(database):
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/products")).status_code == 401
        response = await client.post(
            "/api/auth/setup",
            json=dict(
                username="tester",
                email="tester@example.com",
                full_name="Test Manager",
                password="test-password-123",
            ),
        )
        assert response.status_code == 201, response.text
        await client.post("/api/auth/signout")
        response = await client.post(
            "/api/auth/signin",
            json={"login": "tester@example.com", "password": "test-password-123"},
        )
        assert response.status_code == 200, response.text
        p = proposal()
        await submit(p)
        response = await client.post(
            f"/api/suggestions/{p['decision_id']}/approve", json={}
        )
        assert response.status_code == 200, response.text
        assert response.json()["published"] is False
        assert (
            await database.fetchval(
                "SELECT approved_by FROM price_history WHERE decision_id=$1",
                p["decision_id"],
            )
            == "tester"
        )
        await database.execute("UPDATE users SET role='viewer' WHERE username='tester'")
        response = await client.post(
            "/api/suggestions/" + p["decision_id"] + "/approve", json={}
        )
        assert response.status_code == 403


async def test_late_old_proposal_cannot_replace_newer_work(database):
    newest = proposal()
    await submit(newest)
    old = proposal(ts=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    await submit(old)
    assert (
        await database.fetchval(
            "SELECT status FROM proposals WHERE decision_id=$1", newest["decision_id"]
        )
        == "pending"
    )
    assert (
        await database.fetchval(
            "SELECT status FROM proposals WHERE decision_id=$1", old["decision_id"]
        )
        == "superseded"
    )


async def test_expiry_is_checked_during_approval(database):
    p = proposal()
    await submit(p)
    await database.execute(
        "UPDATE proposals SET expires_at=now()-interval '1 second' WHERE decision_id=$1",
        p["decision_id"],
    )
    with pytest.raises(HTTPException) as err:
        await decisions.decide(p["decision_id"], actor="manager")
    assert err.value.status_code == 409
    assert await database.fetchval("SELECT count(*) FROM price_history") == 1
    await ingest.expire_lapsed()
    assert (
        await database.fetchval(
            "SELECT status FROM proposals WHERE decision_id=$1", p["decision_id"]
        )
        == "expired"
    )


async def test_manual_override_and_rejection_keep_distinct_audit(database):
    p = proposal()
    await submit(p)
    r = await decisions.decide(
        p["decision_id"], actor="manager", price=Decimal("11.20")
    )
    assert r["status"] == "overridden"
    assert await database.fetchval(
        "SELECT proposed_price FROM proposals WHERE decision_id=$1", p["decision_id"]
    ) == Decimal("11.40")
    assert (
        await database.fetchval(
            "SELECT reason_tag FROM price_history WHERE decision_id=$1",
            p["decision_id"],
        )
        == "Manager override"
    )
    second = proposal(current_price=11.2, proposed_price=12.2)
    await submit(second)
    await decisions.decide(
        second["decision_id"], actor="manager", action="reject", reason="Keep stable"
    )
    assert (
        await database.fetchval(
            "SELECT count(*) FROM price_history WHERE decision_id=$1",
            second["decision_id"],
        )
        == 0
    )


async def test_queued_unsent_push_does_not_timeout(database):
    p = proposal()
    await submit(p)
    await decisions.decide(p["decision_id"], actor="manager")
    await database.execute(
        "UPDATE label_pushes SET published_at=now()-interval '1 hour'"
    )
    assert await ingest.settle_label_pushes() == 0
    assert (
        await database.fetchval("SELECT result FROM label_pushes LIMIT 1") == "pending"
    )


async def test_tls_store_input_records_and_queues_atomically(database, monkeypatch):
    from app.config import settings
    from app.routers.system import observation

    monkeypatch.setattr(settings, "mqtt_tls", True)
    payload = dict(
        product_id="SKU-001",
        state_id=str(uuid4()),
        ts=datetime.now(timezone.utc).isoformat(),
        unit_cost=8,
        current_price=10.4,
        competitor_price=10.5,
        units_on_hand=120,
        day_of_week=2,
        time_of_day_bucket=1,
        sales_history=[12.0] * 14,
        source="store",
    )
    result = await observation(payload, None)
    assert result["accepted"] is True
    assert await database.fetchval("SELECT count(*) FROM store_observations") == 1
    assert await database.fetchval("SELECT count(*) FROM message_outbox") == 1
    assert (
        await database.fetchval(
            "SELECT stock_on_hand FROM products WHERE product_id='SKU-001'"
        )
        == 120
    )


async def test_onboarding_upgrade_preserves_existing_store(database):
    from pathlib import Path

    schema = "upgrade_" + uuid4().hex
    async with database.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"CREATE SCHEMA {schema}")
            await conn.execute(f"SET LOCAL search_path TO {schema}")
            await conn.execute("""CREATE TABLE users(username TEXT PRIMARY KEY);
                CREATE TABLE products(product_id TEXT PRIMARY KEY,category TEXT);
                CREATE TABLE store_settings(id BOOLEAN PRIMARY KEY,
                    store_name TEXT NOT NULL DEFAULT 'SJSU Market');
                INSERT INTO products VALUES('SKU-001','Grocery');
                INSERT INTO store_settings VALUES(TRUE,'Existing store');""")
            await conn.execute(
                Path("infra/postgres/migrations/003-dev-onboarding.sql").read_text()
            )
            row = await conn.fetchrow("SELECT * FROM store_settings")
            assert row["store_name"] == "Existing store"
            assert row["onboarded_at"] is not None
            assert await conn.fetchval("SELECT category FROM products") == "Grocery"
            assert await conn.fetchval("SELECT name FROM store_categories") == "Grocery"
            await conn.execute(f"DROP SCHEMA {schema} CASCADE")
