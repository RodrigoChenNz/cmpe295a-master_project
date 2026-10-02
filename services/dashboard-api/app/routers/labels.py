"""Shelf labels: the network table, label detail, and pairing.

Everything on these screens is the console's *belief* about a device, derived
from what the gateway last reported. It is never a claim about what the glass
is showing right now — the dashboard and the edge node are independent MQTT
subscribers and the contract has no acknowledgement topic, so a label can be
displaying a stale price from flash while this table says 'online'. Hence
`as_published_at` in every response and "as published" in the UI.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import auth, bus, db, gate, serialize

router = APIRouter(prefix="/api/labels", tags=["labels"])


@router.get("")
async def list_labels(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
    status_filter: Annotated[list[str], Query(alias="status")] = [],
    kind: Literal["any", "lcd", "eink"] = "any",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    clauses, params = [], []
    if q:
        params.append(f"%{q}%")
        clauses.append(
            f"(l.label_id ILIKE ${len(params)} OR p.name ILIKE ${len(params)}"
            f" OR l.slot_code ILIKE ${len(params)})"
        )
    if status_filter:
        params.append(list(status_filter))
        clauses.append(f"l.status = ANY(${len(params)})")
    if kind != "any":
        params.append(kind)
        clauses.append(f"l.kind = ${len(params)}")
    where = " AND ".join(clauses) if clauses else "TRUE"

    total = await db.fetchval(
        f"""SELECT count(*) FROM shelf_labels l
            LEFT JOIN products p ON p.product_id = l.product_id
            WHERE {where}""",
        *params,
    )
    pages = max(1, -(-total // page_size))
    page_no = min(page, pages)

    rows = await db.fetch(
        f"""SELECT l.*, p.name AS product_name, p.display_name,
                   loc.name AS location_name,
                   push.price AS last_price, push.published_at AS as_published_at,
                   push.result AS last_push_result
              FROM shelf_labels l
              LEFT JOIN products p ON p.product_id = l.product_id
              LEFT JOIN location_slots s ON s.slot_code = l.slot_code
              LEFT JOIN locations loc ON loc.location_id = s.location_id
              LEFT JOIN LATERAL (
                   SELECT price, published_at, result FROM label_pushes
                    WHERE label_id = l.label_id
                    ORDER BY published_at DESC LIMIT 1
              ) push ON TRUE
             WHERE {where}
             -- Failures first, then offline, then the healthy ones: the table
             -- exists to find the labels that need a person.
             ORDER BY CASE l.status WHEN 'failed' THEN 0 WHEN 'offline' THEN 1
                                    WHEN 'syncing' THEN 2 WHEN 'online' THEN 3
                                    ELSE 4 END,
                      l.label_id
             LIMIT {page_size} OFFSET {(page_no - 1) * page_size}""",
        *params,
    )

    by_status = {
        r["status"]: r["count"]
        for r in await db.fetch(
            "SELECT status, count(*) AS count FROM shelf_labels GROUP BY status"
        )
    }

    out = serialize.page(rows, total, page_no, page_size)
    out["by_status"] = by_status
    out["network"] = bus.broker_state() | {"queued": bus.queued_count()}
    return out


@router.get("/{label_id}")
async def get_label(
    label_id: str,
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    label = await db.fetchrow(
        """SELECT l.*, p.name AS product_name, p.display_name, p.brand,
                  p.unit_cost, loc.name AS location_name
             FROM shelf_labels l
             LEFT JOIN products p ON p.product_id = l.product_id
             LEFT JOIN location_slots s ON s.slot_code = l.slot_code
             LEFT JOIN locations loc ON loc.location_id = s.location_id
            WHERE l.label_id = $1""",
        label_id,
    )
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such label.")

    pushes = await db.fetch(
        """SELECT id, price, line1, line2, result, published_at, settled_at,
                  decision_id
             FROM label_pushes WHERE label_id = $1
            ORDER BY published_at DESC LIMIT 25""",
        label_id,
    )
    events = await db.fetch(
        """SELECT event_type, detail, ts FROM label_events
            WHERE label_id = $1 ORDER BY ts DESC LIMIT 25""",
        label_id,
    )

    render = None
    if pushes:
        render = {"line1": pushes[0]["line1"], "line2": pushes[0]["line2"]}
    elif label["product_id"]:
        current = await db.fetchval(
            """SELECT price FROM price_history WHERE product_id = $1
                ORDER BY ts DESC LIMIT 1""",
            label["product_id"],
        )
        if current is not None:
            line1, line2 = gate.render_lines(
                label["display_name"] or label["product_name"] or label_id, current, ""
            )
            render = {"line1": line1, "line2": line2}

    return {
        "label": serialize.row(label),
        "pushes": serialize.rows(pushes),
        "events": serialize.rows(events),
        # Labelled "as published" in the UI, not "what the shelf is showing".
        "shelf_render": render,
        "as_published_at": serialize.value(
            "published_at", pushes[0]["published_at"]
        ) if pushes else None,
    }


class PairBody(BaseModel):
    product_id: str | None = Field(default=None, pattern=r"^SKU-[0-9]{3,}$")
    slot_code: str | None = Field(default=None, max_length=40)


@router.post("/{label_id}/pair")
async def pair(
    label_id: str,
    body: PairBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    """Pair a label to a product, or unpair it by sending product_id: null."""
    label = await db.fetchval(
        "SELECT 1 FROM shelf_labels WHERE label_id = $1", label_id
    )
    if not label:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such label.")

    if body.product_id:
        taken = await db.fetchval(
            """SELECT label_id FROM shelf_labels
                WHERE product_id = $1 AND label_id <> $2""",
            body.product_id,
            label_id,
        )
        if taken:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{body.product_id} is already paired to {taken}. "
                "Unpair that label first.",
            )

    await db.execute(
        """UPDATE shelf_labels
              SET product_id = $2, slot_code = $3,
                  status = CASE WHEN $2::text IS NULL THEN 'unassigned'
                                ELSE 'syncing' END,
                  paired_at = CASE WHEN $2::text IS NULL THEN NULL ELSE now() END
            WHERE label_id = $1""",
        label_id,
        body.product_id,
        body.slot_code,
    )
    await db.execute(
        """INSERT INTO label_events (label_id, event_type, detail)
           VALUES ($1, $2, $3)""",
        label_id,
        "paired" if body.product_id else "unpaired",
        {"product_id": body.product_id, "by": user.username},
    )
    bus.emit("labels", changed=[label_id], action="paired")

    # Pushing the current price right after pairing, so a freshly paired label
    # does not sit showing whatever it had in flash.
    if body.product_id:
        current = await db.fetchrow(
            """SELECT price, reason_tag, decision_id, agent_version
                 FROM price_history WHERE product_id = $1
                ORDER BY ts DESC LIMIT 1""",
            body.product_id,
        )
        if current:
            await gate.publish_price(
                product_id=body.product_id,
                price=current["price"],
                reason_tag=current["reason_tag"],
                approval="manager",
                approved_by=user.username,
                decision_id=None,
                agent_version=None,
            )

    return await get_label(label_id, user)


class ResendBody(BaseModel):
    label_ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/resend")
async def resend(
    body: ResendBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Re-publish the current approved price to these labels.

    This republishes an *already approved* price, so it is not a new pricing
    decision and makes no new proposal. It is still audited, because something
    did reach a shelf.
    """
    sent, skipped = [], []
    for label_id in body.label_ids:
        row = await db.fetchrow(
            """SELECT l.product_id, h.price, h.reason_tag, h.decision_id,
                      h.agent_version
                 FROM shelf_labels l
                 LEFT JOIN LATERAL (
                      SELECT price, reason_tag, decision_id, agent_version
                        FROM price_history WHERE product_id = l.product_id
                       ORDER BY ts DESC LIMIT 1
                 ) h ON TRUE
                WHERE l.label_id = $1""",
            label_id,
        )
        if row is None or row["product_id"] is None or row["price"] is None:
            skipped.append({"label_id": label_id, "reason": "No paired product with a price."})
            continue
        await gate.publish_price(
            product_id=row["product_id"],
            price=row["price"],
            reason_tag=row["reason_tag"],
            approval="manager",
            approved_by=user.username,
            decision_id=row["decision_id"],
            agent_version=row["agent_version"],
        )
        sent.append(label_id)

    return {"sent": sent, "skipped": skipped, "count": len(sent)}
