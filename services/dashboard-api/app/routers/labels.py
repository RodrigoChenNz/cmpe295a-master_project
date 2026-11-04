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


SORTABLE = {
    # The default. The table exists to find the labels that need a person, so
    # failures lead and healthy devices sink.
    "attention": ("CASE l.status WHEN 'failed' THEN 0 WHEN 'offline' THEN 1 "
                  "WHEN 'syncing' THEN 2 WHEN 'online' THEN 3 ELSE 4 END"),
    "label_id": "l.label_id",
    "product": "p.name",
    "slot": "l.slot_code",
    "last_seen": "l.last_seen_at",
    "rssi": "l.rssi_dbm",
    "battery": "l.battery_pct",
    "firmware": "l.firmware",
}

# Every status the CHECK allows. The donut legend has five fixed rows, so a
# status with no rows must come back as 0 rather than be missing.
LABEL_STATUSES = ("online", "syncing", "failed", "offline", "unassigned")

# One node's whole subtree, for the location filter: picking a zone has to
# catch every aisle, bay and slot under it, not the zone's own (zero) slots.
_DESCENDANTS = """
    WITH RECURSIVE sub AS (
        SELECT location_id FROM locations WHERE location_id = {param}
        UNION ALL
        SELECT l2.location_id FROM locations l2 JOIN sub ON l2.parent_id = sub.location_id
    )
"""


def _filters(
    q: str | None,
    status_filter: list[str],
    kind: str,
    paired: str,
    location: str | None,
    *,
    include_status: bool,
) -> tuple[str, str, list]:
    """Returns (cte, where, params).

    `include_status` is the whole point of splitting this out. The donut counts
    must respond to the search, the kind and the location but **not** to the
    status facet they themselves drive — a count that changes when you click the
    row beside it is unreadable (DESIGN.md rule 6).
    """
    clauses: list[str] = []
    params: list = []
    cte = ""

    if q:
        params.append(f"%{q}%")
        n = len(params)
        clauses.append(
            f"(l.label_id ILIKE ${n} OR p.name ILIKE ${n} OR l.slot_code ILIKE ${n}"
            f" OR p.display_name ILIKE ${n} OR l.firmware ILIKE ${n})"
        )
    if include_status and status_filter:
        params.append(list(status_filter))
        clauses.append(f"l.status = ANY(${len(params)})")
    if kind != "any":
        params.append(kind)
        clauses.append(f"l.kind = ${len(params)}")
    if paired == "paired":
        clauses.append("l.product_id IS NOT NULL")
    elif paired == "unpaired":
        clauses.append("l.product_id IS NULL")
    if location:
        params.append(location)
        cte = _DESCENDANTS.format(param=f"${len(params)}")
        clauses.append("s.location_id IN (SELECT location_id FROM sub)")

    return cte, (" AND ".join(clauses) if clauses else "TRUE"), params


_FROM = """
      FROM shelf_labels l
      LEFT JOIN products p ON p.product_id = l.product_id
      LEFT JOIN location_slots s ON s.slot_code = l.slot_code
      LEFT JOIN locations loc ON loc.location_id = s.location_id
      LEFT JOIN shelfiq_location_paths lp ON lp.location_id = s.location_id
"""


class RegisterLabel(BaseModel):
    label_id: str = Field(pattern=r"^SHLF-[0-9]{3,}$")
    kind: Literal['lcd','eink'] = 'lcd'


@router.post('',status_code=201)
async def register_label(body:RegisterLabel,user:Annotated[auth.CurrentUser,Depends(auth.require_editor)]):
    if await db.fetchval('SELECT 1 FROM shelf_labels WHERE label_id=$1',body.label_id):
        raise HTTPException(409,'This label is already registered.')
    try:
        await db.execute('INSERT INTO shelf_labels(label_id,kind) VALUES($1,$2)',body.label_id,body.kind)
    except Exception as exc:
        if db.unique_constraint(exc):raise HTTPException(409,'This label is already registered.') from exc
        raise
    bus.emit('labels',changed=[body.label_id])
    return {'label_id':body.label_id,'kind':body.kind,'status':'unassigned'}


@router.get("")
async def list_labels(
    user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
    status_filter: Annotated[list[str], Query(alias="status")] = [],
    kind: Literal["any", "lcd", "eink"] = "any",
    paired: Literal["any", "paired", "unpaired"] = "any",
    location: str | None = None,
    sort: str = "attention",
    dir: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    if sort not in SORTABLE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"sort must be one of {', '.join(sorted(SORTABLE))}",
        )
    column = SORTABLE[sort]
    direction = "ASC" if dir == "asc" else "DESC"

    cte, where, params = _filters(
        q, list(status_filter), kind, paired, location, include_status=True
    )

    total = await db.fetchval(
        f"{cte} SELECT count(*) {_FROM} WHERE {where}", *params
    )
    pages = max(1, -(-total // page_size))
    page_no = min(page, pages)

    rows = await db.fetch(
        f"""{cte}
            SELECT l.*, p.name AS product_name, p.display_name,
                   loc.name AS location_name, loc.location_id,
                   lp.path AS location_path,
                   push.price AS last_price, push.published_at AS as_published_at,
                   push.result AS last_push_result
              {_FROM}
              LEFT JOIN LATERAL (
                   SELECT price, published_at, result FROM label_pushes
                    WHERE label_id = l.label_id
                    ORDER BY published_at DESC LIMIT 1
              ) push ON TRUE
             WHERE {where}
             -- NULLS LAST matters: last_seen_at and battery_pct are nullable,
             -- and an ascending sort would otherwise lead with unknowns.
             ORDER BY {column} {direction} NULLS LAST, l.label_id ASC
             LIMIT {page_size} OFFSET {(page_no - 1) * page_size}""",
        *params,
    )

    # Same filters minus the status facet — see _filters' docstring.
    cte_f, where_f, params_f = _filters(
        q, list(status_filter), kind, paired, location, include_status=False
    )
    counted = await db.fetch(
        f"{cte_f} SELECT l.status, count(*) AS count {_FROM} WHERE {where_f}"
        " GROUP BY l.status",
        *params_f,
    )
    by_status = {k: 0 for k in LABEL_STATUSES}
    for r in counted:
        by_status[r["status"]] = r["count"]

    out = serialize.page(rows, total, page_no, page_size)
    out["by_status"] = by_status
    network = bus.broker_state() | {"queued": bus.queued_count()}
    # Up or down is everyone's business; the broker's address is the super
    # admin's (see meta.py).
    if user.role != "superadmin":
        network = {k: v for k, v in network.items() if k not in ("host", "port", "tls")}
    out["network"] = network
    return out


@router.get("/{label_id}")
async def get_label(
    label_id: str,
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    label = await db.fetchrow(
        """SELECT l.*, p.name AS product_name, p.display_name, p.brand,
                  p.unit_cost, loc.name AS location_name,
                  lp.path AS location_path,
                  p.location_id AS product_location_id,
                  plp.path AS product_location_path
             FROM shelf_labels l
             LEFT JOIN products p ON p.product_id = l.product_id
             LEFT JOIN location_slots s ON s.slot_code = l.slot_code
             LEFT JOIN locations loc ON loc.location_id = s.location_id
             LEFT JOIN shelfiq_location_paths lp ON lp.location_id = s.location_id
             LEFT JOIN shelfiq_location_paths plp ON plp.location_id = p.location_id
            WHERE l.label_id = $1""",
        label_id,
    )
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such label.")

    pushes = await db.fetch(
        """SELECT id, price, line1, line2, result, published_at, settled_at, sent_at, publication_id,
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

    # A scalar count, not a count over the 25 pushes returned below: past 25
    # pushes in a day that figure would be silently wrong, on a card headed
    # "Device" where a reader would take it for a rate limit.
    writes_today = await db.fetchval(
        """SELECT count(*) FROM label_pushes
            WHERE label_id = $1 AND published_at >= date_trunc('day', now())""",
        label_id,
    )

    return {
        "label": serialize.row(label),
        "pushes": serialize.rows(pushes),
        "events": serialize.rows(events),
        "writes_today": writes_today,
        # Labelled "as published" in the UI, not "what the shelf is showing".
        "shelf_render": render,
        "as_published_at": serialize.value(
            "published_at", pushes[0]["published_at"]
        ) if pushes else None,
    }


class PairBody(BaseModel):
    product_id: str | None = Field(default=None, pattern=r"^SKU-[0-9]{3,}$")
    slot_code: str | None = Field(default=None, max_length=40)


def _slot_clash_error(constraint: str | None) -> HTTPException:
    """Backstop for a race the pre-checks cannot see."""
    which = {
        "shelf_labels_slot_idx": "That position",
        "shelf_labels_product_idx": "That product",
    }.get(constraint, "That value")
    return HTTPException(
        status.HTTP_409_CONFLICT,
        f"{which} was taken by another label a moment ago. Try again.",
    )


@router.post("/{label_id}/pair")
async def pair(
    label_id: str,
    body: PairBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    """Pair a label to a product, or unpair it by sending `product_id: null`.

    **A label's position and its pairing are separate facts.** Omitting
    `slot_code` leaves the position alone — a label being unpaired from a
    product is still physically hanging in bay A1, and wiping the slot because
    the caller did not mention it loses information nobody asked to lose. An
    explicit `"slot_code": null` still clears it, which is how a manager says
    "this label has come off the shelf".
    """
    label = await db.fetchrow(
        "SELECT slot_code FROM shelf_labels WHERE label_id = $1", label_id
    )
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such label.")

    # pydantic distinguishes a field that was absent from one explicitly null.
    touch_slot = "slot_code" in body.model_fields_set

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

    if touch_slot and body.slot_code:
        exists = await db.fetchval(
            "SELECT 1 FROM location_slots WHERE slot_code = $1", body.slot_code
        )
        if not exists:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"There is no position {body.slot_code}. Create it under "
                "Locations first.",
            )
        holder = await db.fetchval(
            "SELECT label_id FROM shelf_labels WHERE slot_code = $1 AND label_id <> $2",
            body.slot_code,
            label_id,
        )
        if holder:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{body.slot_code} is held by {holder}. Move that label first.",
            )

    sets = ["product_id = $2",
            "status = CASE WHEN $2::text IS NULL THEN 'unassigned' ELSE 'syncing' END",
            "paired_at = CASE WHEN $2::text IS NULL THEN NULL ELSE now() END"]
    args = [label_id, body.product_id]
    if touch_slot:
        sets.append("slot_code = $3")
        args.append(body.slot_code)

    try:
        await db.execute(
            f"UPDATE shelf_labels SET {', '.join(sets)} WHERE label_id = $1", *args
        )
    except Exception as exc:  # noqa: BLE001
        constraint = db.unique_constraint(exc)
        if constraint is not None:
            raise _slot_clash_error(constraint) from exc
        if db.foreign_key_constraint(exc) is not None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"There is no position {body.slot_code}.",
            ) from exc
        raise
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

    detail = await get_label(label_id, user)
    warning = await _placement_warning(label_id)
    if warning:
        detail["warning"] = warning
    return detail


async def _placement_warning(label_id: str) -> str | None:
    """A label hanging somewhere other than where its product lives.

    Not refused: the layout and the shelf can legitimately disagree for a while
    (a promotion end-cap, a product mid-move). But it is almost always a slip,
    so the console says so at the moment it happens.
    """
    row = await db.fetchrow(
        """SELECT l.slot_code, p.name AS product_name, p.location_id AS home,
                  s.location_id AS hung, hp.path AS home_path
             FROM shelf_labels l
             JOIN products p ON p.product_id = l.product_id
             JOIN location_slots s ON s.slot_code = l.slot_code
             LEFT JOIN shelfiq_location_paths hp ON hp.location_id = p.location_id
            WHERE l.label_id = $1""",
        label_id,
    )
    if row is None or row["home"] is None:
        return None
    inside = await db.fetchval(
        _DESCENDANTS.format(param="$1")
        + "SELECT EXISTS (SELECT 1 FROM sub WHERE location_id = $2)",
        row["home"],
        row["hung"],
    )
    if inside:
        return None
    return (
        f"Position {row['slot_code']} is outside where {row['product_name']} "
        f"lives ({row['home_path']}). Paired anyway — move the product or the "
        "label if that is not intended."
    )


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


class BlinkBody(BaseModel):
    seconds: int = Field(default=3, ge=1, le=30)


@router.post("/{label_id}/blink")
async def blink(
    label_id: str,
    body: BlinkBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    """Ask a label to identify itself, so somebody can find it on the floor.

    **This is a console-owned topic, not a contract one.** `contracts/` carries
    no label command, and adding one needs all four signatures, so this
    publishes to `retail/label/{label_id}/blink` and the firmware may ignore it
    until the team agrees a shape. It is documented in dashboard-api/README.md
    beside the other two contract gaps.

    Published with `retain=False` deliberately: a retained blink would re-fire
    every time the device reconnects, forever.

    There is no acknowledgement, so the caller is told the message was sent or
    queued — never that the label is blinking.
    """
    label = await db.fetchrow(
        "SELECT status FROM shelf_labels WHERE label_id = $1", label_id
    )
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such label.")

    delivered = await gate.publish_label_command(
        label_id=label_id,
        command="blink",
        payload={"seconds": body.seconds},
        requested_by=user.username,
    )
    await db.execute(
        """INSERT INTO label_events (label_id, event_type, detail)
           VALUES ($1, 'blink_requested', $2)""",
        label_id,
        {"seconds": body.seconds, "by": user.username, "delivered": delivered},
    )
    bus.emit("labels", changed=[label_id], action="blink")

    return {
        "label_id": label_id,
        "published": delivered,
        "queued": bus.queued_count(),
        # Said here so no caller has to guess: the device never answers.
        "note": "No label acknowledges a command, so this reports only that the "
                "message left the service.",
    }
