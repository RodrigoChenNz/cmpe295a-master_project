#!/usr/bin/env python3
"""Stand in for Layers 1-4 so the console has a live system to run against.

    make fake

Publishes on the real contract topics, with the real payload shapes, so that
when Parth's agent and Aruna's forecaster come online this tool is deleted and
nothing in dashboard-api changes:

    retail/proposal/{product_id}   a price recommendation
    retail/explain/{product_id}    its SHAP vector, a beat later
    retail/inventory/{product_id}  dead-stock risk

It also refreshes `shelf_labels.last_seen_at` **directly in Postgres**, which is
the one thing here that is not a contract message. That is deliberate and worth
being explicit about: there is no topic in `contracts/` on which a label reports
that it is alive, so label liveness cannot currently be real. Without this the
whole network ages to 'offline' fifteen minutes after `make up`, which is the
honest consequence of the gap rather than a bug in the console.

**The gap is worth closing properly.** A `retail/shelf/{label_id}` message
carrying `{label_id, decision_id, rendered_at, rssi, battery}` would give the
console a real third state — confirmed at shelf / sent, unconfirmed / shelf
offline — instead of a status it infers from silence. That needs all four
signatures, so it is a proposal in this docstring and not a file in contracts/.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

try:
    import aiomqtt
    import asyncpg
except ImportError:
    sys.exit(
        "fake_publisher needs aiomqtt and asyncpg.\n"
        "  uv run --with aiomqtt --with asyncpg tools/fake_publisher.py\n"
        "or, with no local Python at all:\n"
        "  make fake   (runs it inside the dashboard-api image)"
    )

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://retail:retail_dev_only@localhost:5432/retail_pricing"
)

AGENT_VERSION = "stub-0.1"
FORECASTER_VERSION = "prophet-stub-0.1"

PROPOSAL_EVERY = (20, 50)   # seconds between recommendations
HEARTBEAT_EVERY = 45        # seconds between label sightings
INVENTORY_EVERY = 120       # seconds between forecaster updates

# Reason tags, per category, because "Expires in 2 days" on a 4K television
# reads as a broken demo rather than a pricing agent. Every string here is <= 16
# characters, since line 2 of a 16x2 LCD is 16 cells and the cap is enforced at
# the publisher rather than patched in firmware — see the assertion below.
PERISHABLE = {"Dairy", "Bakery", "Produce", "Seafood"}

MARKDOWN_REASONS = {
    "perishable": ["Expiry 2 days", "Quality drop", "Counter closing", "Slow vs plan"],
    "seasonal": ["End of range", "Season ending", "Slow seller", "Match promo"],
    "general": ["Slow vs plan", "Match promo", "Overstocked", "Clearing stock"],
}
INCREASE_REASONS = {
    "perishable": ["Short supply", "Demand up 22%", "Competitor up"],
    "seasonal": ["Low stock left", "Demand up 18%", "Rival sold out"],
    "general": ["Competitor up", "Rival sold out", "Demand up 22%", "Stock is low"],
}

# A tag longer than sixteen characters would be cut by the firmware into a
# different, misleading sentence. Catching that here rather than on the shelf.
for _group in (*MARKDOWN_REASONS.values(), *INCREASE_REASONS.values()):
    for _tag in _group:
        assert len(_tag) <= 16, f"reason tag {_tag!r} is {len(_tag)} characters"


def reason_bucket(category: str | None) -> str:
    if category in PERISHABLE:
        return "perishable"
    if category == "Clothing":
        return "seasonal"
    return "general"


FEATURES = [
    "days_to_expiry", "units_on_hand", "sales_velocity", "competitor_price",
    "day_of_week", "margin_headroom",
]


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def shap_for(current: Decimal, proposed: Decimal, rng: random.Random) -> dict:
    """A SHAP vector whose contributions sum exactly to proposed - current.

    Additivity is the one property the explanation panel depends on, so it is
    enforced here instead of hoped for: the residual from rounding is folded
    into the largest driver rather than left to drift.
    """
    total = proposed - current
    n = rng.randint(3, 5)
    feats = rng.sample(FEATURES, n)
    weights = [Decimal(str(rng.uniform(0.2, 1.0))) for _ in range(n)]
    # One arm pushes the other way, so the diverging chart has both poles.
    weights[-1] = -weights[-1] * Decimal("0.4")
    scale = total / sum(weights) if sum(weights) != 0 else Decimal(0)
    parts = [(w * scale).quantize(Decimal("0.0001")) for w in weights]
    parts[0] += total - sum(parts)
    pairs = sorted(zip(feats, parts), key=lambda fp: abs(fp[1]), reverse=True)
    return {
        "base_value": float(current),
        "shap": [{"feature": f, "value": float(v)} for f, v in pairs],
    }


async def pick_product(pool: asyncpg.Pool) -> dict | None:
    """A SKU with a current price, no pending proposal, and not excluded.

    Skipping SKUs that already have something in the queue keeps the review
    queue from filling with twelve proposals for the same product, which is
    what `superseded` exists to prevent but is not interesting to look at.
    """
    return await pool.fetchrow(
        """SELECT p.product_id, p.name, p.category, p.unit_cost,
                  p.min_price, p.max_price, h.price AS current_price
             FROM products p
             JOIN LATERAL (
                  SELECT price FROM price_history
                   WHERE product_id = p.product_id ORDER BY ts DESC LIMIT 1
             ) h ON TRUE
            WHERE p.archived_at IS NULL
              AND NOT p.agent_excluded
              AND NOT EXISTS (SELECT 1 FROM proposals
                               WHERE product_id = p.product_id AND status = 'pending')
            ORDER BY random() LIMIT 1"""
    )


async def publish_proposal(
    client: aiomqtt.Client, pool: asyncpg.Pool, rng: random.Random, ttl_hours: int
) -> None:
    product = await pick_product(pool)
    if product is None:
        return

    current = money(product["current_price"])
    # Deliberately mixed: most moves are small enough to be delegated, some are
    # big enough to need a human, and a few land outside the band so the
    # out-of-band state in the queue is reachable without editing data.
    roll = rng.random()
    if roll < 0.45:
        pct = Decimal(str(rng.uniform(-4.5, -1.0)))
    elif roll < 0.70:
        pct = Decimal(str(rng.uniform(1.0, 4.5)))
    elif roll < 0.90:
        pct = Decimal(str(rng.uniform(-18.0, -6.0)))
    else:
        pct = Decimal(str(rng.uniform(6.0, 20.0)))

    proposed = money(current * (Decimal(1) + pct / Decimal(100)))
    if proposed <= 0:
        return
    # Never propose below cost. An agent that recommends selling at a loss has
    # not finished reasoning, and it would make the margin column meaningless.
    floor = money(product["unit_cost"] * Decimal("1.02"))
    proposed = max(proposed, floor)
    if proposed == current:
        return

    bucket = reason_bucket(product["category"])
    reasons = (MARKDOWN_REASONS if proposed < current else INCREASE_REASONS)[bucket]
    decision_id = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    proposal = {
        "product_id": product["product_id"],
        "proposed_price": float(proposed),
        "current_price": float(current),
        "currency": "USD",
        "decision_id": decision_id,
        "reason_tag": rng.choice(reasons),
        "agent_version": AGENT_VERSION,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ts": now_iso(),
        # Neither of these is in contracts/proposal.schema.json. They are sent
        # as optional extras that dashboard-api stores when present and reports
        # as "not reported" when absent — the shape of the addition the team
        # would have to agree before an agent relies on it.
        "confidence": round(rng.uniform(0.60, 0.95), 2),
    }
    await client.publish(
        f"retail/proposal/{product['product_id']}",
        json.dumps(proposal).encode(),
        qos=1,
    )
    print(f"proposal  {product['product_id']:<9} {current} -> {proposed}  "
          f"({proposal['reason_tag']})", flush=True)

    # The explanation lags its proposal, because it does in the real system and
    # the console has to handle the window where one exists without the other.
    await asyncio.sleep(rng.uniform(0.5, 3.0))

    # ...and sometimes never arrives. SHAP computation failing is a normal
    # state, not an error, and the console must say so rather than show a
    # stale explanation from a different decision.
    if rng.random() < 0.12:
        print(f"explain   {product['product_id']:<9} (dropped — SHAP failed)", flush=True)
        return

    vector = shap_for(current, proposed, rng)
    await client.publish(
        f"retail/explain/{product['product_id']}",
        json.dumps(
            {
                "product_id": product["product_id"],
                "decision_id": decision_id,
                "base_value": vector["base_value"],
                "shap": vector["shap"],
                "ts": now_iso(),
            }
        ).encode(),
        qos=1,
    )


async def publish_inventory(
    client: aiomqtt.Client, pool: asyncpg.Pool, rng: random.Random
) -> None:
    rows = await pool.fetch(
        """SELECT p.product_id, p.stock_on_hand,
                  (f.values->>'units_per_day')::numeric AS per_day
             FROM products p
             JOIN product_fields f ON f.product_id = p.product_id
            WHERE p.archived_at IS NULL
              AND p.stock_on_hand IS NOT NULL
              AND (f.values->>'units_per_day')::numeric > 0
            ORDER BY random() LIMIT 4"""
    )
    for r in rows:
        days = Decimal(r["stock_on_hand"]) / r["per_day"]
        risk = max(0.02, min(0.95, float(days) / 60))
        sellby = (datetime.now(timezone.utc) + timedelta(days=float(min(days, Decimal(180))))).date()
        await client.publish(
            f"retail/inventory/{r['product_id']}",
            json.dumps(
                {
                    "product_id": r["product_id"],
                    "dead_stock_risk": round(risk, 3),
                    "predicted_sellby": sellby.isoformat(),
                    "units_on_hand": r["stock_on_hand"],
                    "model_version": FORECASTER_VERSION,
                    "ts": now_iso(),
                }
            ).encode(),
            qos=0,
        )
    if rows:
        print(f"inventory {len(rows)} SKUs", flush=True)


async def heartbeat_labels(pool: asyncpg.Pool, rng: random.Random) -> None:
    """Pretend the gateway reported in. NOT a contract message — see the module
    docstring. This writes to Postgres because there is no topic to write to."""
    # Only labels with no real tag behind them. A label with a device_id
    # reports for itself over retail/shelf/ (draft contract), and faking its
    # sightings would mark pushes confirmed that never reached its glass.
    labels = await pool.fetch(
        """SELECT label_id, battery_pct FROM shelf_labels
            WHERE product_id IS NOT NULL AND device_id IS NULL"""
    )
    seen = 0
    for label in labels:
        # A few stay dark, so 'offline' and 'failed' remain reachable states and
        # the network card has something real to report.
        if rng.random() < 0.15:
            continue
        drained = label["battery_pct"] is not None and label["battery_pct"] <= 5
        if drained:
            continue
        await pool.execute(
            """UPDATE shelf_labels
                  SET last_seen_at = now(),
                      rssi_dbm = $2,
                      -- A pending push that is now confirmed; a failed one
                      -- stays failed until somebody resends it.
                      status = CASE WHEN status IN ('syncing', 'offline')
                                    THEN 'online' ELSE status END
                WHERE label_id = $1""",
            label["label_id"],
            rng.randint(-72, -46),
        )
        seen += 1

    # Settle the pushes those sightings confirm.
    settled = await pool.fetch(
        """UPDATE label_pushes SET result = 'confirmed', settled_at = now()
            WHERE result = 'pending'
              AND label_id IN (SELECT label_id FROM shelf_labels
                                WHERE device_id IS NULL
                                  AND last_seen_at > now() - interval '10 seconds')
        RETURNING label_id"""
    )
    print(f"heartbeat {seen}/{len(labels)} labels seen"
          + (f", {len(settled)} pushes confirmed" if settled else ""), flush=True)


async def main() -> None:
    rng = random.Random()
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=4)
    # This writes fake proposals and label sightings. Only ever do that to the
    # demo database, which the seed marks; never to one holding real data.
    if not await pool.fetchval("SELECT to_regclass('public.sample_data_marker') IS NOT NULL"):
        await pool.close()
        sys.exit(
            "fake_publisher: this database is not the demo seed (no sample_data_marker).\n"
            "Refusing to write emulated data into it. Use `make demo` instead of `make up`."
        )
    ttl =await pool.fetchval("SELECT proposal_ttl_hours FROM change_limits LIMIT 1") or 4

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    print(f"fake publisher -> mqtt://{MQTT_HOST}:{MQTT_PORT}  (ctrl-c to stop)", flush=True)

    async with aiomqtt.Client(
        hostname=MQTT_HOST, port=MQTT_PORT, identifier="fake-publisher"
    ) as client:
        # Heartbeat and inventory on their own cadences, so one slow publish
        # does not hold up the others.
        async def every(seconds: int, fn) -> None:
            while not stop.is_set():
                try:
                    await fn()
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    print(f"  ! {fn.__name__}: {exc}", flush=True)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=seconds)
                except asyncio.TimeoutError:
                    pass

        async def proposals() -> None:
            while not stop.is_set():
                try:
                    await publish_proposal(client, pool, rng, ttl)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! proposal: {exc}", flush=True)
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=rng.uniform(*PROPOSAL_EVERY)
                    )
                except asyncio.TimeoutError:
                    pass

        await heartbeat_labels(pool, rng)
        await asyncio.gather(
            proposals(),
            every(HEARTBEAT_EVERY, lambda: heartbeat_labels(pool, rng)),
            every(INVENTORY_EVERY, lambda: publish_inventory(client, pool, rng)),
        )

    await pool.close()
    print("stopped", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
