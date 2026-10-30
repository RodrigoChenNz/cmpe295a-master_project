"""The store's category list — what the Add product form offers.

Chosen during onboarding from the store type's suggestions, and edited on
Settings afterwards. Saved as a whole list rather than row by row, because the
order is part of what the manager is saying.

A product's category is still free text on `products.category`. Removing a
name here never rewrites a product; it only stops being suggested. Pricing
rules for a category live in `category_rules` (see rules.py) and are a separate
decision.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import auth, bus, db

router = APIRouter(prefix="/api/categories", tags=["categories"])

MAX_CATEGORIES = 100


@router.get("")
async def list_categories(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    rows = await db.fetch(
        """SELECT sc.name, count(p.product_id) AS product_count
             FROM store_categories sc
             LEFT JOIN products p
                    ON p.category = sc.name AND p.archived_at IS NULL
            GROUP BY sc.name, sc.sort_order
            ORDER BY sc.sort_order, sc.name"""
    )
    return {"categories": [dict(r) for r in rows]}


class CategoriesBody(BaseModel):
    names: list[str] = Field(max_length=MAX_CATEGORIES)


@router.put("")
async def save_categories(
    body: CategoriesBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    names = [n.strip() for n in body.names]
    if any(not n or len(n) > 60 for n in names):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Each category needs a name of 1–60 characters."
        )
    if len({n.lower() for n in names}) != len(names):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Two categories cannot share a name."
        )

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM store_categories")
            await conn.executemany(
                "INSERT INTO store_categories (name, sort_order) VALUES ($1, $2)",
                [(name, i) for i, name in enumerate(names, start=1)],
            )
            await conn.execute(
                """INSERT INTO agent_audit_log (event_type, actor, detail)
                   VALUES ('guardrail_set', $1, $2)""",
                user.username,
                {"scope": "categories", "categories": names},
            )

    bus.emit("categories", set_by=user.username)
    return await list_categories(user)
