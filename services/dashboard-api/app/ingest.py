"""Schema-validated, duplicate-safe MQTT ingestion and device acknowledgements."""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from jsonschema import ValidationError
from retail.contracts import loads, validate
from . import bus, db, decisions

log = logging.getLogger("shelfiq.ingest")


def _when(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return dt


async def handle(topic, raw):
    try:
        p = loads(raw)
        family = topic.split("/")[1]
        if family not in {"proposal", "explain", "inventory", "state", "shelf"}:
            return False
        validate(family, p)
        key = "label_id" if family == "shelf" else "product_id"
        if p[key] != topic.rsplit("/", 1)[-1]:
            raise ValueError("Topic and payload identifiers disagree")
        timestamp = _when(p["ts"])
        if (timestamp - datetime.now(timezone.utc)).total_seconds() > 300:
            raise ValueError("Timestamp is more than five minutes in the future")
        await {
            "proposal": _proposal,
            "explain": _explain,
            "inventory": _inventory,
            "state": _state,
            "shelf": _shelf,
        }[family](p)
        return True
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        log.warning("Rejected %s: %s", topic, exc)
        return False
    except Exception:
        log.exception("Ingestion failed on %s", topic)
        return False


async def _proposal(p):
    product_id = p["product_id"]
    decision_id = p["decision_id"]
    now = datetime.now(timezone.utc)
    expires = _when(p["expires_at"])
    created = _when(p["ts"])
    if expires <= created:
        raise ValueError("Expiry must follow creation")
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT product_id FROM products WHERE product_id=$1 FOR UPDATE", product_id
            ):
                raise ValueError("Unknown product")
            if await conn.fetchval("SELECT 1 FROM proposals WHERE decision_id=$1", decision_id):
                return
            latest = await conn.fetchval(
                "SELECT max(ts) FROM proposals WHERE product_id=$1", product_id
            )
            status = (
                "expired"
                if expires <= now
                else ("superseded" if latest and created <= latest else "pending")
            )
            if status == "pending":
                previous = await conn.fetch(
                    """UPDATE proposals SET status='superseded',decided_at=now(),decided_by='system'
                    WHERE product_id=$1 AND status='pending' RETURNING *""",
                    product_id,
                )
                for row in previous:
                    await decisions.outcome(conn, row, "superseded", "system")
            await conn.execute(
                """INSERT INTO proposals(decision_id,product_id,proposed_price,current_price,
                reason_tag,agent_version,status,expires_at,ts,agent_confidence,rationale,decided_at,decided_by)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                decision_id,
                product_id,
                Decimal(str(p["proposed_price"])),
                Decimal(str(p["current_price"])),
                p["reason_tag"],
                p["agent_version"],
                status,
                expires,
                created,
                Decimal(str(p["confidence"])) if p.get("confidence") is not None else None,
                p.get("rationale"),
                None if status == "pending" else now,
                None if status == "pending" else "system",
            )
            await conn.execute(
                """INSERT INTO agent_audit_log(event_type,product_id,decision_id,actor,detail)
                VALUES('proposal_received',$1,$2,$3,$4)""",
                product_id,
                decision_id,
                p["agent_version"],
                {"status": status},
            )
            pending = await conn.fetchval(
                "DELETE FROM pending_explanations WHERE decision_id=$1 RETURNING payload",
                decision_id,
            )
            if pending:
                try:
                    await _store_explanation(conn, pending, p)
                except ValueError:
                    log.warning("Discarded non-additive early explanation for %s", decision_id)
    if status == "pending":
        try:
            applied = await decisions.decide(decision_id, actor="policy", automatic=True)
        except HTTPException:
            applied = None
        if not applied:
            bus.emit(
                "proposal", product_id=product_id, decision_id=decision_id, needs_human=True
            )


async def auto_approve(decision_id):
    return await decisions.decide(decision_id, actor="policy", automatic=True)


async def _store_explanation(conn, p, proposal):
    if p["product_id"] != proposal["product_id"]:
        raise ValueError("Explanation belongs to another product")
    total = Decimal(str(p["base_value"])) + sum(Decimal(str(x["value"])) for x in p["shap"])
    if abs(total - Decimal(str(proposal["proposed_price"]))) > Decimal(".005"):
        raise ValueError("Explanation does not reconstruct the proposed price")
    await conn.execute(
        """INSERT INTO shap_vectors(decision_id,product_id,base_value,shap,ts,metadata)
        VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(decision_id) DO NOTHING""",
        p["decision_id"],
        p["product_id"],
        Decimal(str(p["base_value"])),
        p["shap"],
        _when(p["ts"]),
        {k: p[k] for k in ["method", "target", "reference"] if k in p},
    )


async def _explain(p):
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT product_id FROM products WHERE product_id=$1 FOR UPDATE",
                p["product_id"],
            ):
                raise ValueError("Unknown product")
            proposal = await conn.fetchrow(
                "SELECT * FROM proposals WHERE decision_id=$1", p["decision_id"]
            )
            if proposal is None:
                await conn.execute(
                    "INSERT INTO pending_explanations(decision_id,payload) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    p["decision_id"],
                    p,
                )
            else:
                await _store_explanation(conn, p, proposal)
    bus.emit("explain", product_id=p["product_id"], decision_id=p["decision_id"])


async def _inventory(p):
    if not await db.fetchval("SELECT 1 FROM products WHERE product_id=$1", p["product_id"]):
        raise ValueError("Unknown product")
    from datetime import date

    await db.execute(
        """INSERT INTO inventory_risk(product_id,dead_stock_risk,predicted_sellby,units_on_hand,model_version,ts)
        VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(product_id,model_version,ts) DO NOTHING""",
        p["product_id"],
        Decimal(str(p["dead_stock_risk"])),
        date.fromisoformat(str(p["predicted_sellby"])),
        p.get("units_on_hand"),
        p["model_version"],
        _when(p["ts"]),
    )
    bus.emit("inventory", product_id=p["product_id"])


async def _state(p, conn=None):
    if conn is None:
        async with db.pool().acquire() as connection:
            async with connection.transaction():
                await _state(p, connection)
        bus.emit("inventory", product_id=p["product_id"])
        return
    if not await conn.fetchval(
        "SELECT product_id FROM products WHERE product_id=$1 FOR UPDATE",
        p["product_id"],
    ):
        raise ValueError("Unknown product")
    if await conn.fetchval("SELECT 1 FROM store_observations WHERE state_id=$1", p["state_id"]):
        return
    latest = await conn.fetchval(
        "SELECT max(ts) FROM store_observations WHERE product_id=$1", p["product_id"]
    )
    await conn.execute(
        "INSERT INTO store_observations(state_id,product_id,payload,ts) VALUES($1,$2,$3,$4)",
        p["state_id"],
        p["product_id"],
        p,
        _when(p["ts"]),
    )
    if latest is None or _when(p["ts"]) > latest:
        await conn.execute(
            "UPDATE products SET stock_on_hand=$2 WHERE product_id=$1",
            p["product_id"],
            p["units_on_hand"],
        )


async def _shelf(p):
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            label = await conn.fetchrow(
                "SELECT * FROM shelf_labels WHERE label_id=$1 FOR UPDATE", p["label_id"]
            )
            if not label or label["product_id"] != p["product_id"]:
                raise ValueError("Label is not paired with that product")
            await conn.execute(
                """UPDATE shelf_labels SET last_seen_at=now(),firmware=$2,emulated=$3,
                rssi_dbm=$4,battery_pct=$5 WHERE label_id=$1""",
                p["label_id"],
                p["firmware"],
                p["emulated"],
                p.get("rssi"),
                p.get("battery"),
            )
            if p["event"] == "rendered":
                push = await conn.fetchrow(
                    "SELECT * FROM label_pushes WHERE label_id=$1 AND publication_id=$2",
                    p["label_id"],
                    p["publication_id"],
                )
                if (
                    not push
                    or push["price"] != Decimal(str(p["price"]))
                    or push["line1"] != p["line1"]
                    or push["line2"] != p["line2"]
                ):
                    raise ValueError(
                        "Acknowledgement content does not match the approved publication"
                    )
                newest = await conn.fetchval(
                    "SELECT max(publication_id) FROM label_pushes WHERE label_id=$1",
                    p["label_id"],
                )
                if p["publication_id"] != newest:
                    return
                await conn.execute(
                    "UPDATE label_pushes SET result='confirmed',settled_at=now() WHERE id=$1",
                    push["id"],
                )
                await conn.execute(
                    """UPDATE shelf_labels SET status='online',last_publication_id=$2,
                    rendered_line1=$3,rendered_line2=$4,rendered_at=$5 WHERE label_id=$1""",
                    p["label_id"],
                    p["publication_id"],
                    p["line1"],
                    p["line2"],
                    _when(p["rendered_at"]),
                )
    bus.emit("labels", changed=[p["label_id"]])


async def expire_lapsed():
    count = 0
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("""UPDATE proposals SET status='expired',decided_at=now(),decided_by='system'
                WHERE status='pending' AND expires_at<=now() RETURNING *""")
            for row in rows:
                await conn.execute(
                    "INSERT INTO agent_audit_log(event_type,product_id,decision_id,actor) VALUES('proposal_expired',$1,$2,'system')",
                    row["product_id"],
                    row["decision_id"],
                )
                await decisions.outcome(conn, row, "expired", "system")
            count = len(rows)
            await conn.execute(
                "DELETE FROM pending_explanations WHERE received_at<now()-interval '1 day'"
            )
    if count:
        bus.emit("expired", count=count)
    return count


async def settle_label_pushes(timeout_secs=45):
    rows = await db.fetch(
        """UPDATE label_pushes SET result='failed',settled_at=now()
        WHERE result='pending' AND sent_at<now()-make_interval(secs=>$1) RETURNING label_id,publication_id""",
        float(timeout_secs),
    )
    for row in rows:
        await db.execute(
            """UPDATE shelf_labels SET status='failed' WHERE label_id=$1
            AND NOT EXISTS(SELECT 1 FROM label_pushes WHERE label_id=$1 AND publication_id>$2)""",
            row["label_id"],
            row["publication_id"],
        )
    if rows:
        bus.emit("labels", changed=[r["label_id"] for r in rows])
    return len(rows)


async def mark_stale_labels():
    window = await db.fetchval("SELECT label_stale_secs FROM store_settings LIMIT 1")
    rows = await db.fetch(
        """UPDATE shelf_labels SET status='offline' WHERE product_id IS NOT NULL
        AND status<>'offline' AND (last_seen_at IS NULL OR last_seen_at<now()-make_interval(secs=>$1)) RETURNING label_id""",
        float(window or 900),
    )
    if rows:
        bus.emit("labels", changed=[r["label_id"] for r in rows])
    return len(rows)
