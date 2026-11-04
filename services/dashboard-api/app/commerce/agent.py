"""Persisted, single-writer nightly orchestration; workers execute pricing jobs.

Steps commit independently and resume after a process restart. Synthetic market
observations retain their provenance. A failed dependency fails the run visibly.
"""

import asyncio
import contextlib
import logging
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from .. import bus, db
from .common import audit, wire
from .decisions import Actor, Explanation, Submission, submit
from .operations import Reader, listing

router = APIRouter(tags=["store-agent"])
log = logging.getLogger("shelfiq.commerce.agent")
STEPS = [
    "review_outcomes",
    "market_research",
    "forecast_sync",
    "pricing",
    "procurement",
    "listings",
    "accounting_close",
]


async def enqueue(actor, scheduled_date=None):
    async with db.pool().acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(2952040)")
        active = await conn.fetchval(
            "SELECT run_id FROM agent_runs WHERE status IN ('queued','running')"
        )
        if active:
            return wire(await conn.fetchrow("SELECT * FROM agent_runs WHERE run_id=$1", active))
        if scheduled_date:
            old = await conn.fetchrow(
                "SELECT * FROM agent_runs WHERE scheduled_date=$1", scheduled_date
            )
            if old:
                return wire(old)
        rid = uuid4()
        await conn.execute(
            "INSERT INTO agent_runs(run_id,status,requested_by,scheduled_date) VALUES($1,'queued',$2,$3)",
            rid,
            actor,
            scheduled_date,
        )
        await audit(conn, "agent_run_queued", rid, actor, {})
        return wire(await conn.fetchrow("SELECT * FROM agent_runs WHERE run_id=$1", rid))


@router.post("/api/agent/runs")
async def start(actor: Actor):
    result = await enqueue(actor)
    bus.emit("run")
    return result


@router.get("/api/agent/runs")
async def runs(
    user: Reader, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)
):
    return await listing("agent_runs", page, page_size)


@router.get("/api/agent/runs/{rid}")
async def run_detail(rid: UUID, user: Reader):
    row = await db.fetchrow("SELECT * FROM agent_runs WHERE run_id=$1", rid)
    if not row:
        raise HTTPException(404, "Agent run not found.")
    return wire(
        {
            **row,
            "decision_ids": [
                r["decision_id"]
                for r in await db.fetch(
                    """SELECT decision_id FROM commerce_decisions WHERE run_id=$1
        UNION ALL SELECT decision_id FROM agent_price_jobs WHERE run_id=$1""",
                    rid,
                )
            ],
        }
    )


async def review_outcomes(rid):
    # Report observed sales in the subsequent 24h, without claiming causality.
    async with db.pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch("""SELECT p.decision_id,p.product_id,p.decided_at,
            coalesce(sum(l.units),0) AS units,coalesce(sum(l.amount),0) AS revenue,coalesce(sum(l.cogs),0) AS cogs
            FROM proposals p LEFT JOIN ledger_entries l ON l.product_id=p.product_id
            AND l.created_at>=p.decided_at AND l.created_at<p.decided_at+interval '24 hours' AND l.type IN ('sale','refund')
            WHERE p.decided_at<now()-interval '24 hours' AND p.decided_at>now()-interval '7 days'
            AND p.status IN ('approved','auto_approved','overridden')
            AND NOT EXISTS(SELECT 1 FROM decision_feedback f WHERE f.decision_id=p.decision_id AND f.outcome IS NOT NULL)
            GROUP BY p.decision_id LIMIT 1000""")
        for row in rows:
            await conn.execute(
                """INSERT INTO decision_feedback(decision_id,outcome) VALUES($1,$2)
                ON CONFLICT(decision_id) DO UPDATE SET outcome=$2,updated_at=now()""",
                row["decision_id"],
                wire(
                    {
                        "metrics": {
                            "units": row["units"],
                            "revenue": row["revenue"],
                            "gross_profit": row["revenue"] - row["cogs"],
                        },
                        "measured_at": datetime.now(timezone.utc),
                        "window": "24 hours after approval; observational, not causal",
                    }
                ),
            )
    return f"Recorded 24-hour observed outcomes for {len(rows)} price decisions."


async def market_research(rid):
    async with db.pool().acquire() as conn, conn.transaction():
        # Source observation ids make retries reproducible instead of inventing web evidence.
        rows = await conn.fetch("""SELECT DISTINCT ON(product_id) product_id,payload,ts FROM store_observations
            WHERE ts>now()-interval '1 day' ORDER BY product_id,ts DESC""")
        for row in rows:
            payload = row["payload"]
            await conn.execute(
                """INSERT INTO market_observations(observation_id,product_id,source,competitor,price,observed_at)
                VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING""",
                UUID(payload["state_id"]),
                row["product_id"],
                "simulator" if payload["source"] == "simulator" else "manual",
                "Synthetic competitor"
                if payload["source"] == "simulator"
                else "Store observation",
                Decimal(str(payload["competitor_price"])),
                row["ts"],
            )
    return (
        f"Imported {len(rows)} recent competitor observations with explicit source provenance."
    )


async def forecast_sync(rid):
    count = await db.fetchval(
        "SELECT count(DISTINCT product_id) FROM inventory_risk WHERE received_at>now()-interval '1 day'"
    )
    if not count:
        raise RuntimeError(
            "No recent inventory forecasts; start the forecaster and observation source."
        )
    return f"{count} products have forecasts received within one day."


async def pricing(rid):
    async with db.pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch("""SELECT p.*,s.payload AS state,h.price AS current_price,
            f.dead_stock_risk
            FROM products p JOIN LATERAL(SELECT payload FROM store_observations WHERE product_id=p.product_id AND ts>now()-interval '1 day' ORDER BY ts DESC LIMIT 1) s ON true
            JOIN LATERAL(SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1) h ON true
            JOIN LATERAL(SELECT dead_stock_risk FROM inventory_risk WHERE product_id=p.product_id ORDER BY received_at DESC LIMIT 1) f ON true
            WHERE NOT p.agent_excluded AND p.archived_at IS NULL AND p.unit_cost>0""")
        for p in rows:
            feedback = await conn.fetch(
                """SELECT coalesce(f.tags,'[]'::jsonb) AS tags,p.status FROM proposals p LEFT JOIN decision_feedback f USING(decision_id)
                WHERE p.product_id=$1 AND p.status IN ('approved','overridden','rejected') ORDER BY p.decided_at DESC LIMIT 20""",
                p["product_id"],
            )
            # Fixed, inspectable feedback penalty damps aggressive proposals. This
            # is decision-time reward shaping, not an unreported checkpoint update.
            penalties = {
                "too_aggressive": 0.3,
                "bad_timing": 0.2,
                "margin_too_low": 0.3,
                "stock_risk": 0.2,
                "explanation_unclear": 0.1,
            }
            penalty = min(
                0.8,
                sum(
                    sum(penalties.get(tag, 0) for tag in r["tags"])
                    + (0.1 if r["status"] == "rejected" else 0)
                    for r in feedback
                )
                / max(1, len(feedback)),
            )
            state = {
                **p["state"],
                "current_price": float(p["current_price"]),
                "unit_cost": float(p["unit_cost"]),
                "units_on_hand": p["stock_on_hand"] or 0,
                "day_of_week": datetime.now(timezone.utc).weekday(),
                "time_of_day_bucket": datetime.now(timezone.utc).hour // 6,
            }
            await conn.execute(
                """INSERT INTO agent_price_jobs(job_id,run_id,product_id,decision_id,payload)
                VALUES($1,$2,$3,$4,$5) ON CONFLICT(run_id,product_id) DO NOTHING""",
                uuid4(),
                rid,
                p["product_id"],
                uuid4(),
                {
                    "state": state,
                    "minimum": float(p["min_price"] or p["unit_cost"] * Decimal("1.05")),
                    "maximum": float(p["max_price"] or p["unit_cost"] * Decimal("1.9")),
                    "feedback_penalty": penalty,
                },
            )
        count = await conn.fetchval(
            "SELECT count(*) FROM agent_price_jobs WHERE run_id=$1", rid
        )
    if not count:
        raise RuntimeError("No eligible products with recent observations, cost and forecast.")
    deadline = asyncio.get_running_loop().time() + 90
    while asyncio.get_running_loop().time() < deadline:
        pending = await db.fetchval(
            """SELECT count(*) FROM agent_price_jobs j LEFT JOIN proposals p USING(decision_id)
            LEFT JOIN shap_vectors s USING(decision_id) WHERE j.run_id=$1 AND (p.decision_id IS NULL OR s.decision_id IS NULL)""",
            rid,
        )
        if not pending:
            return f"{count} price proposals and matching explanations received from the configured pricing worker; feedback shaping is recorded in each job."
        await asyncio.sleep(1)
    raise RuntimeError(
        "Pricing jobs did not receive proposals and explanations within 90 seconds. Start the pricing worker and broker, then retry the run."
    )


async def procurement(rid):
    created = 0
    async with db.pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch("""SELECT p.*,s.lead_time_days,s.name AS supplier_name,si.unit_cost AS supplier_cost,o.payload AS state
            FROM products p JOIN suppliers s USING(supplier_id)
            JOIN supplier_items si ON si.supplier_id=p.supplier_id AND si.product_id=p.product_id
            JOIN LATERAL(SELECT payload FROM store_observations WHERE product_id=p.product_id ORDER BY ts DESC LIMIT 1) o ON true
            WHERE p.archived_at IS NULL AND p.stock_on_hand IS NOT NULL AND s.simulated ORDER BY p.product_id""")
        for p in rows:
            demand = statistics.mean(p["state"]["sales_history"][-7:])
            target = max(1, round(demand * (p["lead_time_days"] + 3)))
            due = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM purchase_orders WHERE supplier_id=$1 AND status IN ('draft','ordered','partially_received') AND lines @> $2::jsonb)",
                p["supplier_id"],
                [{"product_id": p["product_id"]}],
            )
            if p["stock_on_hand"] >= target or due:
                continue
            qty = target - p["stock_on_hand"]
            await submit(
                conn,
                Submission(
                    kind="purchase_order",
                    proposal={
                        "supplier_id": str(p["supplier_id"]),
                        "lines": [
                            {
                                "product_id": p["product_id"],
                                "quantity": qty,
                                "unit_cost": str(p["supplier_cost"]),
                            }
                        ],
                        "deliver_by": (
                            datetime.now(timezone.utc) + timedelta(days=p["lead_time_days"])
                        )
                        .date()
                        .isoformat(),
                    },
                    context={
                        "stock_on_hand": p["stock_on_hand"],
                        "daily_demand": round(demand, 2),
                    },
                    explanation=Explanation(
                        summary=f"Order {qty} units of {p['name']} from the simulated supplier to cover lead time plus three days of demand.",
                        factors=[
                            {"name": "lead_time_days", "value": p["lead_time_days"]},
                            {"name": "target_stock", "value": target},
                        ],
                    ),
                    run_id=rid,
                ),
            )
            created += 1
    return f"{created} replenishment recommendations; existing open purchases are excluded."


async def listings(rid):
    created = 0
    async with db.pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT * FROM products WHERE archived_at IS NULL AND stock_on_hand IS NOT NULL ORDER BY product_id"
        )
        for p in rows:
            online = p["stock_on_hand"] > 0
            if p["online"] == online:
                continue
            await submit(
                conn,
                Submission(
                    kind="listing",
                    proposal={
                        "product_id": p["product_id"],
                        "action": "list" if online else "delist",
                        "fields": {},
                    },
                    context={"stock_on_hand": p["stock_on_hand"]},
                    explanation=Explanation(
                        summary=f"{'List' if online else 'Delist'} {p['name']} because {'stock is available' if online else 'there is no sellable stock'}.",
                        factors=[{"name": "stock_on_hand", "value": p["stock_on_hand"]}],
                    ),
                    run_id=rid,
                ),
            )
            created += 1
    return f"{created} listing recommendations based on sellable stock."


async def accounting_close(rid):
    invalid = await db.fetchval("SELECT count(*) FROM products WHERE stock_on_hand<0")
    if invalid:
        raise RuntimeError("Negative inventory detected.")
    drift = await db.fetchval("""SELECT count(*) FROM inventory_opening_balances b JOIN products p USING(product_id)
        WHERE p.stock_on_hand<>b.quantity+coalesce((SELECT sum(quantity) FROM inventory_movements WHERE product_id=p.product_id),0)""")
    if drift:
        raise RuntimeError(
            "Inventory opening balances and movements do not reconcile to current stock."
        )
    sale_mismatch = await db.fetchval("""SELECT count(*) FROM orders o WHERE
        (SELECT coalesce(sum(-quantity),0) FROM inventory_movements WHERE order_id=o.order_id AND type='sale')
        <> (SELECT sum((x->>'quantity')::int) FROM jsonb_array_elements(o.payload->'line_items') x)""")
    if sale_mismatch:
        raise RuntimeError("An order does not reconcile to its sale movements.")
    return "Checked nonnegative inventory, opening balances, movement totals and order-to-sale movement reconciliation. Accounting remains event driven."


async def execute_run(conn, row):
    rid = row["run_id"]
    steps = row["steps"]
    await conn.execute(
        "UPDATE agent_runs SET status='running',started_at=coalesce(started_at,now()) WHERE run_id=$1",
        rid,
    )
    try:
        for name in STEPS:
            if any(x["name"] == name and x["status"] == "succeeded" for x in steps):
                continue
            steps = [x for x in steps if x["name"] != name]
            entry = {
                "name": name,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            steps.append(entry)
            await conn.execute("UPDATE agent_runs SET steps=$2 WHERE run_id=$1", rid, steps)
            bus.emit("run")
            entry["summary"] = await globals()[name](rid)
            entry["status"] = "succeeded"
            entry["finished_at"] = datetime.now(timezone.utc).isoformat()
            await conn.execute("UPDATE agent_runs SET steps=$2 WHERE run_id=$1", rid, steps)
        summary = "Completed all seven store-agent steps. Review the decisions inbox for recommendations that require owner approval; supplier purchases and payments are simulated."
        await conn.execute(
            "UPDATE agent_runs SET status='succeeded',summary=$2,finished_at=now() WHERE run_id=$1",
            rid,
            summary,
        )
    except asyncio.CancelledError:
        raise  # persisted running state resumes after restart
    except Exception as exc:
        log.exception("Agent run failed")
        if steps and steps[-1]["status"] == "running":
            steps[-1].update(
                status="failed",
                summary=str(exc),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        await conn.execute(
            "UPDATE agent_runs SET status='failed',steps=$2,summary=$3,finished_at=now() WHERE run_id=$1",
            rid,
            steps,
            str(exc),
        )
    bus.emit("run")
    bus.emit("decision")


async def expire():
    # Session-first lock order is shared with offer verdicts and buyer actions.
    async with db.pool().acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT offer_id,checkout_id,decision_id FROM offers WHERE status IN ('pending_review','countered') AND expires_at<=now()"
        )
        for row in rows:
            await conn.execute(
                "SELECT checkout_id FROM checkout_sessions WHERE checkout_id=$1 FOR UPDATE",
                row["checkout_id"],
            )
            await conn.execute(
                "UPDATE commerce_decisions SET status='expired' WHERE decision_id=$1 AND status='pending'",
                row["decision_id"],
            )
            await conn.execute(
                "UPDATE offers SET status='expired' WHERE offer_id=$1 AND status IN ('pending_review','countered')",
                row["offer_id"],
            )
        await conn.execute(
            "UPDATE commerce_decisions SET status='expired' WHERE kind<>'deal' AND status='pending' AND expires_at<=now()"
        )
        await conn.execute(
            "DELETE FROM commerce_rate_limits WHERE minute<now()-interval '1 day'"
        )


async def run(stop):
    while not stop.is_set():
        try:
            from .cadence import release

            await release()
            await expire()
            setting = await db.fetchrow("SELECT * FROM store_settings WHERE id")
            if setting and setting["nightly_enabled"]:
                local = datetime.now(timezone.utc).astimezone(ZoneInfo(setting["timezone"]))
                if local.time().replace(tzinfo=None) >= setting["nightly_run_time"]:
                    await enqueue("scheduler", local.date())
            async with db.pool().acquire() as conn:
                if await conn.fetchval("SELECT pg_try_advisory_lock(2952041)"):
                    try:
                        row = await conn.fetchrow(
                            "SELECT * FROM agent_runs WHERE status IN ('queued','running') ORDER BY created_at LIMIT 1"
                        )
                        if row:
                            await execute_run(conn, row)
                    finally:
                        await conn.execute("SELECT pg_advisory_unlock(2952041)")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Commerce worker tick failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)
