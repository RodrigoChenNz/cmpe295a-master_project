"""Inbound MQTT messages: validate, persist, then offer to the gate.

Every payload here comes from another team member's service, so nothing is
trusted: a malformed message is logged and dropped rather than allowed to raise
through the message loop and kill the subscription.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from . import bus, db, gate, labels_in

log = logging.getLogger("shelfiq.ingest")


def _dec(value: Any) -> Decimal:
    # Via str, never float: Decimal(0.1) is not 0.1.
    return Decimal(str(value))


def _when(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def handle(topic: str, raw: bytes) -> None:
    # A retained label config, replayed when this service connects (and echoed
    # when it publishes one). Before JSON parsing: an empty one is a deletion.
    if topic.startswith("retail/label/") and topic.endswith("/config"):
        try:
            await labels_in.config_seen(topic.split("/")[2], raw)
        except Exception:  # noqa: BLE001
            log.exception("failed to check %s", topic)
        return

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("dropping unparseable message on %s: %s", topic, exc)
        return

    if not isinstance(payload, dict):
        log.warning("dropping non-object payload on %s", topic)
        return

    # The label topics are keyed by label or device, not by product.
    try:
        if topic.startswith("retail/shelf/"):
            await labels_in.shelf_report(topic.rsplit("/", 1)[-1], payload)
            return
        if topic == "retail/label/discovered":
            await labels_in.discovered(payload)
            return
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        log.warning("dropping malformed payload on %s: %s", topic, exc)
        return
    except Exception:  # noqa: BLE001
        log.exception("failed to ingest %s", topic)
        return

    product_id = payload.get("product_id")
    topic_id = topic.rsplit("/", 1)[-1]
    # contracts/README.md: the id in the topic must equal the id in the payload.
    # A mismatch means one of them is wrong and we cannot tell which.
    if product_id != topic_id:
        log.warning(
            "dropping %s: topic says %s, payload says %s", topic, topic_id, product_id
        )
        return

    try:
        if topic.startswith("retail/proposal/"):
            await _proposal(payload)
        elif topic.startswith("retail/explain/"):
            await _explain(payload)
        elif topic.startswith("retail/inventory/"):
            await _inventory(payload)
        else:
            log.debug("ignoring unsubscribed topic %s", topic)
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        log.warning("dropping malformed payload on %s: %s", topic, exc)
    except Exception:  # noqa: BLE001
        log.exception("failed to ingest %s", topic)


async def _proposal(p: dict) -> None:
    product_id = p["product_id"]
    decision_id = p["decision_id"]

    known = await db.fetchval(
        "SELECT 1 FROM products WHERE product_id = $1", product_id
    )
    if not known:
        # A proposal for a SKU the store does not stock is not actionable, and
        # inserting it would violate the foreign key anyway.
        log.warning("proposal for unknown product %s; dropped", product_id)
        return

    reason_tag = str(p["reason_tag"])[:16]

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            # A newer proposal replaces any pending one for the same SKU. The
            # old one is marked superseded rather than deleted, because the
            # queue the manager saw at 14:02 has to stay reconstructible.
            await conn.execute(
                """UPDATE proposals SET status = 'superseded', decided_at = now(),
                                        decided_by = 'system'
                    WHERE product_id = $1 AND status = 'pending'""",
                product_id,
            )
            await conn.execute(
                """INSERT INTO proposals
                       (decision_id, product_id, proposed_price, current_price,
                        reason_tag, agent_version, status, expires_at, ts,
                        agent_confidence, rationale)
                   VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7, $8, $9, $10)
                   ON CONFLICT (decision_id) DO NOTHING""",
                decision_id,
                product_id,
                _dec(p["proposed_price"]),
                _dec(p["current_price"]),
                reason_tag,
                str(p["agent_version"]),
                _when(p["expires_at"]),
                _when(p["ts"]),
                # Neither field is in the contract. Taken when offered, left
                # NULL when not, and the UI says "not reported" either way.
                _dec(p["confidence"]) if p.get("confidence") is not None else None,
                p.get("rationale"),
            )
            await conn.execute(
                """INSERT INTO agent_audit_log
                       (event_type, product_id, decision_id, actor, detail, ts)
                   VALUES ('proposal_received', $1, $2, $3, $4, now())""",
                product_id,
                decision_id,
                str(p["agent_version"]),
                {"proposed_price": float(_dec(p["proposed_price"]))},
            )

    verdict = await gate.evaluate_proposal(decision_id)
    if verdict.auto_approve:
        await auto_approve(decision_id)
    else:
        bus.emit("proposal", product_id=product_id, decision_id=decision_id,
                 needs_human=True, reasons=verdict.reasons)


async def auto_approve(decision_id: str) -> None:
    """Apply a proposal under the manager's standing policy.

    Still fully audited — `approval: "auto"`, `approved_by: "policy"` — and it
    still appears in the log the manager reviews. Delegated is not invisible.
    """
    row = await db.fetchrow(
        """SELECT product_id, proposed_price, reason_tag, agent_version
             FROM proposals WHERE decision_id = $1 AND status = 'pending'""",
        decision_id,
    )
    if row is None:
        return

    await db.execute(
        """UPDATE proposals
              SET status = 'auto_approved', decided_by = 'policy',
                  decided_at = now(), final_price = proposed_price
            WHERE decision_id = $1""",
        decision_id,
    )
    await db.execute(
        """INSERT INTO agent_audit_log
               (event_type, product_id, decision_id, actor, detail, ts)
           VALUES ('proposal_auto_approved', $1, $2, 'policy', $3, now())""",
        row["product_id"],
        decision_id,
        {"price": float(row["proposed_price"])},
    )
    await gate.publish_price(
        product_id=row["product_id"],
        price=row["proposed_price"],
        reason_tag=row["reason_tag"],
        approval="auto",
        approved_by="policy",
        decision_id=decision_id,
        agent_version=row["agent_version"],
    )


async def _explain(p: dict) -> None:
    decision_id = p["decision_id"]
    shap = p["shap"]
    if not isinstance(shap, list):
        raise TypeError("shap must be an array")

    known = await db.fetchval(
        "SELECT 1 FROM proposals WHERE decision_id = $1", decision_id
    )
    if not known:
        # An explanation can legitimately arrive before its proposal, or for a
        # proposal this dashboard never saw. Dropped rather than orphaned,
        # because the FK requires the proposal and a retry will bring it back.
        log.info("explanation for unknown decision %s; dropped", decision_id)
        return

    await db.execute(
        """INSERT INTO shap_vectors (decision_id, product_id, base_value, shap, ts)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (decision_id) DO UPDATE
              SET base_value = EXCLUDED.base_value,
                  shap = EXCLUDED.shap,
                  ts = EXCLUDED.ts""",
        decision_id,
        p["product_id"],
        _dec(p["base_value"]),
        shap,
        _when(p["ts"]),
    )
    bus.emit("explain", product_id=p["product_id"], decision_id=decision_id)


async def _inventory(p: dict) -> None:
    known = await db.fetchval(
        "SELECT 1 FROM products WHERE product_id = $1", p["product_id"]
    )
    if not known:
        return
    await db.execute(
        """INSERT INTO inventory_risk
               (product_id, dead_stock_risk, predicted_sellby, units_on_hand,
                model_version, ts)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        p["product_id"],
        _dec(p["dead_stock_risk"]),
        datetime.fromisoformat(str(p["predicted_sellby"])).date()
        if "T" in str(p["predicted_sellby"])
        else _date(p["predicted_sellby"]),
        p.get("units_on_hand"),
        str(p["model_version"]),
        _when(p["ts"]),
    )
    bus.emit("inventory", product_id=p["product_id"],
             dead_stock_risk=str(_dec(p["dead_stock_risk"])))


def _date(value: Any):
    from datetime import date

    return date.fromisoformat(str(value))


# ─── expiry timer ───────────────────────────────────────────────────────────


async def expire_lapsed() -> int:
    """Lapse proposals nobody acted on. The shelf keeps its approved price.

    Run on a timer rather than computed on read, because the review queue has
    to be the same set of rows for every client and for the audit log.
    """
    rows = await db.fetch(
        """UPDATE proposals
              SET status = 'expired', decided_at = now(), decided_by = 'system'
            WHERE status = 'pending' AND expires_at <= now()
        RETURNING decision_id, product_id"""
    )
    for r in rows:
        await db.execute(
            """INSERT INTO agent_audit_log
                   (event_type, product_id, decision_id, actor, detail, ts)
               VALUES ('proposal_expired', $1, $2, 'system', '{}'::jsonb, now())""",
            r["product_id"],
            r["decision_id"],
        )
    if rows:
        bus.emit("expired", count=len(rows),
                 product_ids=[r["product_id"] for r in rows])
        log.info("expired %d lapsed proposals", len(rows))
    return len(rows)


async def settle_label_pushes(timeout_secs: int = 45) -> int:
    """Mark pushes that were never acknowledged as failed.

    There is no acknowledgement topic in the contract yet, so "confirmed" can
    only ever mean "we published it and heard no complaint". Rather than claim
    that as confirmation, an unsettled push ages into `failed` and the label
    reads as such. Worth replacing with a real `retail/shelf/{id}` ack — see
    the note in services/dashboard-api/README.md.
    """
    rows = await db.fetch(
        """UPDATE label_pushes
              SET result = 'failed', settled_at = now()
            WHERE result = 'pending'
              AND published_at < now() - make_interval(secs => $1)
        RETURNING label_id""",
        float(timeout_secs),
    )
    for r in rows:
        await db.execute(
            "UPDATE shelf_labels SET status = 'failed' WHERE label_id = $1",
            r["label_id"],
        )
    if rows:
        bus.emit("labels", changed=[r["label_id"] for r in rows])
    return len(rows)


async def mark_stale_labels() -> int:
    """A label not seen within the configured window reads as offline."""
    window = await db.fetchval("SELECT label_stale_secs FROM store_settings LIMIT 1")
    rows = await db.fetch(
        """UPDATE shelf_labels
              SET status = 'offline'
            WHERE product_id IS NOT NULL
              AND status <> 'offline'
              AND (last_seen_at IS NULL
                   OR last_seen_at < now() - make_interval(secs => $1))
        RETURNING label_id""",
        float(window or 4500),
    )
    if rows:
        bus.emit("labels", changed=[r["label_id"] for r in rows])
    return len(rows)
