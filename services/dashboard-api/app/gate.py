"""The decision gate — the one piece of logic that makes this service the
control point of the system rather than a read model.

A proposal arrives. Either the manager's standing policy already covers it, in
which case it is applied with no human; or it does not, in which case it waits
in the review queue for one. Nothing reaches a shelf by any other route: there
is exactly one function in this codebase that publishes `retail/price/`, and it
is `publish_price` below.

The rule, from contracts/README.md, is applied verbatim:

    min_price <= proposed_price <= max_price
    |proposed_price - current_price| / current_price * 100 <= auto_approve_pct

`auto_approve_pct` of NULL means always ask.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from . import bus, db
from .db import money_num

log = logging.getLogger("shelfiq.gate")


@dataclass(frozen=True)
class GateVerdict:
    auto_approve: bool
    reasons: list[str]  # why a human is needed; empty when auto-approved

    @property
    def needs_human(self) -> bool:
        return not self.auto_approve


def evaluate(
    *,
    proposed_price: Decimal,
    current_price: Decimal,
    min_price: Decimal | None,
    max_price: Decimal | None,
    auto_approve_pct: Decimal | None,
    agent_excluded: bool = False,
) -> GateVerdict:
    """Pure, so it can be tested without a database or a broker."""
    reasons: list[str] = []

    if agent_excluded:
        reasons.append("This product is excluded from the agent.")

    if min_price is not None and proposed_price < min_price:
        reasons.append(f"Below the ${min_price} floor.")
    if max_price is not None and proposed_price > max_price:
        reasons.append(f"Above the ${max_price} ceiling.")

    if auto_approve_pct is None:
        reasons.append("Policy is set to always ask for this product.")
    elif current_price > 0:
        move = abs(proposed_price - current_price) / current_price * Decimal(100)
        if move > auto_approve_pct:
            reasons.append(
                f"A {move.quantize(Decimal('0.1'))}% move exceeds the "
                f"{auto_approve_pct}% you delegated."
            )
    else:
        # No current price to measure a move against. Treat that as "ask",
        # because a percentage of zero is not a threshold.
        reasons.append("No approved price to measure this change against.")

    return GateVerdict(auto_approve=not reasons, reasons=reasons)


async def evaluate_proposal(decision_id: str) -> GateVerdict:
    """Evaluate a stored proposal against its product's current policy."""
    row = await db.fetchrow(
        """SELECT pr.proposed_price, pr.current_price,
                  p.min_price, p.max_price, p.auto_approve_pct, p.agent_excluded
             FROM proposals pr
             JOIN products p ON p.product_id = pr.product_id
            WHERE pr.decision_id = $1""",
        decision_id,
    )
    if row is None:
        raise LookupError(f"no proposal {decision_id}")
    return evaluate(
        proposed_price=row["proposed_price"],
        current_price=row["current_price"],
        min_price=row["min_price"],
        max_price=row["max_price"],
        auto_approve_pct=row["auto_approve_pct"],
        agent_excluded=row["agent_excluded"],
    )


# ─── the only publisher of retail/price/ ────────────────────────────────────


async def publish_price(
    *,
    product_id: str,
    price: Decimal,
    reason_tag: str,
    approval: str,
    approved_by: str,
    decision_id: str | None,
    agent_version: str | None,
) -> dict[str, Any]:
    """Record an approved price and push it to the shelf.

    Order matters and is not an accident: the row is written first, then the
    message is published. A crash between the two leaves a price in the audit
    log that never reached a shelf, which the label-push row will show as
    unsettled. The opposite order would put a price on a shelf with no record
    of who approved it, and that is the one failure this project cannot have.
    """
    if approval not in ("auto", "manager", "override"):
        raise ValueError(f"approval must be auto|manager|override, got {approval!r}")
    if len(reason_tag) > 16:
        # The 16-character cap is LCD hardware, enforced at the publisher so it
        # is never patched in firmware. Truncating silently would ship a
        # misleading explanation to the shelf edge, so this raises instead.
        raise ValueError(
            f"reason_tag {reason_tag!r} is {len(reason_tag)} characters; "
            "LCD line 2 holds 16"
        )

    now = datetime.now(timezone.utc)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            history_id = await conn.fetchval(
                """INSERT INTO price_history
                       (product_id, price, currency, decision_id, reason_tag,
                        approval, approved_by, agent_version, ts)
                   VALUES ($1, $2, 'USD', $3, $4, $5, $6, $7, $8)
                   RETURNING id""",
                product_id,
                price,
                decision_id,
                reason_tag,
                approval,
                approved_by,
                agent_version,
                now,
            )
            await conn.execute(
                """INSERT INTO agent_audit_log
                       (event_type, product_id, decision_id, actor, detail, ts)
                   VALUES ('price_published', $1, $2, $3, $4, $5)""",
                product_id,
                decision_id,
                approved_by,
                {"price": money_num(price), "approval": approval},
                now,
            )
            label = await conn.fetchrow(
                """SELECT l.label_id, l.kind, p.display_name, p.name
                     FROM shelf_labels l
                     JOIN products p ON p.product_id = l.product_id
                    WHERE l.product_id = $1""",
                product_id,
            )
            push_id = None
            if label is not None:
                line1, line2 = render_lines(
                    label["display_name"] or label["name"], price, reason_tag
                )
                push_id = await conn.fetchval(
                    """INSERT INTO label_pushes
                           (label_id, product_id, decision_id, price, line1, line2,
                            result, published_at)
                       VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
                       RETURNING id""",
                    label["label_id"],
                    product_id,
                    decision_id,
                    price,
                    line1,
                    line2,
                    now,
                )
                await conn.execute(
                    """UPDATE shelf_labels SET status = 'syncing' WHERE label_id = $1""",
                    label["label_id"],
                )

    payload = {
        "product_id": product_id,
        "price": money_num(price),
        "currency": "USD",
        "decision_id": decision_id,
        "reason_tag": reason_tag,
        "approval": approval,
        "approved_by": approved_by,
        "agent_version": agent_version,
        "ts": now.isoformat().replace("+00:00", "Z"),
    }
    delivered = await bus.publish(
        f"retail/price/{product_id}", payload, qos=1, retain=True
    )

    if not delivered and push_id is not None:
        # Be honest in the UI: the price is approved and recorded, but it has
        # not left the building.
        log.info("price for %s is queued, not published", product_id)

    bus.emit(
        "price",
        product_id=product_id,
        price=str(price),
        approval=approval,
        approved_by=approved_by,
        decision_id=decision_id,
        delivered=delivered,
    )

    return {
        "history_id": history_id,
        "published": delivered,
        "queued": bus.queued_count(),
        "label_push_id": push_id,
    }


async def publish_guardrail(
    *,
    product_id: str,
    min_price: Decimal,
    max_price: Decimal,
    auto_approve_pct: Decimal | None,
    set_by: str,
    quiet: bool = False,
) -> bool:
    """Push a SKU's policy to the agent, so it can clamp its own proposals.

    `quiet` suppresses the stream event. A category save republishes one
    guardrail per SKU, and a stream event each would push dozens of messages
    through subscriber queues that hold 200 — tripping every connected browser's
    resync path. The caller emits one event describing the whole save instead.
    """
    if max_price < min_price:
        raise ValueError("max_price must be at least min_price")
    now = datetime.now(timezone.utc)
    payload = {
        "product_id": product_id,
        "min_price": money_num(min_price),
        "max_price": money_num(max_price),
        "auto_approve_pct": float(auto_approve_pct) if auto_approve_pct is not None else None,
        "set_by": set_by,
        "ts": now.isoformat().replace("+00:00", "Z"),
    }
    await db.execute(
        """INSERT INTO agent_audit_log
               (event_type, product_id, decision_id, actor, detail, ts)
           VALUES ('guardrail_set', $1, NULL, $2, $3, $4)""",
        product_id,
        set_by,
        payload,
        now,
    )
    delivered = await bus.publish(
        f"retail/guardrail/{product_id}", payload, qos=1, retain=True
    )
    if not quiet:
        bus.emit("guardrail", product_id=product_id, set_by=set_by, delivered=delivered)
    return delivered


async def publish_label_command(
    *,
    label_id: str,
    command: str,
    payload: dict[str, Any],
    requested_by: str,
) -> bool:
    """A command aimed at one device, rather than a price aimed at a shelf.

    **Not a contract topic.** `contracts/` defines five topics and none of them
    is a device command; adding one needs all four signatures. This publishes to
    `retail/label/{label_id}/{command}` so the console has somewhere to put
    "blink" today, and the firmware may ignore it until the team agrees a shape.

    `retain=False`, always. A retained command would re-fire on every reconnect
    for the life of the device.

    It lives here rather than calling `bus.publish` from a router so that every
    outbound publish in this service still goes through one module. The claim at
    the top of this file — that it is the only publisher of `retail/price/` —
    stays true; it simply has a neighbour now.
    """
    now = datetime.now(timezone.utc)
    body = {
        "label_id": label_id,
        "command": command,
        **payload,
        "requested_by": requested_by,
        "ts": now.isoformat().replace("+00:00", "Z"),
    }
    delivered = await bus.publish(
        f"retail/label/{label_id}/{command}", body, qos=1, retain=False
    )
    if not delivered:
        log.info("label command %s for %s is queued, not published", command, label_id)
    return delivered


async def publish_label_config(label_id: str) -> bool:
    """Tell the edge which device this label is, and its product.

    `retail/label/{label_id}/config`, retained, so a gateway that reboots
    relearns every assignment from its bridge. **Not a contract topic**: like
    blink, only the console and the edge read it. Does nothing for a label
    with no device; `clear_label_config` is the way to take one away.
    """
    row = await db.fetchrow(
        """SELECT l.device_id, l.product_id, p.name
             FROM shelf_labels l
             LEFT JOIN products p ON p.product_id = l.product_id
            WHERE l.label_id = $1""",
        label_id,
    )
    if row is None or row["device_id"] is None:
        return False
    now = datetime.now(timezone.utc)
    return await bus.publish(
        f"retail/label/{label_id}/config",
        {
            "label_id": label_id,
            "device_id": row["device_id"],
            "product_id": row["product_id"],
            "display_name": row["name"],
            "ts": now.isoformat().replace("+00:00", "Z"),
        },
        qos=1,
        retain=True,
    )


async def clear_label_config(label_id: str) -> bool:
    """Delete the retained config: the device goes back to "Not set up"."""
    return await bus.publish(f"retail/label/{label_id}/config", None, qos=1, retain=True)


async def publish_label_pairing(open_until: datetime | None, requested_by: str) -> bool:
    """Open or close "Add labels". Retained, so a gateway that reboots inside
    the window still knows; the bridge rings only for a window in the future."""
    now = datetime.now(timezone.utc)
    until = open_until or now
    return await bus.publish(
        "retail/label/pairing",
        {
            "open_until": until.isoformat().replace("+00:00", "Z"),
            "requested_by": requested_by,
            "ts": now.isoformat().replace("+00:00", "Z"),
        },
        qos=1,
        retain=True,
    )


# ─── shelf rendering ────────────────────────────────────────────────────────


def render_lines(display_name: str, price: Decimal, reason_tag: str) -> tuple[str, str]:
    """What a 16x2 LCD will show, computed here rather than in firmware.

    Line 1 is the display name. Line 2 is the price and, if it fits, the reason.
    Both are hard-cut at 16 characters because the hardware cuts them there, and
    the console renders exactly these strings so the mirror cannot disagree with
    the glass about anything except timing.
    """
    line1 = display_name.upper()[:16]
    money_part = f"${price:.2f}"
    room = 16 - len(money_part) - 1
    if room >= 4 and reason_tag:
        line2 = f"{money_part} {reason_tag[:room]}"
    else:
        line2 = money_part
    return line1, line2[:16]


def compose_reason(
    *,
    reason_tag: str,
    rationale: str | None,
    shap: list[dict] | None,
    product_name: str,
    stock_on_hand: int | None,
) -> dict[str, Any]:
    """The prose the Price suggestions table and detail screen show as "Why".

    `rationale` is not in contracts/proposal.schema.json. When the publisher
    supplied one, it is used as-is and marked `source: "agent"`. When it did
    not, a sentence is composed here from the reason tag, the SHAP vector and
    the product's own facts, and marked `source: "composed"` — so the UI can
    say where the words came from instead of implying the agent wrote them.
    """
    if rationale:
        return {"text": rationale, "source": "agent"}

    if shap:
        top = max(shap, key=lambda f: abs(float(f.get("value", 0))))
        feature = str(top.get("feature", "")).replace("_", " ")
        direction = "up" if float(top.get("value", 0)) > 0 else "down"
        text = (
            f"{reason_tag}. The largest single factor was {feature}, "
            f"pushing the price {direction}."
        )
        if stock_on_hand is not None:
            text += f" {stock_on_hand} units on hand."
        return {"text": text, "source": "composed"}

    return {
        "text": f"{reason_tag}. The agent sent no explanation for this proposal.",
        "source": "composed",
    }
