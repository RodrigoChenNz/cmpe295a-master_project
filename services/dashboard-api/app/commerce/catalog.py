from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from .. import db
from .common import Model, VERSION, enabled, minor, rate_limit

router = APIRouter(tags=["development-catalog"], dependencies=[Depends(rate_limit)])


class Pagination(Model):
    limit: int = Field(default=24, ge=1, le=100)
    cursor: str | None = None


class Search(Model):
    query: str = Field(default="", max_length=200)
    pagination: Pagination = Field(default_factory=Pagination)


class Lookup(Model):
    ids: list[str] = Field(min_length=1, max_length=100)


def product(row, base):
    amount = minor(row["online_price"] or row["shelf_price"])
    price = {"amount": amount, "currency": "USD"}
    return {
        "id": row["product_id"],
        "title": row["name"],
        "description": {"plain": row["description"]},
        "url": base + "/shop/p/" + row["product_id"],
        "categories": [{"value": row["category"], "taxonomy": "merchant"}]
        if row["category"]
        else [],
        "price_range": {"min": price, "max": price},
        "media": [{"type": "image", "url": url} for url in row["images"]],
        "variants": [
            {
                "id": row["product_id"],
                "sku": row["sku"] or row["product_id"],
                "price": price,
                "availability": {"available": (row["stock_on_hand"] or 0) > 0},
            }
        ],
    }


QUERY = """SELECT p.*,(SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1) AS shelf_price
 FROM products p WHERE online AND archived_at IS NULL"""


@router.get("/.well-known/ucp")
async def discovery(request: Request):
    # Local development binding. No third-party certification, live payment or
    # authenticated platform profile discovery is advertised by this profile.
    base = str(request.base_url).rstrip("/")
    caps = {
        name: [
            {
                "version": VERSION,
                "spec": f"https://ucp.dev/{VERSION}/specification/shopping/{path}",
                "schema": f"https://ucp.dev/{VERSION}/schemas/shopping/{schema}.json",
            }
        ]
        for name, path, schema in [
            ("dev.ucp.shopping.checkout", "checkout", "checkout"),
            ("dev.ucp.shopping.catalog.search", "catalog/search", "catalog_search"),
            ("dev.ucp.shopping.catalog.lookup", "catalog/lookup", "catalog_lookup"),
        ]
    }
    return {
        "ucp": {
            "version": VERSION,
            "services": {
                "dev.ucp.shopping": [
                    {
                        "version": VERSION,
                        "spec": f"https://ucp.dev/{VERSION}/specification/overview",
                        "transport": "rest",
                        "schema": f"https://ucp.dev/{VERSION}/services/shopping/rest.openapi.json",
                        "endpoint": base + "/ucp/v1",
                    }
                ]
            },
            "capabilities": caps,
            "payment_handlers": {},
        },
        "shelfiq_development": {
            "test_payments_only": True,
            "negotiation": "local extension; namespace registration pending",
            "production_ready": False,
            "buyer_auth": "client-generated random bearer token; minimum 32 characters",
            "limitations": [
                "Local checkout payment/fulfillment shapes are development bindings.",
                "Public deployment requires TLS 1.3, platform negotiation and RFC 9421 signing.",
            ],
        },
    }


@router.post("/ucp/v1/catalog/search")
async def search(body: Search, request: Request):
    cursor = body.pagination.cursor or ""
    async with db.pool().acquire() as conn:
        await enabled(conn)
        rows = await conn.fetch(
            QUERY
            + """ AND (name ILIKE $1 OR product_id ILIKE $1) AND product_id>$2
            AND (online_price IS NOT NULL OR EXISTS(SELECT 1 FROM price_history WHERE product_id=p.product_id))
            ORDER BY product_id LIMIT $3""",
            "%" + body.query + "%",
            cursor,
            body.pagination.limit + 1,
        )
    more = len(rows) > body.pagination.limit
    rows = rows[: body.pagination.limit]
    return {
        "ucp": {
            "version": VERSION,
            "capabilities": {"dev.ucp.shopping.catalog.search": [{"version": VERSION}]},
        },
        "products": [product(p, str(request.base_url).rstrip("/")) for p in rows],
        "pagination": {
            "cursor": rows[-1]["product_id"] if more else None,
            "has_next_page": more,
        },
    }


@router.post("/ucp/v1/catalog/lookup")
async def lookup(body: Lookup, request: Request):
    async with db.pool().acquire() as conn:
        await enabled(conn)
        rows = await conn.fetch(QUERY + " AND product_id=ANY($1::text[])", body.ids)
    found = [
        product(p, str(request.base_url).rstrip("/"))
        for p in rows
        if p["online_price"] or p["shelf_price"]
    ]
    return {
        "ucp": {
            "version": VERSION,
            "capabilities": {"dev.ucp.shopping.catalog.lookup": [{"version": VERSION}]},
        },
        "products": found,
        "messages": [
            {"type": "error", "code": "not_found", "content": id, "severity": "recoverable"}
            for id in body.ids
            if id not in {x["id"] for x in found}
        ],
    }


@router.get("/api/public/products/{pid}/why")
async def why(pid: str):
    row = await db.fetchrow(
        """SELECT h.*,p.online_price FROM price_history h JOIN products p USING(product_id)
        WHERE product_id=$1 AND p.online AND p.archived_at IS NULL ORDER BY h.id DESC LIMIT 1""",
        pid,
    )
    if not row:
        raise HTTPException(404, "Product not available.")
    return {
        "price": str(row["online_price"] or row["price"]),
        "changed_at": row["ts"].isoformat(),
        "reason_tag": row["reason_tag"],
        "sentence": "The store set a separate online price."
        if row["online_price"]
        else f"The store approved this price with the reason: {row['reason_tag']}.",
    }
