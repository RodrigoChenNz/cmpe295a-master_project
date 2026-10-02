"""The activity log — a time-first view of every price that reached a shelf.

It is its own screen because of how the task starts: a customer disputes a
price they saw on Tuesday and nobody knows the SKU yet. That is a query the
per-product log cannot answer, and it is the only place where decisions across
the whole catalogue line up in time.
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from .. import auth, db, serialize

router = APIRouter(prefix="/api/activity", tags=["activity"])

# Day counts, not interval strings: asyncpg types a `$1::interval` parameter as
# an interval and wants a timedelta for it, so a string silently becomes a 500.
# make_interval(days => $n) takes a plain integer and reads better anyway.
RANGES = {"today": 1, "7d": 7, "30d": 30, "90d": 90}


def _filters(
    q: str | None,
    range_key: str,
    decision: str,
    shelf: str,
) -> tuple[str, list]:
    clauses, params = [], []

    if range_key in RANGES:
        params.append(RANGES[range_key])
        clauses.append(f"ts >= now() - make_interval(days => ${len(params)})")

    if q:
        params.append(f"%{q}%")
        clauses.append(
            f"(product_name ILIKE ${len(params)} OR product_id ILIKE ${len(params)}"
            f" OR label_id ILIKE ${len(params)})"
        )

    # The design's "Approved as suggested" / "Approved at edited price" map
    # exactly onto the contract's approval values, so nothing is re-derived.
    if decision == "suggested":
        clauses.append("approval = 'manager' AND decision_id IS NOT NULL")
    elif decision == "edited":
        clauses.append("approval = 'override'")
    elif decision == "auto":
        clauses.append("approval = 'auto'")
    elif decision == "manual":
        clauses.append("decision_id IS NULL")

    if shelf == "confirmed":
        clauses.append("shelf_result = 'confirmed'")
    elif shelf == "pending":
        clauses.append("shelf_result = 'pending'")
    elif shelf == "failed":
        clauses.append("shelf_result = 'failed'")
    elif shelf == "none":
        clauses.append("shelf_result IS NULL")

    return (" AND ".join(clauses) if clauses else "TRUE"), params


@router.get("")
async def list_activity(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
    range: Literal["today", "7d", "30d", "90d", "all"] = "7d",
    decision: Literal["all", "suggested", "edited", "auto", "manual"] = "all",
    shelf: Literal["all", "confirmed", "pending", "failed", "none"] = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
) -> dict:
    where, params = _filters(q, range, decision, shelf)

    total = await db.fetchval(
        f"SELECT count(*) FROM shelfiq_activity WHERE {where}", *params
    )
    pages = max(1, -(-total // page_size))
    page_no = min(page, pages)

    rows = await db.fetch(
        f"""SELECT * FROM shelfiq_activity WHERE {where}
             ORDER BY ts DESC, id DESC
             LIMIT {page_size} OFFSET {(page_no - 1) * page_size}""",
        *params,
    )
    return serialize.page(rows, total, page_no, page_size)


@router.get("/export.csv")
async def export_csv(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
    range: Literal["today", "7d", "30d", "90d", "all"] = "30d",
    decision: Literal["all", "suggested", "edited", "auto", "manual"] = "all",
    shelf: Literal["all", "confirmed", "pending", "failed", "none"] = "all",
) -> StreamingResponse:
    """The compliance export. Same filters as the screen, so what somebody
    downloads is what they were looking at."""
    where, params = _filters(q, range, decision, shelf)
    rows = await db.fetch(
        f"""SELECT ts, product_id, product_name, category, old_price, new_price,
                   change_pct, reason_tag, approval, approved_by, approved_by_name,
                   decision_id, agent_version, label_id, shelf_result
              FROM shelfiq_activity WHERE {where}
             ORDER BY ts DESC""",
        *params,
    )

    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: serialize.value(k, v) for k, v in r.items()})
    else:
        buf.write("no rows matched these filters\n")

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="shelfiq-activity.csv"'},
    )


@router.get("/audit")
async def audit_log(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    product_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    """The raw append-only audit trail, including the events that never touch a
    price: guardrail_set, proposal_rejected, agent_started. This is what makes
    the auditability claim checkable rather than asserted."""
    clauses, params = [], []
    if product_id:
        params.append(product_id)
        clauses.append(f"product_id = ${len(params)}")
    where = " AND ".join(clauses) if clauses else "TRUE"

    total = await db.fetchval(
        f"SELECT count(*) FROM agent_audit_log WHERE {where}", *params
    )
    pages = max(1, -(-total // page_size))
    page_no = min(page, pages)
    rows = await db.fetch(
        f"""SELECT id, event_type, product_id, decision_id, actor, detail, ts
              FROM agent_audit_log WHERE {where}
             ORDER BY ts DESC, id DESC
             LIMIT {page_size} OFFSET {(page_no - 1) * page_size}""",
        *params,
    )
    return serialize.page(rows, total, page_no, page_size)
