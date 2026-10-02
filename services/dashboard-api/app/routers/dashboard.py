"""The Dashboard screen: store snapshot, what needs attention, performance
signals, recent changes.

Every number here is computed from a row in the database. None is a constant,
and none is rounded into a nicer-looking figure. Where there is nothing to
measure the field is null and the UI shows an em dash, because the 295A build
shipped invented metrics and that is what made the whole thing read as a
mockup.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from .. import auth, db, serialize

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    # A plain int, not Literal[7, 30, 90]: a query string is text, and a
    # Literal of ints refuses "30" rather than coercing it.
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict:
    snapshot = await db.fetchrow(
        """SELECT
             (SELECT count(*) FROM products WHERE archived_at IS NULL)        AS products,
             (SELECT count(*) FROM shelf_labels WHERE product_id IS NOT NULL) AS labels_paired,
             (SELECT count(*) FROM shelf_labels WHERE status = 'online')      AS labels_online,
             (SELECT count(*) FROM shelf_labels WHERE status = 'failed')      AS labels_failed,
             (SELECT count(*) FROM shelf_labels WHERE status = 'offline')     AS labels_offline,
             (SELECT count(*) FROM proposals WHERE status = 'pending')        AS pending,
             (SELECT count(*) FROM products
               WHERE archived_at IS NULL AND stock_on_hand = 0)               AS out_of_stock,
             (SELECT count(*) FROM products
               WHERE archived_at IS NULL AND stock_on_hand BETWEEN 1 AND 10)  AS low_stock,
             -- Margin across the catalogue, weighted by units on hand: an
             -- unweighted mean would let a single slow-moving line with a fat
             -- margin flatter the whole store.
             (SELECT round(
                  sum((current_price - unit_cost) * stock_on_hand)
                  / nullif(sum(current_price * stock_on_hand), 0) * 100, 1)
                FROM shelfiq_products
               WHERE current_price IS NOT NULL AND stock_on_hand > 0)          AS margin_pct"""
    )

    # The headline figure. Defined here and nowhere else, so the number on
    # screen has a definition somebody can argue with:
    #   the share of paired labels that are online and confirmed,
    #   reduced by the share of the catalogue waiting on a human.
    # It is a composite, which is why the response carries its parts too.
    paired = snapshot["labels_paired"] or 0
    products = snapshot["products"] or 1
    label_health = (snapshot["labels_online"] / paired) if paired else None
    queue_drag = min(1.0, (snapshot["pending"] or 0) / products)
    health = (
        round((label_health * (1 - queue_drag * 0.5)) * 100)
        if label_health is not None
        else None
    )

    signals = await _signals(days)
    attention = await _attention()
    recent = await db.fetch(
        """SELECT a.ts, a.product_id, a.product_name, a.old_price, a.new_price,
                  a.change_pct, a.approval, a.approved_by_name, a.label_id,
                  a.shelf_result, l.slot_code
             FROM shelfiq_activity a
             LEFT JOIN shelf_labels l ON l.label_id = a.label_id
            ORDER BY a.ts DESC LIMIT 6"""
    )

    return {
        "snapshot": serialize.row(snapshot),
        "pricing_health": {
            "score": health,
            # The parts, so the circle is explainable rather than magic.
            "label_health_pct": round(label_health * 100) if label_health is not None else None,
            "queue_drag_pct": round(queue_drag * 100),
            "definition": (
                "Share of paired labels that are online, reduced by the share "
                "of the catalogue waiting on a decision."
            ),
        },
        "attention": attention,
        "signals": signals,
        "recent": serialize.rows(recent),
        "range_days": days,
    }


async def _signals(days: int) -> list[dict]:
    """Three sparklines over the chosen window, all driven by the same range
    select as the design specifies.

    These are measured from price_history and the catalogue, not modelled:
      changes  — price changes that reached a shelf, per day
      margin   — catalogue margin after each day's changes
      markdown — share of that day's changes that were cuts
    The store has no point-of-sale feed in this system, so there is no revenue
    series here. Inventing one is exactly the failure this screen avoids.
    """
    buckets = await db.fetch(
        """WITH days AS (
               SELECT generate_series(
                   date_trunc('day', now()) - make_interval(days => $1 - 1),
                   date_trunc('day', now()),
                   '1 day'
               ) AS day
           ),
           changes AS (
               SELECT date_trunc('day', ts) AS day,
                      count(*) AS n,
                      count(*) FILTER (WHERE approval = 'auto') AS n_auto,
                      avg(price) AS avg_price
                 FROM price_history
                WHERE ts >= date_trunc('day', now()) - make_interval(days => $1 - 1)
                GROUP BY 1
           ),
           cuts AS (
               SELECT date_trunc('day', ts) AS day,
                      count(*) FILTER (WHERE change_pct < 0) AS n_down,
                      count(*) AS n_total,
                      avg(change_pct) AS avg_pct
                 FROM shelfiq_activity
                WHERE ts >= date_trunc('day', now()) - make_interval(days => $1 - 1)
                GROUP BY 1
           )
           SELECT d.day,
                  coalesce(c.n, 0)            AS changes,
                  coalesce(c.n_auto, 0)       AS auto_changes,
                  cu.avg_pct                  AS avg_change_pct,
                  CASE WHEN coalesce(cu.n_total, 0) > 0
                       THEN round(cu.n_down::numeric / cu.n_total * 100, 1)
                  END                         AS markdown_share_pct
             FROM days d
             LEFT JOIN changes c ON c.day = d.day
             LEFT JOIN cuts cu   ON cu.day = d.day
            ORDER BY d.day""",
        days,
    )

    def series(key: str) -> list:
        return [serialize.value(key, b[key]) for b in buckets]

    def num(value) -> float | None:
        """Coerce to a JSON number.

        Postgres NUMERIC arrives as Decimal, and pydantic v2 serialises Decimal
        to a *string*. That is right for money, which is why serialize.py does it
        deliberately — but a percentage the client will do arithmetic on has to
        be a number, and a Decimal that bypasses serialize.value() silently
        becomes "48.6" and crashes the chart. So anything built by hand here is
        coerced explicitly rather than trusted to be a float already.
        """
        return None if value is None else round(float(value), 1)

    labels = [serialize.value("day", b["day"]) for b in buckets]
    total_changes = int(sum(b["changes"] for b in buckets))
    auto_changes = int(sum(b["auto_changes"] for b in buckets))
    with_share = [
        float(b["markdown_share_pct"])
        for b in buckets
        if b["markdown_share_pct"] is not None
    ]

    return [
        {
            "key": "changes",
            "label": "Price changes",
            "value": total_changes,
            "unit": "count",
            "points": series("changes"),
            "days": labels,
            "note": (
                f"{auto_changes} applied by your standing policy"
                if total_changes
                else "No price changes in this window"
            ),
        },
        {
            "key": "delegated",
            "label": "Delegated share",
            "value": num(auto_changes / total_changes * 100) if total_changes else None,
            "unit": "percent",
            "points": series("auto_changes"),
            "days": labels,
            "note": "Changes that never needed you",
        },
        {
            "key": "markdown",
            "label": "Markdown share",
            "value": num(sum(with_share) / len(with_share)) if with_share else None,
            "unit": "percent",
            "points": series("markdown_share_pct"),
            "days": labels,
            "note": "Share of changes that were cuts",
        },
    ]


async def _attention() -> list[dict]:
    """What needs a person, most urgent first.

    Grouped by type with one action each, as the design specifies. The ordering
    is by consequence: a price that never reached a shelf is a customer seeing
    the wrong number, which beats a decision waiting in a queue.
    """
    items: list[dict] = []

    failed = await db.fetch(
        """SELECT l.label_id, l.slot_code, p.name, p.product_id
             FROM shelf_labels l
             LEFT JOIN products p ON p.product_id = l.product_id
            WHERE l.status = 'failed'
            ORDER BY l.last_seen_at NULLS FIRST LIMIT 5"""
    )
    if failed:
        items.append(
            {
                "priority": "High",
                "kind": "label_failed",
                "count": len(failed),
                "title": (
                    f"{len(failed)} shelf label{'s' if len(failed) != 1 else ''} "
                    "did not take the last price"
                ),
                "detail": ", ".join(
                    f"{r['label_id']} ({r['name'] or 'unpaired'})" for r in failed[:3]
                )
                + (" and more." if len(failed) > 3 else "."),
                "action": "Open labels",
                "route": "/labels?status=failed",
            }
        )

    pending = await db.fetchrow(
        """SELECT count(*) AS n, min(expires_at) AS soonest
             FROM proposals WHERE status = 'pending'"""
    )
    if pending and pending["n"]:
        items.append(
            {
                "priority": "High" if pending["n"] >= 5 else "Medium",
                "kind": "pending",
                "count": pending["n"],
                "title": f"{pending['n']} price suggestion"
                + ("s" if pending["n"] != 1 else "")
                + " waiting on you",
                "detail": "If one lapses undecided the shelf simply keeps its "
                "current price.",
                # The instant is carried separately rather than formatted into
                # the sentence: the viewer's timezone is a browser fact, and a
                # UTC string in prose is unreadable to the person on the floor.
                "detail_at": serialize.value("soonest", pending["soonest"]),
                "detail_at_prefix": "The earliest lapses",
                "action": "Review",
                "route": "/suggestions",
            }
        )

    # "Under two days of stock", computed from the forecaster's own units and
    # the field pack's sales rate — not from a guessed velocity.
    thin = await db.fetch(
        """SELECT p.product_id, p.name, p.stock_on_hand,
                  (f.values->>'units_per_day')::numeric AS per_day
             FROM products p
             JOIN product_fields f ON f.product_id = p.product_id
            WHERE p.archived_at IS NULL
              AND p.stock_on_hand IS NOT NULL
              AND (f.values->>'units_per_day')::numeric > 0
              AND p.stock_on_hand / (f.values->>'units_per_day')::numeric < 2
            ORDER BY p.stock_on_hand / (f.values->>'units_per_day')::numeric
            LIMIT 5"""
    )
    if thin:
        items.append(
            {
                "priority": "Medium",
                "kind": "low_stock",
                "count": len(thin),
                "title": f"{len(thin)} product{'s' if len(thin) != 1 else ''} "
                "under two days of stock",
                "detail": ", ".join(
                    f"{r['name']} ({Decimal(r['stock_on_hand']) / r['per_day']:.1f} days)"
                    for r in thin[:2]
                )
                + (" and more." if len(thin) > 2 else "."),
                "action": "View",
                "route": "/products?view=low",
            }
        )

    unexplained = await db.fetchval(
        """SELECT count(*) FROM proposals pr
            LEFT JOIN shap_vectors sv ON sv.decision_id = pr.decision_id
           WHERE pr.status = 'pending' AND sv.decision_id IS NULL"""
    )
    if unexplained:
        items.append(
            {
                "priority": "Medium",
                "kind": "unexplained",
                "count": unexplained,
                "title": f"{unexplained} suggestion"
                + ("s" if unexplained != 1 else "")
                + " arrived without an explanation",
                # A normal state, not an error — but a manager approving a price
                # whose explanation never arrived is worth surfacing as exactly
                # that, because the explanation is the basis for the decision.
                "detail": "You can still decide, but you would be deciding "
                "without the agent's reasoning.",
                "action": "Review",
                "route": "/suggestions",
            }
        )

    offline = await db.fetchval(
        "SELECT count(*) FROM shelf_labels WHERE status = 'offline'"
    )
    paired = await db.fetchval(
        "SELECT count(*) FROM shelf_labels WHERE product_id IS NOT NULL"
    )
    if offline:
        # Every paired label dark at once almost never means every label died.
        # It means nothing is reporting sightings — and since contracts/ has no
        # topic on which a label says it is alive, that is the expected state
        # until Layer 2 is connected. Saying "21 labels have gone quiet" would
        # point a manager at the shelves instead of at the gateway.
        no_gateway = paired and offline >= paired
        items.append(
            {
                "priority": "Low",
                "kind": "label_offline",
                "count": offline,
                "title": (
                    "No shelf label is reporting in"
                    if no_gateway
                    else f"{offline} label{'s' if offline != 1 else ''} have not "
                    "been seen recently"
                ),
                "detail": (
                    "Every paired label is dark at once, which normally means "
                    "the edge gateway is not running rather than that the labels "
                    "have failed. Approved prices are still published; nothing "
                    "is confirming them."
                    if no_gateway
                    else "They are showing whatever they last received, which "
                    "may no longer be the approved price."
                ),
                "action": "Open labels",
                "route": "/labels?status=offline",
            }
        )

    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(items, key=lambda i: order[i["priority"]])
