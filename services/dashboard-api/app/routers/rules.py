"""Pricing rules: store-wide change limits and the per-category table.

Two layers, and the distinction matters:

  products.min_price / max_price / auto_approve_pct  — the per-SKU policy, and
      the only one the MQTT contract carries (contracts/guardrail.schema.json).
  category_rules                                      — the manager's defaults
      and a margin floor the per-SKU contract has no field for.

Saving a category rule therefore republishes the guardrail for every SKU in
that category, because otherwise the agent would keep proposing against a
policy the manager has already changed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import auth, bus, db, gate, serialize

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("")
async def get_rules(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    limits = await db.fetchrow("SELECT * FROM change_limits LIMIT 1")
    categories = await db.fetch(
        """SELECT cr.*, counts.n AS product_count
             FROM category_rules cr
             LEFT JOIN LATERAL (
                  SELECT count(*) AS n FROM products
                   WHERE category = cr.category AND archived_at IS NULL
             ) counts ON TRUE
            ORDER BY cr.category"""
    )
    # Categories that exist in the catalogue but have no rule yet: better to
    # show them as unconfigured than to leave a product silently ungoverned.
    unruled = await db.fetch(
        """SELECT category, count(*) AS product_count
             FROM products
            WHERE archived_at IS NULL AND category IS NOT NULL
              AND category NOT IN (SELECT category FROM category_rules)
            GROUP BY category ORDER BY category"""
    )
    return {
        "limits": serialize.row(limits) if limits else None,
        "categories": serialize.rows(categories),
        "unconfigured": serialize.rows(unruled),
    }


class LimitsBody(BaseModel):
    max_change_pct: Decimal = Field(ge=0, le=100)
    max_changes_per_day: int = Field(ge=0, le=100)
    min_hours_between: int = Field(ge=0, le=168)
    proposal_ttl_hours: int = Field(ge=1, le=168)


@router.put("/limits")
async def put_limits(
    body: LimitsBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    row = await db.fetchrow(
        """UPDATE change_limits
              SET max_change_pct = $1, max_changes_per_day = $2,
                  min_hours_between = $3, proposal_ttl_hours = $4,
                  set_by = $5, updated_at = now()
            WHERE id
        RETURNING *""",
        body.max_change_pct,
        body.max_changes_per_day,
        body.min_hours_between,
        body.proposal_ttl_hours,
        user.username,
    )
    await db.execute(
        """INSERT INTO agent_audit_log (event_type, actor, detail)
           VALUES ('guardrail_set', $1, $2)""",
        user.username,
        {
            "scope": "store",
            "max_change_pct": float(body.max_change_pct),
            "proposal_ttl_hours": body.proposal_ttl_hours,
        },
    )
    bus.emit("rules", scope="limits", set_by=user.username)
    return {"limits": serialize.row(row) if row else None}


class CategoryRuleBody(BaseModel):
    ceiling_pct: Decimal | None = Field(default=None, ge=0, le=1000)
    min_margin_pct: Decimal = Field(ge=0, le=100)
    agent_may_suggest: bool = True
    # Off by default, on purpose. Writing `agent_excluded = NOT agent_may_suggest`
    # across a category destroys per-SKU exclusions a manager set by hand on
    # Product detail, and there is no undo. The UI ticks this explicitly, with
    # the count of products it would change spelled out beside it.
    apply_agent_flag: bool = False


# One expression, used by both the preview and the save, so the figures a
# manager is shown are produced by the same arithmetic that then runs.
_DERIVE = """
    SELECT p.product_id, p.name, p.unit_cost, p.min_price, p.max_price,
           p.auto_approve_pct, p.agent_excluded,
           h.price AS current_price,
           round(p.unit_cost / (1 - $2::numeric / 100), 2) AS new_min_price,
           CASE WHEN $3::numeric IS NULL THEN p.max_price
                ELSE round(coalesce(h.price, p.max_price, p.unit_cost)
                           * (1 + $3::numeric / 100), 2)
           END AS new_max_price
      FROM products p
      LEFT JOIN LATERAL (
           SELECT price FROM price_history
            WHERE product_id = p.product_id ORDER BY ts DESC LIMIT 1
      ) h ON TRUE
     WHERE p.category = $1 AND p.archived_at IS NULL
     ORDER BY p.product_id
"""


@router.get("/categories/{category}/preview")
async def preview_category_rule(
    category: str,
    _user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
    min_margin_pct: Decimal = Query(ge=0, le=100),
    ceiling_pct: Decimal | None = Query(default=None, ge=0, le=1000),
    agent_may_suggest: bool = True,
    apply_agent_flag: bool = False,
) -> dict:
    """What saving this rule would change, before it changes anything.

    A category save rewrites `min_price` and `max_price` on every SKU in the
    category and republishes a retained guardrail for each. That is a large,
    irreversible blast radius for a form with two percentage boxes, so the
    numbers are available first. Three examples are enough to catch a decimal
    point in the wrong place; the counts carry the rest.
    """
    rows = await db.fetch(_DERIVE, category, min_margin_pct, ceiling_pct)

    with_price = sum(1 for r in rows if r["current_price"] is not None)
    min_changes = sum(1 for r in rows if r["min_price"] != r["new_min_price"])
    max_changes = sum(1 for r in rows if r["max_price"] != r["new_max_price"])
    excluded = sum(1 for r in rows if r["agent_excluded"])
    would_publish = sum(
        1 for r in rows if r["new_min_price"] is not None and r["new_max_price"] is not None
    )

    examples = [
        {
            "product_id": r["product_id"],
            "name": r["name"],
            "min_price": serialize.value("min_price", r["min_price"]),
            "new_min_price": serialize.value("min_price", r["new_min_price"]),
            "max_price": serialize.value("max_price", r["max_price"]),
            "new_max_price": serialize.value("max_price", r["new_max_price"]),
        }
        for r in rows
        if r["min_price"] != r["new_min_price"] or r["max_price"] != r["new_max_price"]
    ][:3]

    return {
        "category": category,
        "products": len(rows),
        "with_price": with_price,
        "without_price": len(rows) - with_price,
        "would_publish": would_publish,
        "skipped_no_band": len(rows) - would_publish,
        "min_price_changes": min_changes,
        "max_price_changes": max_changes,
        "individually_excluded": excluded,
        # Only meaningful when the flag is being applied; otherwise per-SKU
        # exclusions are left exactly as they are.
        "would_unexclude": (excluded if apply_agent_flag and agent_may_suggest else 0),
        "would_exclude": (
            len(rows) - excluded if apply_agent_flag and not agent_may_suggest else 0
        ),
        "applies_agent_flag": apply_agent_flag,
        "examples": examples,
    }


@router.put("/categories/{category}")
async def put_category_rule(
    category: str,
    body: CategoryRuleBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    if body.min_margin_pct >= 100:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A 100% margin floor would put every floor price at infinity.",
        )

    # The upsert and the per-SKU re-derivation are the two facts that must
    # agree; they go in one transaction. The publishing happens after it
    # commits, because publish_guardrail opens its own connection and holding
    # two pool connections at once is how a saturated pool deadlocks.
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO category_rules
                       (category, ceiling_pct, min_margin_pct,
                        agent_may_suggest, set_by, updated_at)
                   VALUES ($1, $2, $3, $4, $5, now())
                   ON CONFLICT (category) DO UPDATE
                      SET ceiling_pct = EXCLUDED.ceiling_pct,
                          min_margin_pct = EXCLUDED.min_margin_pct,
                          agent_may_suggest = EXCLUDED.agent_may_suggest,
                          set_by = EXCLUDED.set_by,
                          updated_at = now()
                RETURNING *""",
                category,
                body.ceiling_pct,
                body.min_margin_pct,
                body.agent_may_suggest,
                user.username,
            )

            agent_clause = (
                ", agent_excluded = NOT $4::boolean" if body.apply_agent_flag else ""
            )
            affected = await conn.fetch(
                f"""UPDATE products p
                       SET min_price = round(p.unit_cost / (1 - $2::numeric / 100), 2),
                           max_price = CASE
                               WHEN $3::numeric IS NULL THEN p.max_price
                               ELSE round(coalesce(h.price, p.max_price, p.unit_cost)
                                          * (1 + $3::numeric / 100), 2)
                           END{agent_clause},
                           policy_set_by = $5,
                           policy_set_at = now(),
                           updated_at = now()
                      FROM (SELECT product_id, (SELECT price FROM price_history
                                                 WHERE product_id = pr.product_id
                                                 ORDER BY ts DESC LIMIT 1) AS price
                              FROM products pr
                             WHERE pr.category = $1 AND pr.archived_at IS NULL) h
                     WHERE p.product_id = h.product_id
                 RETURNING p.product_id, p.min_price, p.max_price, p.auto_approve_pct""",
                category,
                body.min_margin_pct,
                body.ceiling_pct,
                body.agent_may_suggest,
                user.username,
            )

    published, queued = 0, 0
    for r in affected:
        if r["min_price"] is None or r["max_price"] is None:
            continue
        ok = await gate.publish_guardrail(
            product_id=r["product_id"],
            min_price=r["min_price"],
            max_price=r["max_price"],
            auto_approve_pct=r["auto_approve_pct"],
            set_by=user.username,
            # One event for the whole save, below, rather than one per SKU.
            quiet=True,
        )
        published += 1 if ok else 0
        queued += 0 if ok else 1

    bus.emit(
        "guardrail",
        category=category,
        products=len(affected),
        published=published,
        queued=queued,
        set_by=user.username,
    )
    bus.emit("rules", scope="category", category=category, set_by=user.username)
    return {
        "rule": serialize.row(row) if row else None,
        "republished": {
            "products": len(affected),
            "published": published,
            "queued": queued,
        },
    }
