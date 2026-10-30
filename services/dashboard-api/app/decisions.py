"""Single transaction and lock order for human and delegated decisions."""

from datetime import datetime, timezone
from fastapi import HTTPException
from . import db, gate, outbox, bus


async def outcome(conn, row, status, actor, price=None):
    await outbox.enqueue(
        conn,
        f"retail/decision/{row['decision_id']}",
        dict(
            decision_id=str(row["decision_id"]),
            product_id=row["product_id"],
            status=status,
            final_price=float(price) if price is not None else None,
            decided_by=actor,
            ts=datetime.now(timezone.utc).isoformat(),
        ),
    )


async def decide(
    decision_id, *, actor, action="approve", price=None, reason=None, automatic=False
):
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            product_id = await conn.fetchval(
                "SELECT product_id FROM proposals WHERE decision_id=$1", decision_id
            )
            if product_id is None:
                raise HTTPException(404, "No such suggestion.")
            product = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id=$1 FOR UPDATE", product_id
            )
            row = await conn.fetchrow(
                "SELECT * FROM proposals WHERE decision_id=$1 FOR UPDATE", decision_id
            )
            if row["status"] != "pending":
                raise HTTPException(409, f"Already {row['status']}.")
            if row["expires_at"] <= datetime.now(timezone.utc):
                # No writes followed by a raised exception: leave expiry recording to
                # the housekeeping task; this request must never execute stale work.
                raise HTTPException(409, "This suggestion has expired.")
            final = row["proposed_price"] if price is None else price
            current = await conn.fetchval(
                "SELECT price FROM price_history WHERE product_id=$1 ORDER BY id DESC LIMIT 1",
                product_id,
            )
            if action != "reject":
                if current is None or current != row["current_price"]:
                    raise HTTPException(
                        409, "The approved price changed; request a fresh recommendation."
                    )
                verdict = gate.evaluate(
                    proposed_price=final,
                    current_price=current,
                    min_price=product["min_price"],
                    max_price=product["max_price"],
                    auto_approve_pct=product["auto_approve_pct"],
                    agent_excluded=product["agent_excluded"],
                )
                if automatic and verdict.needs_human:
                    return None
                if (product["min_price"] is not None and final < product["min_price"]) or (
                    product["max_price"] is not None and final > product["max_price"]
                ):
                    raise HTTPException(409, "The price is outside this product's guardrails.")
                limits = await conn.fetchrow("SELECT * FROM change_limits WHERE id=TRUE")
                if limits and automatic:
                    stats = await conn.fetchrow(
                        """SELECT count(*) FILTER(WHERE ts>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS n,
                        max(ts) AS last FROM price_history WHERE product_id=$1""",
                        product_id,
                    )
                    if stats["n"] >= limits["max_changes_per_day"]:
                        return None
                    if (
                        stats["last"]
                        and (datetime.now(timezone.utc) - stats["last"]).total_seconds()
                        < limits["min_hours_between"] * 3600
                    ):
                        return None
                    if abs(final - current) / current * 100 > limits["max_change_pct"]:
                        return None
            status = (
                "rejected"
                if action == "reject"
                else (
                    "auto_approved"
                    if automatic
                    else ("overridden" if final != row["proposed_price"] else "approved")
                )
            )
            await conn.execute(
                "UPDATE proposals SET status=$2,decided_by=$3,decided_at=now(),final_price=$4,decline_reason=$5 WHERE decision_id=$1",
                decision_id,
                status,
                actor,
                None if action == "reject" else final,
                reason,
            )
            event = {
                "rejected": "proposal_rejected",
                "approved": "proposal_approved",
                "overridden": "proposal_overridden",
                "auto_approved": "proposal_auto_approved",
            }[status]
            await conn.execute(
                "INSERT INTO agent_audit_log(event_type,product_id,decision_id,actor,detail) VALUES($1,$2,$3,$4,$5)",
                event,
                product_id,
                decision_id,
                actor,
                {"final_price": None if action == "reject" else float(final), "reason": reason},
            )
            result = {"published": False, "queued": 0}
            if action != "reject":
                result = await gate.publish_price(
                    product_id=product_id,
                    price=final,
                    reason_tag="Manager override"
                    if status == "overridden"
                    else row["reason_tag"],
                    approval="auto"
                    if automatic
                    else ("override" if status == "overridden" else "manager"),
                    approved_by=actor,
                    decision_id=decision_id,
                    agent_version=row["agent_version"],
                    connection=conn,
                )
            await outcome(conn, row, status, actor, None if action == "reject" else final)
    bus.emit("suggestions", decided=[str(decision_id)], product_id=product_id, status=status)
    return dict(
        decision_id=str(decision_id),
        product_id=product_id,
        status=status,
        final_price=None if action == "reject" else str(final),
        agent_proposed=str(row["proposed_price"]),
        **result,
    )
