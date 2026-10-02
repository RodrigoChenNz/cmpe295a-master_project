"""The review queue — approve, override, reject.

This is the product. Everything else in the console supports it.

Three things here are load-bearing rather than incidental:

1. `decided_by` comes from the session and from nowhere else.
2. An approval is a single transaction: decide the proposal, then publish. A
   second approval of the same proposal finds it no longer pending and is
   refused, so a double-click cannot move a shelf price twice.
3. An override records what the agent *wanted* alongside what shipped, because
   the attached explanation explains the former and the contract is explicit
   that it must never be presented as explaining the latter.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import auth, bus, db, gate, serialize

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


@router.get("")
async def list_suggestions(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
    category: str | None = None,
    direction: Literal["any", "up", "down"] = "any",
    size: Literal["any", "small", "large"] = "any",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    clauses: list[str] = []
    params: list = []

    if q:
        params.append(f"%{q}%")
        clauses.append(f"(name ILIKE ${len(params)} OR sku ILIKE ${len(params)})")
    if category and category != "All categories":
        params.append(category)
        clauses.append(f"category = ${len(params)}")
    if direction == "up":
        clauses.append("delta > 0")
    elif direction == "down":
        clauses.append("delta < 0")
    # "Small" is the 5% the seed delegates by default, so the two filters line
    # up with the policy rather than with an arbitrary round number.
    if size == "small":
        clauses.append("abs(delta_pct) <= 5")
    elif size == "large":
        clauses.append("abs(delta_pct) > 5")

    where = " AND ".join(clauses) if clauses else "TRUE"

    total = await db.fetchval(
        f"SELECT count(*) FROM shelfiq_review_queue WHERE {where}", *params
    )
    pages = max(1, -(-total // page_size))
    page_no = min(page, pages)

    rows = await db.fetch(
        f"""SELECT * FROM shelfiq_review_queue WHERE {where}
             ORDER BY expires_at ASC
             LIMIT {page_size} OFFSET {(page_no - 1) * page_size}""",
        *params,
    )

    out = []
    for r in rows:
        item = serialize.row(r)
        item["why"] = gate.compose_reason(
            reason_tag=r["reason_tag"],
            rationale=r["rationale"],
            shap=r["shap"],
            product_name=r["name"],
            stock_on_hand=r["stock_on_hand"],
        )
        # Say plainly whether the agent reported a confidence, rather than
        # rendering an empty bar that reads as zero.
        item["confidence_reported"] = r["agent_confidence"] is not None
        item["shelf_render"] = dict(
            zip(
                ("line1", "line2"),
                gate.render_lines(
                    r["display_name"] or r["name"],
                    r["proposed_price"],
                    r["reason_tag"],
                ),
            )
        )
        out.append(item)

    return {
        "rows": out,
        "total": total,
        "page": page_no,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/{decision_id}")
async def get_suggestion(
    decision_id: str,
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    row = await db.fetchrow(
        """SELECT q.*, p.name AS product_name
             FROM shelfiq_review_queue q
             JOIN products p ON p.product_id = q.product_id
            WHERE q.decision_id = $1""",
        decision_id,
    )
    if row is None:
        # Either it never existed or someone else just decided it. Both are
        # the same thing from here: it is not in the queue any more.
        decided = await db.fetchrow(
            """SELECT status, decided_by, decided_at, final_price
                 FROM proposals WHERE decision_id = $1""",
            decision_id,
        )
        if decided:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This suggestion was already {decided['status']}"
                + (f" by {decided['decided_by']}." if decided["decided_by"] else "."),
            )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such suggestion.")

    history = await db.fetch(
        """SELECT price, ts, reason_tag, approval, approved_by
             FROM price_history WHERE product_id = $1
            ORDER BY ts DESC LIMIT 90""",
        row["product_id"],
    )
    earlier = await db.fetch(
        """SELECT decision_id, status, proposed_price, final_price, reason_tag,
                  decided_by, decided_at, ts
             FROM proposals
            WHERE product_id = $1 AND decision_id <> $2 AND status <> 'pending'
            ORDER BY ts DESC LIMIT 8""",
        row["product_id"],
        decision_id,
    )
    rules = await db.fetchrow(
        "SELECT * FROM category_rules WHERE category = $1", row["category"]
    )

    item = serialize.row(row)
    item["why"] = gate.compose_reason(
        reason_tag=row["reason_tag"],
        rationale=row["rationale"],
        shap=row["shap"],
        product_name=row["name"],
        stock_on_hand=row["stock_on_hand"],
    )
    item["confidence_reported"] = row["agent_confidence"] is not None
    line1, line2 = gate.render_lines(
        row["display_name"] or row["name"], row["proposed_price"], row["reason_tag"]
    )
    item["shelf_render"] = {"line1": line1, "line2": line2}

    return {
        "suggestion": item,
        "price_history": serialize.rows(list(reversed(history))),
        "earlier_decisions": serialize.rows(earlier),
        "category_rules": serialize.row(rules) if rules else None,
    }


class ApproveBody(BaseModel):
    # Omit to take the agent's price as-is; supply one to override it.
    price: Decimal | None = Field(default=None, gt=0, decimal_places=2)


@router.post("/{decision_id}/approve")
async def approve(
    decision_id: str,
    body: ApproveBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    # Claim the proposal first. The WHERE status = 'pending' is the lock: two
    # managers clicking Approve at the same instant means exactly one UPDATE
    # matches a row, and the other gets the 409 below.
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT pr.product_id, pr.proposed_price, pr.current_price,
                          pr.reason_tag, pr.agent_version,
                          p.min_price, p.max_price
                     FROM proposals pr
                     JOIN products p ON p.product_id = pr.product_id
                    WHERE pr.decision_id = $1
                      FOR UPDATE OF pr""",
                decision_id,
            )
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such suggestion.")

            final = body.price if body.price is not None else row["proposed_price"]
            overridden = body.price is not None and body.price != row["proposed_price"]

            # "Manual prices are still checked against your guardrails."
            lo, hi = row["min_price"], row["max_price"]
            if lo is not None and final < lo:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"${final} is below this product's ${lo} floor.",
                )
            if hi is not None and final > hi:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"${final} is above this product's ${hi} ceiling.",
                )

            claimed = await conn.execute(
                """UPDATE proposals
                      SET status = $2, decided_by = $3, decided_at = now(),
                          final_price = $4
                    WHERE decision_id = $1 AND status = 'pending'""",
                decision_id,
                "overridden" if overridden else "approved",
                user.username,
                final,
            )
            if claimed.endswith(" 0"):
                existing = await conn.fetchrow(
                    "SELECT status, decided_by FROM proposals WHERE decision_id = $1",
                    decision_id,
                )
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Already {existing['status']}"
                    + (f" by {existing['decided_by']}." if existing["decided_by"] else "."),
                )

            await conn.execute(
                """INSERT INTO agent_audit_log
                       (event_type, product_id, decision_id, actor, detail, ts)
                   VALUES ($1, $2, $3, $4, $5, now())""",
                "proposal_overridden" if overridden else "proposal_approved",
                row["product_id"],
                decision_id,
                user.username,
                {
                    "final_price": float(final),
                    "agent_proposed": float(row["proposed_price"]),
                },
            )

    result = await gate.publish_price(
        product_id=row["product_id"],
        price=final,
        reason_tag=row["reason_tag"],
        approval="override" if overridden else "manager",
        approved_by=user.username,
        decision_id=decision_id,
        agent_version=row["agent_version"],
    )

    bus.emit(
        "suggestions",
        decided=[decision_id],
        product_id=row["product_id"],
        status="overridden" if overridden else "approved",
    )

    return {
        "decision_id": decision_id,
        "product_id": row["product_id"],
        "status": "overridden" if overridden else "approved",
        "final_price": str(final),
        "agent_proposed": str(row["proposed_price"]),
        # When this is false the price is approved and audited but has not left
        # the building, and the UI says "queued" rather than "on the shelf".
        "published": result["published"],
        "queued": result["queued"],
    }


class RejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=400)


@router.post("/{decision_id}/reject")
async def reject(
    decision_id: str,
    body: RejectBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Decline a proposal. The shelf keeps its current price and nothing is
    published — a rejection is a decision about a recommendation, not a price
    change, so it belongs in the audit log and not in price_history."""
    row = await db.fetchrow(
        "SELECT product_id FROM proposals WHERE decision_id = $1", decision_id
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such suggestion.")

    claimed = await db.execute(
        """UPDATE proposals
              SET status = 'rejected', decided_by = $2, decided_at = now(),
                  decline_reason = $3
            WHERE decision_id = $1 AND status = 'pending'""",
        decision_id,
        user.username,
        body.reason,
    )
    if claimed.endswith(" 0"):
        existing = await db.fetchrow(
            "SELECT status, decided_by FROM proposals WHERE decision_id = $1",
            decision_id,
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Already {existing['status']}."
        )

    await db.execute(
        """INSERT INTO agent_audit_log
               (event_type, product_id, decision_id, actor, detail, ts)
           VALUES ('proposal_rejected', $1, $2, $3, $4, now())""",
        row["product_id"],
        decision_id,
        user.username,
        {"reason": body.reason},
    )
    bus.emit("suggestions", decided=[decision_id],
             product_id=row["product_id"], status="rejected")

    return {"decision_id": decision_id, "status": "rejected"}


class BulkBody(BaseModel):
    decision_ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/bulk/approve")
async def bulk_approve(
    body: BulkBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Approve several at their suggested prices.

    Each one is its own transaction and its own publish, so a single refusal
    (someone else decided it, or it falls outside its band) does not take the
    rest of the batch down with it. The response names every failure, because
    "12 approved" when one silently did not is the kind of report that gets a
    price wrong on a shelf.
    """
    approved, failed = [], []
    for decision_id in body.decision_ids:
        try:
            result = await approve(decision_id, ApproveBody(), user)
            approved.append(result)
        except HTTPException as exc:
            failed.append({"decision_id": decision_id, "error": exc.detail})
    return {
        "approved": approved,
        "failed": failed,
        "counts": {"approved": len(approved), "failed": len(failed)},
    }


@router.post("/bulk/reject")
async def bulk_reject(
    body: BulkBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    rejected, failed = [], []
    for decision_id in body.decision_ids:
        try:
            rejected.append(await reject(decision_id, RejectBody(), user))
        except HTTPException as exc:
            failed.append({"decision_id": decision_id, "error": exc.detail})
    return {
        "rejected": rejected,
        "failed": failed,
        "counts": {"rejected": len(rejected), "failed": len(failed)},
    }
