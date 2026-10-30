"""Real Mosquitto + PostgreSQL loop through all four software workers."""

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4
import aiomqtt
import pytest
from app import bus, outbox
from app.config import settings
from retail import workers
from retail.contracts import dumps, validate
from retail.learning import train


@pytest.mark.parametrize("algorithm", ["rule", "dqn", "ddpg"])
async def test_full_mqtt_loop_to_confirmed_emulated_shelf(
    database, monkeypatch, tmp_path, algorithm
):
    monkeypatch.setattr(settings, "mqtt_host", "localhost")
    monkeypatch.setattr(settings, "mqtt_port", 11883)
    monkeypatch.setattr(settings, "mqtt_enabled", True)
    monkeypatch.setattr(settings, "mqtt_client_id", "shelfiq-test-api")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MQTT_PORT", "11883")
    monkeypatch.setenv("DEVICE_STATE", str(tmp_path / "device.sqlite3"))
    monkeypatch.setenv("PRICING_POLICY", algorithm)
    if algorithm != "rule":
        path = tmp_path / (algorithm + ".zip")
        train(algorithm, 256, 7, str(path))
        monkeypatch.setenv("PRICING_MODEL", str(path))
    await database.execute(
        "UPDATE products SET auto_approve_pct=100 WHERE product_id='SKU-001'"
    )
    await database.execute("UPDATE change_limits SET max_change_pct=100")
    async with aiomqtt.Client("localhost", port=11883) as clean:
        for family in ["price", "state", "inventory", "guardrail"]:
            await clean.publish(f"retail/{family}/SKU-001", b"", retain=True, qos=1)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(bus.run(stop)), asyncio.create_task(outbox.run(stop))]
    tasks += [
        asyncio.create_task(workers.run(role))
        for role in ["device", "forecaster", "pricing"]
    ]
    try:
        async with aiomqtt.Client(
            "localhost", port=11883, protocol=aiomqtt.ProtocolVersion.V5
        ) as client:
            # Re-publish deterministic input during startup; consumers deduplicate
            # it and wait for the matching forecast state_id.
            p = dict(
                product_id="SKU-001",
                state_id=str(uuid4()),
                ts=datetime.now(timezone.utc).isoformat(),
                unit_cost=8,
                current_price=10.4,
                competitor_price=10.5,
                units_on_hand=180,
                day_of_week=2,
                time_of_day_bucket=1,
                sales_history=[18] * 28,
                source="simulator",
            )
            validate("state", p)
            for i in range(100):
                if i % 10 == 0:
                    await client.publish("retail/state/SKU-001", dumps(p), qos=1, retain=True)
                row = await database.fetchrow(
                    "SELECT * FROM shelf_labels WHERE label_id='SHLF-001'"
                )
                if row["rendered_at"] is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("No confirmed shelf rendering within ten seconds")
            assert row["emulated"] is True
            assert row["status"] == "online"
            assert row["rendered_line1"] == "MILK"
            assert await database.fetchval("SELECT count(*) FROM shap_vectors") == 1
            assert (
                await database.fetchval(
                    "SELECT count(*) FROM label_pushes WHERE result='confirmed'"
                )
                == 1
            )
            assert (
                await database.fetchval("SELECT count(*) FROM store_observations") == 1
            )
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bus._client = None
