"""Products: the catalogue list, its filter-panel counts, and the detail screen.

The filtering rule is the handoff's, implemented once here rather than twice:

    search query, then the panel filters (category OR, stock-health OR,
    price-review OR; the three groups combine with AND), then the saved view.

Panel counts are computed against the *search-filtered* catalogue, so ticking a
category never changes the numbers beside the other categories.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from .. import auth, bus, db, gate, serialize

router = APIRouter(prefix="/api/products", tags=["products"])

SORTABLE = {
    "name": "name",
    "sku": "sku",
    "category": "category",
    "price": "current_price",
    "margin": "margin_pct",
    "stock": "stock_on_hand",
    "supplier": "supplier",
}

StockHealth = Literal["in", "low", "out"]
PriceReview = Literal["review", "up", "down"]
SavedView = Literal["all", "low", "review"]


def _where(
    q: str | None,
    categories: list[str],
    stock: list[str],
    review: list[str],
    view: str,
) -> tuple[str, list]:
    """Build the WHERE clause and its parameters.

    Everything is a bound parameter — the only strings interpolated into SQL
    anywhere in this service are the sort column and direction, and both are
    looked up in a whitelist before they get near a query.
    """
    clauses: list[str] = []
    params: list = []

    def add(sql: str, *vals) -> None:
        nonlocal clauses, params
        start = len(params) + 1
        clauses.append(sql.format(*[f"${start + i}" for i in range(len(vals))]))
        params.extend(vals)

    if q:
        # Name, SKU, UPC, brand and supplier, because a manager with a delivery
        # note in hand is as likely to search an article code as a name.
        add(
            "(name ILIKE {0} OR sku ILIKE {0} OR upc ILIKE {0}"
            " OR brand ILIKE {0} OR supplier ILIKE {0})",
            f"%{q}%",
        )
    if categories:
        add("category = ANY({0})", categories)
    if stock:
        add("stock_health = ANY({0})", stock)
    if review:
        parts = []
        if "review" in review:
            parts.append("pending_count > 0")
        if "up" in review:
            parts.append("last_change_dir = 'up'")
        if "down" in review:
            parts.append("last_change_dir = 'down'")
        clauses.append("(" + " OR ".join(parts) + ")")

    # The saved view is a last AND on top of the panel filters, which is why a
    # view and a conflicting filter can legitimately produce an empty list.
    if view == "low":
        clauses.append("stock_health IN ('low', 'out')")
    elif view == "review":
        clauses.append("pending_count > 0")

    return (" AND ".join(clauses) if clauses else "TRUE"), params


@router.get("")
async def list_products(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
    category: Annotated[list[str], Query()] = [],
    stock: Annotated[list[StockHealth], Query()] = [],
    review: Annotated[list[PriceReview], Query()] = [],
    view: SavedView = "all",
    sort: str = "name",
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

    where, params = _where(q, list(category), list(stock), list(review), view)

    total = await db.fetchval(
        f"SELECT count(*) FROM shelfiq_products WHERE {where}", *params
    )

    # Clamp rather than return an empty page: when a filter shrinks the list
    # under the current page, the handoff says to land on the last page that
    # exists, and doing it here keeps every client consistent.
    pages = max(1, -(-total // page_size))
    page_no = min(page, pages)
    offset = (page_no - 1) * page_size

    # NULLS LAST on every sort, so products with no price or no stock count
    # collect at the end instead of leading an ascending list.
    rows = await db.fetch(
        f"""SELECT * FROM shelfiq_products
             WHERE {where}
             ORDER BY {column} {direction} NULLS LAST, product_id ASC
             LIMIT {page_size} OFFSET {offset}""",
        *params,
    )

    return serialize.page(rows, total, page_no, page_size)


@router.get("/facets")
async def facets(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
    q: str | None = None,
) -> dict:
    """Counts for the filter panel, against the search-filtered catalogue only.

    Deliberately NOT narrowed by the panel's own selections: a count that moves
    when you tick the box beside it makes the panel unreadable.
    """
    where, params = _where(q, [], [], [], "all")

    categories = await db.fetch(
        f"""SELECT category, count(*) AS count
              FROM shelfiq_products WHERE {where} AND category IS NOT NULL
             GROUP BY category ORDER BY category""",
        *params,
    )
    health = await db.fetch(
        f"""SELECT stock_health AS key, count(*) AS count
              FROM shelfiq_products WHERE {where} AND stock_health IS NOT NULL
             GROUP BY stock_health""",
        *params,
    )
    counts = await db.fetchrow(
        f"""SELECT
              count(*)                                              AS all_products,
              count(*) FILTER (WHERE pending_count > 0)              AS needs_review,
              count(*) FILTER (WHERE last_change_dir = 'up')         AS price_up,
              count(*) FILTER (WHERE last_change_dir = 'down')       AS price_down,
              count(*) FILTER (WHERE stock_health IN ('low','out'))  AS low_stock
             FROM shelfiq_products WHERE {where}""",
        *params,
    )

    return {
        "categories": [dict(r) for r in categories],
        "stock": {r["key"]: r["count"] for r in health},
        "views": {
            "all": counts["all_products"],
            "low": counts["low_stock"],
            "review": counts["needs_review"],
        },
        "review": {
            "review": counts["needs_review"],
            "up": counts["price_up"],
            "down": counts["price_down"],
        },
        "total": counts["all_products"],
    }


@router.get("/{product_id}")
async def get_product(
    product_id: str,
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    product = await db.fetchrow(
        "SELECT * FROM shelfiq_products WHERE product_id = $1", product_id
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such product.")

    fields = await db.fetchrow(
        "SELECT pack, values FROM product_fields WHERE product_id = $1", product_id
    )
    history = await db.fetch(
        """SELECT price, ts, reason_tag, approval, approved_by, decision_id
             FROM price_history WHERE product_id = $1
            ORDER BY ts DESC LIMIT 90""",
        product_id,
    )
    decisions = await db.fetch(
        """SELECT decision_id, proposed_price, current_price, final_price, status,
                  reason_tag, rationale, agent_confidence, decided_by, decided_at, ts
             FROM proposals WHERE product_id = $1
            ORDER BY ts DESC LIMIT 20""",
        product_id,
    )
    label = None
    if product["label_id"]:
        label = await db.fetchrow(
            """SELECT label_id, kind, status, slot_code, last_seen_at, rssi_dbm,
                      battery_pct, firmware
                 FROM shelf_labels WHERE label_id = $1""",
            product["label_id"],
        )

    # The exact strings the firmware will cut at 16 characters, computed by the
    # same function that publishes them, so the preview cannot drift from them.
    line1, line2 = gate.render_lines(
        product["display_name"] or product["name"],
        product["current_price"] or Decimal("0"),
        (history[0]["reason_tag"] if history else "") or "",
    )

    return {
        "product": serialize.row(product),
        "fields": serialize.row(fields) if fields else None,
        "price_history": serialize.rows(list(reversed(history))),
        "decisions": serialize.rows(decisions),
        "label": serialize.row(label) if label else None,
        "shelf_render": {"line1": line1, "line2": line2},
    }


class ProductBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=16)
    sku: str | None = Field(default=None, max_length=40)
    upc: str | None = Field(default=None, pattern=r"^[0-9]{8,14}$")
    brand: str | None = Field(default=None, max_length=120)
    supplier: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=60)
    unit_cost: Decimal = Field(ge=0, decimal_places=2)
    stock_on_hand: int | None = Field(default=None, ge=0)
    min_price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    max_price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    auto_approve_pct: Decimal | None = Field(default=None, ge=0, le=100)
    agent_excluded: bool = False
    image_url: str | None = Field(default=None, max_length=500)

    @field_validator("display_name")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        # LCDs have no lower case in the default character ROM, so storing it
        # would promise something the hardware cannot show.
        return v.upper() if v else v


class NewProductBody(ProductBody):
    product_id: str = Field(pattern=r"^SKU-[0-9]{3,}$")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_product(
    body: NewProductBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    if body.min_price and body.max_price and body.min_price > body.max_price:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Minimum price cannot exceed the maximum."
        )
    exists = await db.fetchval(
        "SELECT 1 FROM products WHERE product_id = $1", body.product_id
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{body.product_id} is already in the catalogue."
        )

    await db.execute(
        """INSERT INTO products
               (product_id, name, display_name, sku, upc, brand, supplier, category,
                unit_cost, stock_on_hand, min_price, max_price, auto_approve_pct,
                agent_excluded, image_url, policy_set_by, policy_set_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16, now())""",
        body.product_id, body.name, body.display_name, body.sku, body.upc,
        body.brand, body.supplier, body.category, body.unit_cost,
        body.stock_on_hand, body.min_price, body.max_price, body.auto_approve_pct,
        body.agent_excluded, body.image_url, user.username,
    )

    if body.min_price and body.max_price:
        await gate.publish_guardrail(
            product_id=body.product_id,
            min_price=body.min_price,
            max_price=body.max_price,
            auto_approve_pct=body.auto_approve_pct,
            set_by=user.username,
        )

    bus.emit("products", changed=[body.product_id], action="created")
    return await get_product(body.product_id, user)


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    body: ProductBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    if body.min_price and body.max_price and body.min_price > body.max_price:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Minimum price cannot exceed the maximum."
        )
    before = await db.fetchrow(
        """SELECT min_price, max_price, auto_approve_pct
             FROM products WHERE product_id = $1""",
        product_id,
    )
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such product.")

    policy_changed = (
        before["min_price"] != body.min_price
        or before["max_price"] != body.max_price
        or before["auto_approve_pct"] != body.auto_approve_pct
    )

    await db.execute(
        """UPDATE products
              SET name = $2, display_name = $3, sku = $4, upc = $5, brand = $6,
                  supplier = $7, category = $8, unit_cost = $9, stock_on_hand = $10,
                  min_price = $11, max_price = $12, auto_approve_pct = $13,
                  agent_excluded = $14, image_url = $15,
                  policy_set_by = CASE WHEN $16 THEN $17 ELSE policy_set_by END,
                  policy_set_at = CASE WHEN $16 THEN now() ELSE policy_set_at END,
                  updated_at = now()
            WHERE product_id = $1""",
        product_id, body.name, body.display_name, body.sku, body.upc, body.brand,
        body.supplier, body.category, body.unit_cost, body.stock_on_hand,
        body.min_price, body.max_price, body.auto_approve_pct, body.agent_excluded,
        body.image_url, policy_changed, user.username,
    )

    # Only publish a guardrail when the policy actually moved. Republishing a
    # retained message on every name edit would make the agent's view of policy
    # changes meaningless as a signal.
    if policy_changed and body.min_price and body.max_price:
        await gate.publish_guardrail(
            product_id=product_id,
            min_price=body.min_price,
            max_price=body.max_price,
            auto_approve_pct=body.auto_approve_pct,
            set_by=user.username,
        )

    bus.emit("products", changed=[product_id], action="updated")
    return await get_product(product_id, user)


class PriceBody(BaseModel):
    price: Decimal = Field(gt=0, decimal_places=2)
    reason_tag: str = Field(min_length=1, max_length=16)


@router.post("/{product_id}/price")
async def set_price(
    product_id: str,
    body: PriceBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """A manager-originated price, with no proposal behind it.

    `decision_id` is null and there is no explanation, by design — the contract
    calls this out as one of the two cases that break the join chain, and the
    UI must say "no explanation" rather than show a stale one.
    """
    product = await db.fetchrow(
        """SELECT min_price, max_price FROM products WHERE product_id = $1""",
        product_id,
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such product.")

    # The band applies to a human too. A manager may deliberately go outside it,
    # but not by accident, so this refuses and the UI asks them to widen the
    # band first — a visible, audited policy change rather than a silent one.
    lo, hi = product["min_price"], product["max_price"]
    if lo is not None and body.price < lo:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"${body.price} is below this product's ${lo} floor. "
            "Change the floor first if you mean to go lower.",
        )
    if hi is not None and body.price > hi:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"${body.price} is above this product's ${hi} ceiling. "
            "Change the ceiling first if you mean to go higher.",
        )

    result = await gate.publish_price(
        product_id=product_id,
        price=body.price,
        reason_tag=body.reason_tag,
        approval="manager",
        approved_by=user.username,
        decision_id=None,
        agent_version=None,
    )
    return result


class BulkDeleteBody(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/bulk/archive")
async def bulk_archive(
    body: BulkDeleteBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Archive rather than DELETE.

    price_history and agent_audit_log reference products, and those tables are
    append-only evidence. Removing the product row would either fail on the
    foreign key or take the audit trail with it, so a deleted product is one
    with `archived_at` set: gone from every list, still joinable from the log.
    """
    rows = await db.fetch(
        """UPDATE products SET archived_at = now(), updated_at = now()
            WHERE product_id = ANY($1) AND archived_at IS NULL
        RETURNING product_id""",
        body.product_ids,
    )
    ids = [r["product_id"] for r in rows]
    if ids:
        # Unpair their labels, otherwise an archived product keeps a label
        # hostage and the label reads as paired to something invisible.
        await db.execute(
            """UPDATE shelf_labels
                  SET product_id = NULL, slot_code = slot_code,
                      status = 'unassigned', paired_at = NULL
                WHERE product_id = ANY($1)""",
            ids,
        )
        await db.execute(
            """UPDATE proposals SET status = 'superseded', decided_at = now(),
                                    decided_by = $2
                WHERE product_id = ANY($1) AND status = 'pending'""",
            ids,
            user.username,
        )
        bus.emit("products", changed=ids, action="archived")

    return {"archived": ids, "count": len(ids)}


class BulkCategoryBody(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=60)


@router.post("/bulk/category")
async def bulk_category(
    body: BulkCategoryBody,
    _user: Annotated[auth.CurrentUser, Depends(auth.require_editor)],
) -> dict:
    rows = await db.fetch(
        """UPDATE products SET category = $2, updated_at = now()
            WHERE product_id = ANY($1) AND archived_at IS NULL
        RETURNING product_id""",
        body.product_ids,
        body.category,
    )
    ids = [r["product_id"] for r in rows]
    bus.emit("products", changed=ids, action="recategorised")
    return {"updated": ids, "count": len(ids)}
