from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .. import bus, db
from .common import Model, audit, wire
from .decisions import Actor
from .operations import Manager, Reader

router = APIRouter(tags=["commerce-settings-market"])


class CommerceSettings(Model):
    commerce_enabled: bool
    nightly_enabled: bool = False
    price_delivery_mode: Literal["immediate", "daily"] = "immediate"
    nightly_run_time: str = Field(default="02:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    shelf_update_time: str = Field(default="06:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    shipping_minor: int = Field(default=500, ge=0, le=100000, strict=True)
    tax_bps: int = Field(default=0, ge=0, le=10000, strict=True)


@router.get("/api/commerce/settings")
async def get_settings(user: Reader):
    return wire(
        await db.fetchrow("""SELECT commerce_enabled,nightly_enabled,nightly_run_time,shelf_update_time,shipping_minor,tax_bps,timezone,price_delivery_mode
        FROM store_settings WHERE id""")
    )


@router.put("/api/commerce/settings")
async def put_settings(body: CommerceSettings, user: Manager):
    from datetime import time

    async with db.pool().acquire() as conn, conn.transaction():
        if body.commerce_enabled:
            await conn.fetch("SELECT product_id FROM products ORDER BY product_id FOR UPDATE")
            await conn.execute(
                "UPDATE store_settings SET inventory_ledger_enabled=true WHERE id"
            )
            await conn.execute("""INSERT INTO inventory_opening_balances(product_id,quantity)
                SELECT p.product_id,p.stock_on_hand-coalesce((SELECT sum(quantity) FROM inventory_movements WHERE product_id=p.product_id),0)
                FROM products p WHERE stock_on_hand IS NOT NULL ON CONFLICT DO NOTHING""")
        zone = await conn.fetchval("SELECT timezone FROM store_settings WHERE id")
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError:
            raise HTTPException(409, "Set a valid store timezone before enabling nightly runs.")
        await conn.execute(
            """UPDATE store_settings SET commerce_enabled=$1,nightly_enabled=$2,nightly_run_time=$3,
            shelf_update_time=$4,shipping_minor=$5,tax_bps=$6,price_delivery_mode=$7 WHERE id""",
            body.commerce_enabled,
            body.nightly_enabled,
            time.fromisoformat(body.nightly_run_time),
            time.fromisoformat(body.shelf_update_time),
            body.shipping_minor,
            body.tax_bps,
            body.price_delivery_mode,
        )
        await audit(conn, "commerce_settings", "store", user.username, body.model_dump())
    bus.emit("settings")
    return body.model_dump()


class Supplier(Model):
    name: str = Field(min_length=2, max_length=120)
    contact: str | None = Field(default=None, max_length=250)
    lead_time_days: int = Field(default=3, ge=0, le=365)
    simulated: Literal[True] = True


@router.get("/api/suppliers")
async def suppliers(user: Reader):
    return {"rows": wire(await db.fetch("SELECT * FROM suppliers ORDER BY name"))}


@router.post("/api/suppliers")
async def supplier(body: Supplier, user: Manager):
    sid = uuid4()
    async with db.pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO suppliers(supplier_id,name,contact,lead_time_days,simulated) VALUES($1,$2,$3,$4,true)",
            sid,
            body.name,
            body.contact,
            body.lead_time_days,
        )
        await audit(conn, "supplier_created", sid, user.username, body.model_dump())
    return wire(await db.fetchrow("SELECT * FROM suppliers WHERE supplier_id=$1", sid))


class CommerceProduct(Model):
    supplier_id: UUID | None = None
    online: bool
    description: str = Field(default="", max_length=10000)
    online_price: Decimal | None = Field(default=None, gt=0, lt=1000000, decimal_places=2)
    images: list[str] = Field(default_factory=list, max_length=10)


@router.put("/api/commerce/products/{pid}")
async def commerce_product(pid: str, body: CommerceProduct, user: Manager):
    if any(not x.startswith("https://") for x in body.images):
        raise HTTPException(400, "Images must use HTTPS URLs.")
    async with db.pool().acquire() as conn, conn.transaction():
        p = await conn.fetchrow("SELECT * FROM products WHERE product_id=$1 FOR UPDATE", pid)
        if not p or p["archived_at"]:
            raise HTTPException(404, "Product not found.")
        if body.online_price is not None and (
            (p["min_price"] is not None and body.online_price < p["min_price"])
            or (p["max_price"] is not None and body.online_price > p["max_price"])
        ):
            raise HTTPException(409, "Online price is outside this product's guardrails.")
        if body.supplier_id and not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM suppliers WHERE supplier_id=$1)", body.supplier_id
        ):
            raise HTTPException(404, "Supplier not found.")
        await conn.execute(
            """UPDATE products SET supplier_id=$2,online=$3,description=$4,online_price=$5,images=$6 WHERE product_id=$1""",
            pid,
            body.supplier_id,
            body.online,
            body.description,
            body.online_price,
            body.images,
        )
        if body.supplier_id:
            await conn.execute(
                "INSERT INTO supplier_items(supplier_id,product_id,title,unit_cost) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                body.supplier_id,
                pid,
                p["name"],
                p["unit_cost"],
            )
        await audit(conn, "commerce_product", pid, user.username, body.model_dump())
    bus.emit("products")
    return {"product_id": pid, **wire(body.model_dump())}


@router.get("/api/commerce/products")
async def commerce_products(user: Reader):
    return {
        "rows": wire(
            await db.fetch("""SELECT p.product_id,p.name,p.online,p.description,p.online_price,p.images,p.supplier_id,p.stock_on_hand,p.unit_cost,
        (SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1) AS current_price
        FROM products p WHERE archived_at IS NULL ORDER BY product_id""")
        )
    }


class Observation(Model):
    product_id: str
    source: Literal["simulator", "supplier", "manual", "web"]
    competitor: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(gt=0, lt=1000000, decimal_places=2)
    currency: Literal["USD"] = "USD"
    url: str | None = Field(default=None, max_length=2000)
    observed_at: datetime


class Observations(Model):
    items: list[Observation] = Field(min_length=1, max_length=200)


@router.post("/api/market/observations")
async def observations(body: Observations, actor: Actor):
    async with db.pool().acquire() as conn, conn.transaction():
        ids = []
        for item in body.items:
            if item.observed_at.tzinfo is None or item.observed_at > datetime.now(
                timezone.utc
            ) + timedelta(minutes=5):
                raise HTTPException(
                    400, "Observed time must have a timezone and cannot be in the future."
                )
            if item.source == "web" and (not item.url or not item.url.startswith("https://")):
                raise HTTPException(400, "A web observation needs an HTTPS evidence URL.")
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM products WHERE product_id=$1)", item.product_id
            ):
                raise HTTPException(404, "Unknown product.")
            oid = uuid4()
            await conn.execute(
                """INSERT INTO market_observations(observation_id,product_id,source,competitor,price,currency,url,observed_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
                oid,
                item.product_id,
                item.source,
                item.competitor,
                item.price,
                item.currency,
                item.url,
                item.observed_at,
            )
            ids.append(str(oid))
        await audit(conn, "market_observations", "store", actor, {"ids": ids})
    bus.emit("market")
    return {"ids": ids}


@router.get("/api/market/observations")
async def market(
    user: Reader,
    product_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    rows = await db.fetch(
        """SELECT * FROM market_observations WHERE ($1::text IS NULL OR product_id=$1)
        ORDER BY observed_at DESC LIMIT $2 OFFSET $3""",
        product_id,
        page_size,
        (page - 1) * page_size,
    )
    total = await db.fetchval(
        "SELECT count(*) FROM market_observations WHERE ($1::text IS NULL OR product_id=$1)",
        product_id,
    )
    return {
        "rows": wire(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/api/market/summary")
async def market_summary(user: Reader):
    return {
        "rows": wire(
            await db.fetch("""SELECT product_id,min(price) AS competitor_min,
        percentile_disc(0.5) WITHIN GROUP(ORDER BY price) AS competitor_median,
        max(observed_at) AS last_observed,count(*) AS observations
        FROM (SELECT DISTINCT ON (product_id,competitor) * FROM market_observations
        WHERE observed_at>now()-interval '7 days' ORDER BY product_id,competitor,observed_at DESC) m GROUP BY product_id ORDER BY product_id""")
        )
    }


@router.get("/api/commerce/status")
async def status(user: Reader):
    from ..config import settings

    return {
        "scope": "local development; test payments and simulated suppliers",
        "webhooks": {
            "configured": len(settings.commerce_webhook_key) >= 32,
            "target": "configured HTTPS receiver"
            if settings.commerce_webhook_url
            else "local signed receiver",
            "queued": await db.fetchval(
                "SELECT count(*) FROM commerce_webhook_outbox WHERE delivered_at IS NULL"
            ),
            "delivered": await db.fetchval(
                "SELECT count(*) FROM commerce_webhook_outbox WHERE delivered_at IS NOT NULL"
            ),
        },
        "inventory_ledger_enabled": await db.fetchval(
            "SELECT inventory_ledger_enabled FROM store_settings WHERE id"
        ),
        "scheduled_prices": wire(
            await db.fetch(
                "SELECT decision_id,product_id,release_at,status,message FROM scheduled_price_publications WHERE status='queued' ORDER BY release_at"
            )
        ),
        "latest_run": wire(
            await db.fetchrow(
                "SELECT run_id,status,summary,finished_at FROM agent_runs ORDER BY created_at DESC LIMIT 1"
            )
        ),
    }
