"""Long-running MQTT workers. The API alone writes approvals and price history."""

import asyncio
import contextlib
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import aiomqtt
import asyncpg
import numpy as np

from .contracts import dumps, loads, validate
from .mqtt import options
from .device import DisplayStore
from .explain import explain_price
from .forecasting import inventory_estimate
from .pricing import PricingPolicy, observation

log = logging.getLogger("shelfiq.workers")


def now():
    return datetime.now(timezone.utc)


def touch(role):
    Path(f"/tmp/shelfiq-{role}.health").touch()


async def publish(client, family, p, *, retain=False, qos=1, key=None):
    validate(family, p)
    key = key or p.get("product_id") or p.get("label_id")
    await client.publish(f"retail/{family}/{key}", dumps(p), qos=qos, retain=retain)


async def simulator(client, pool):
    seed = int(os.getenv("SIM_SEED", "42"))
    rng = np.random.default_rng(seed)
    histories = {}
    tick = 0
    while True:
        rows = await pool.fetch("""SELECT p.product_id,p.unit_cost,p.stock_on_hand,ph.price
            FROM products p JOIN LATERAL(SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1) ph ON TRUE ORDER BY p.product_id""")
        for row in rows:
            cost = float(row["unit_cost"])
            if cost <= 0:
                continue
            sku = row["product_id"]
            history = histories.setdefault(
                sku, list(np.maximum(0, rng.normal(18, 3, 28)).round(0))
            )
            sold = max(
                0,
                int(
                    round(
                        18 * (1 + 0.2 * np.sin(tick * 2 * np.pi / 7)) + rng.normal(0, 2)
                    )
                ),
            )
            stock = max(
                0,
                (row["stock_on_hand"] if row["stock_on_hand"] is not None else 200)
                - sold,
            )
            if tick % 7 == 0:
                stock = 200
            history.append(sold)
            history = history[-90:]
            histories[sku] = history
            p = dict(
                product_id=sku,
                state_id=str(uuid4()),
                ts=now().isoformat(),
                unit_cost=cost,
                current_price=float(row["price"]),
                competitor_price=round(
                    float(row["price"]) * (1 + rng.uniform(-0.03, 0.03)), 2
                ),
                units_on_hand=stock,
                day_of_week=tick % 7,
                time_of_day_bucket=tick % 4,
                sales_history=[float(v) for v in history],
                source="simulator",
            )
            await publish(client, "state", p, retain=True)
        touch("simulator")
        tick += 1
        await asyncio.sleep(float(os.getenv("SIM_INTERVAL", "15")))


async def forecaster(client):
    await client.subscribe("retail/state/+", qos=1)
    method = os.getenv("FORECAST_METHOD", "seasonal")
    async for msg in client.messages:
        try:
            p = validate("state", loads(msg.payload))
            estimate = await asyncio.to_thread(
                inventory_estimate, p["sales_history"], p["units_on_hand"], method
            )
            out = dict(
                product_id=p["product_id"],
                state_id=p["state_id"],
                dead_stock_risk=estimate["risk"],
                forecast_daily=round(estimate["daily_demand"], 4),
                risk_kind="heuristic_score",
                predicted_sellby=(now() + timedelta(days=estimate["days_to_clear"]))
                .date()
                .isoformat(),
                units_on_hand=p["units_on_hand"],
                model_version=f"{method}-v1",
                ts=p["ts"],
            )
            await publish(client, "inventory", out, retain=True, qos=0)
            touch("forecaster")
        except Exception:
            log.exception("Forecast rejected")


async def pricing(client):
    algorithm = os.getenv("PRICING_POLICY", "rule")
    path = os.getenv("PRICING_MODEL")
    policy = PricingPolicy(algorithm, path)
    version = (
        algorithm
        + "-"
        + (hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12] if path else "v1")
    )
    states = {}
    forecasts = {}
    policies = {}
    processed = {}
    for topic in [
        "retail/state/+",
        "retail/inventory/+",
        "retail/guardrail/+",
        "retail/price/+",
        "retail/decision/+",
    ]:
        await client.subscribe(topic, qos=1)
    async for msg in client.messages:
        try:
            family = str(msg.topic).split("/")[1]
            p = validate(family, loads(msg.payload))
            sku = p["product_id"]
            if family == "state":
                if sku in states and p["ts"] <= states[sku]["ts"]:
                    continue
                states[sku] = p
            elif family == "inventory":
                forecasts[sku] = p
            elif family == "guardrail":
                policies[sku] = p
                continue
            else:
                # Approved prices and all decision outcomes are feedback, not new
                # training samples: realized demand belongs in offline training.
                touch("pricing")
                continue
            state = states.get(sku)
            fc = forecasts.get(sku)
            if (
                not state
                or not fc
                or fc.get("state_id") != state["state_id"]
                or processed.get(sku) == state["state_id"]
            ):
                continue
            age = (
                now() - datetime.fromisoformat(str(state["ts"]).replace("Z", "+00:00"))
            ).total_seconds()
            if not -5 <= age <= 120:
                continue
            cost = float(state["unit_cost"])
            limits = policies.get(sku, {})
            lo = float(limits.get("min_price", cost * 1.05))
            hi = float(limits.get("max_price", cost * 1.9))
            obs = observation(state, fc.get("forecast_daily"))
            amount = policy.price(obs, cost=cost, minimum=lo, maximum=hi)
            base, shap = await asyncio.to_thread(
                explain_price, policy, obs, cost=cost, minimum=lo, maximum=hi
            )
            identifier = str(uuid4())
            timestamp = now()
            top = shap[0]["feature"].replace("_", " ")
            proposal = dict(
                product_id=sku,
                decision_id=identifier,
                current_price=float(state["current_price"]),
                proposed_price=amount,
                currency="USD",
                reason_tag="Policy proposal",
                agent_version=version,
                ts=timestamp.isoformat(),
                expires_at=(
                    timestamp + timedelta(seconds=int(os.getenv("PROPOSAL_TTL", "300")))
                ).isoformat(),
                rationale=f"{algorithm.upper()} recommendation. Largest price contribution: {top}. Demand forecast uses {fc['model_version']}.",
            )
            explanation = dict(
                product_id=sku,
                decision_id=identifier,
                base_value=base,
                shap=shap,
                ts=timestamp.isoformat(),
                method="exact_interventional_single_reference_shapley",
                target="proposed_price_usd",
                reference=[0.5, 0.65, 0.65, 0.45, 0.5, 0.5],
            )
            await publish(client, "proposal", proposal)
            await publish(client, "explain", explanation)
            processed[sku] = state["state_id"]
            touch("pricing")
        except Exception:
            log.exception("Pricing input rejected")


async def device(client, pool):
    store = DisplayStore(os.getenv("DEVICE_STATE", "artifacts/device.sqlite3"))
    await client.subscribe("retail/price/+", qos=1)

    async def heartbeats():
        while True:
            rows = await pool.fetch(
                "SELECT label_id,product_id FROM shelf_labels WHERE product_id IS NOT NULL"
            )
            for row in rows:
                await publish(
                    client,
                    "shelf",
                    dict(
                        label_id=row["label_id"],
                        product_id=row["product_id"],
                        event="heartbeat",
                        ts=now().isoformat(),
                        firmware="emulator-1.0",
                        emulated=True,
                        rssi=-45,
                        battery=100,
                    ),
                    key=row["label_id"],
                )
            # Re-ack durable state after reconnect; the API verifies current binding,
            # publication and exact content before accepting any confirmation.
            for state in store.all():
                state["ts"] = now().isoformat()
                await publish(client, "shelf", state, key=state["label_id"])
            touch("device")
            await asyncio.sleep(10)

    task = asyncio.create_task(heartbeats())
    try:
        async for msg in client.messages:
            try:
                p = validate("price", loads(msg.payload))
                if "publication_id" not in p:
                    continue
                labels = await pool.fetch(
                    "SELECT label_id FROM shelf_labels WHERE product_id=$1",
                    p["product_id"],
                )
                for label in labels:
                    state = store.receive(label["label_id"], p)
                    await publish(client, "shelf", state, key=label["label_id"])
                touch("device")
            except Exception:
                log.exception("Device message rejected")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        store.conn.close()


async def run(role):
    logging.basicConfig(level=logging.INFO)
    pool = None
    if role in {"simulator", "device"}:
        pool = await asyncpg.create_pool(
            os.environ.get(
                "DATABASE_URL",
                "postgresql://retail:retail_dev_only@localhost:5432/retail_pricing",
            ),
            min_size=1,
            max_size=2,
        )
    try:
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=os.getenv("MQTT_HOST", "localhost"),
                    port=int(os.getenv("MQTT_PORT", "1883")),
                    identifier="shelfiq-" + role,
                    **options(),
                ) as client:
                    log.info("%s connected", role)
                    if role == "simulator":
                        await simulator(client, pool)
                    elif role == "device":
                        await device(client, pool)
                    elif role == "forecaster":
                        await forecaster(client)
                    elif role == "pricing":
                        await pricing(client)
                    else:
                        raise ValueError("Unknown worker")
            except (aiomqtt.MqttError, OSError):
                log.exception("%s disconnected; retrying", role)
                await asyncio.sleep(2)
    finally:
        if pool:
            await pool.close()
