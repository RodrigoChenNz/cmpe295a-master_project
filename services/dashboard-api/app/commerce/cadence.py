"""Optional daily shelf cutover. Approval is distinct from publication."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from .. import db, gate
from .common import audit


def next_cutover(setting, now=None):
    current = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(setting["timezone"]))
    local = datetime.combine(current.date(), setting["shelf_update_time"], current.tzinfo)
    if local <= current:
        local += timedelta(days=1)
    return local.astimezone(timezone.utc)


async def release():
    async with db.pool().acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(2952043)"):
            return
        # Same lock order as immediate approvals: product first.
        products = await conn.fetch(
            "SELECT product_id FROM products WHERE product_id IN (SELECT product_id FROM scheduled_price_publications WHERE status='queued' AND release_at<=now()) ORDER BY product_id FOR UPDATE"
        )
        for product in products:
            pid = product["product_id"]
            jobs = await conn.fetch(
                "SELECT * FROM scheduled_price_publications WHERE product_id=$1 AND status='queued' AND release_at<=now() ORDER BY approved_at DESC,decision_id FOR UPDATE",
                pid,
            )
            latest = jobs[0]
            current = await conn.fetchval(
                "SELECT price FROM price_history WHERE product_id=$1 ORDER BY id DESC LIMIT 1",
                pid,
            )
            p = latest["payload"]
            limits = await conn.fetchrow(
                "SELECT min_price,max_price FROM products WHERE product_id=$1", pid
            )
            value = Decimal(p["price"])
            valid = (
                current == latest["reference_price"]
                and (limits["min_price"] is None or value >= limits["min_price"])
                and (limits["max_price"] is None or value <= limits["max_price"])
            )
            for job in jobs:
                if job["decision_id"] == latest["decision_id"] and valid:
                    await gate.publish_price(
                        product_id=pid,
                        price=value,
                        reason_tag=p["reason_tag"],
                        approval=p["approval"],
                        approved_by=p["approved_by"],
                        decision_id=UUID(p["decision_id"]),
                        agent_version=p["agent_version"],
                        connection=conn,
                    )
                    await conn.execute(
                        "UPDATE scheduled_price_publications SET status='published',published_at=now() WHERE decision_id=$1",
                        job["decision_id"],
                    )
                    await audit(
                        conn, "scheduled_price_published", job["decision_id"], "scheduler", {}
                    )
                else:
                    await conn.execute(
                        "UPDATE scheduled_price_publications SET status='canceled',message=$2 WHERE decision_id=$1",
                        job["decision_id"],
                        "Newer approval selected at cutover."
                        if valid
                        else "Current price or guardrails changed; request a fresh recommendation.",
                    )
                    await audit(
                        conn, "scheduled_price_canceled", job["decision_id"], "scheduler", {}
                    )
