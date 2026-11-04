import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from .. import auth, bus, db, decisions as price_decisions
from ..config import settings
from .common import Model, audit, wire
from .operations import Manager, Reader, Purchase, create_po

router = APIRouter(tags=["commerce-decisions"])
TAGS = {
    "too_aggressive",
    "too_timid",
    "bad_timing",
    "wrong_supplier",
    "margin_too_low",
    "stock_risk",
    "explanation_unclear",
    "agree",
}


async def service(request: Request):
    provided = request.headers.get("X-Service-Key", "")
    configured = settings.commerce_agent_key
    if not configured or len(configured) < 32 or not hmac.compare_digest(provided, configured):
        raise HTTPException(401, "A configured agent service key is required.")
    return "agent:store"


async def manager_or_service(request: Request):
    if request.headers.get("X-Service-Key"):
        return await service(request)
    user = await auth.user_from_request(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    if user.role not in ("manager", "superadmin"):
        raise HTTPException(403, "Manager access is required.")
    return user.username


Service = Annotated[str, Depends(service)]
Actor = Annotated[str, Depends(manager_or_service)]


class Explanation(Model):
    summary: str = Field(min_length=10, max_length=1000)
    factors: list[dict] = Field(default_factory=list, max_length=20)
    evidence: list[dict] = Field(default_factory=list, max_length=30)


class ListingFields(Model):
    description: str | None = Field(default=None, max_length=10000)
    online_price: Decimal | None = Field(default=None, gt=0, lt=1000000, decimal_places=2)
    images: list[str] | None = Field(default=None, max_length=10)


class ListingProposal(Model):
    product_id: str
    action: Literal["list", "delist", "update"]
    fields: ListingFields = Field(default_factory=ListingFields)


class Submission(Model):
    kind: Literal["purchase_order", "listing"]
    proposal: dict
    context: dict = Field(default_factory=dict)
    explanation: Explanation
    run_id: UUID | None = None
    agent_version: str = Field(default="store-agent-1", max_length=100)
    expires_at: datetime | None = None


class Verdict(Model):
    action: Literal["approve", "override", "reject"]
    changes: dict = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=8)


class Mode(Model):
    mode: Literal["ask", "auto"] = "ask"


class PurchasePolicy(Mode):
    auto_max_total: Decimal = Field(default=Decimal("500"), ge=0, lt=1000000, decimal_places=2)


class DealPolicy(Mode):
    min_margin_pct: Decimal = Field(default=20, ge=0, lt=100)
    max_discount_pct: Decimal = Field(default=10, ge=0, le=100)
    min_quantity: int = Field(default=5, ge=1, le=10000)


class Policy(Model):
    purchase_order: PurchasePolicy = Field(default_factory=PurchasePolicy)
    deal: DealPolicy = Field(default_factory=DealPolicy)
    listing: Mode = Field(default_factory=Mode)


@router.get("/api/rules/autonomy")
async def get_policy(user: Reader):
    return await db.fetchval("SELECT policy FROM commerce_policy WHERE id")


@router.put("/api/rules/autonomy")
async def set_policy(body: Policy, user: Manager):
    value = wire(body.model_dump())
    async with db.pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE commerce_policy SET policy=$1,updated_by=$2,updated_at=now() WHERE id",
            value,
            user.username,
        )
        await audit(conn, "autonomy_changed", "store", user.username, value)
    bus.emit("rules")
    return value


async def submit(conn, body, actor="agent:store"):
    if body.kind == "purchase_order":
        proposal = wire(Purchase.model_validate(body.proposal).model_dump())
        subject = {
            "supplier_id": proposal["supplier_id"],
            "product_ids": sorted(x["product_id"] for x in proposal["lines"]),
        }
        policy = (await conn.fetchval("SELECT policy FROM commerce_policy WHERE id"))[
            "purchase_order"
        ]
        total = sum(Decimal(x["unit_cost"]) * x["quantity"] for x in proposal["lines"])
        automatic = policy["mode"] == "auto" and total <= Decimal(policy["auto_max_total"])
    else:
        proposal = wire(
            ListingProposal.model_validate(body.proposal).model_dump(exclude_unset=True)
        )
        subject = {"product_id": proposal["product_id"]}
        automatic = (await conn.fetchval("SELECT policy FROM commerce_policy WHERE id"))[
            "listing"
        ]["mode"] == "auto"
    # Avoid duplicate pending work for the same subject/kind across runs.
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", body.kind + str(subject)
    )
    existing = await conn.fetchval(
        "SELECT decision_id FROM commerce_decisions WHERE kind=$1 AND subject=$2 AND status='pending' AND expires_at>now()",
        body.kind,
        subject,
    )
    if existing:
        return dict(
            await conn.fetchrow(
                "SELECT * FROM commerce_decisions WHERE decision_id=$1", existing
            )
        )
    did = uuid4()
    expiry = body.expires_at or datetime.now(timezone.utc) + timedelta(hours=24)
    if expiry.tzinfo is None or not datetime.now(timezone.utc) < expiry <= datetime.now(
        timezone.utc
    ) + timedelta(days=7):
        raise HTTPException(
            400, "Decision expiry must be in the next seven days with a timezone."
        )
    await conn.execute(
        """INSERT INTO commerce_decisions(decision_id,kind,subject,proposal,context,explanation,gate,run_id,agent_version,expires_at)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        did,
        body.kind,
        subject,
        proposal,
        body.context,
        body.explanation.model_dump(),
        {
            "auto": automatic,
            "reasons": [] if automatic else ["Owner approval is required by autonomy policy."],
        },
        body.run_id,
        body.agent_version,
        expiry,
    )
    if automatic:
        await apply_verdict(
            conn, did, Verdict(action="approve"), actor="policy", automatic=True
        )
    await audit(conn, "decision_submitted", did, actor, {"kind": body.kind})
    return dict(
        await conn.fetchrow("SELECT * FROM commerce_decisions WHERE decision_id=$1", did)
    )


async def apply_verdict(conn, did, body, actor, automatic=False):
    info = await conn.fetchrow(
        "SELECT kind,subject FROM commerce_decisions WHERE decision_id=$1", did
    )
    if not info:
        raise HTTPException(404, "Decision not found.")
    # Every deal writer takes checkout -> decision -> offer, including expiry.
    session = None
    if info["kind"] == "deal":
        session = await conn.fetchrow(
            "SELECT * FROM checkout_sessions WHERE checkout_id=$1 FOR UPDATE",
            UUID(info["subject"]["checkout_id"]),
        )
    row = await conn.fetchrow(
        "SELECT * FROM commerce_decisions WHERE decision_id=$1 FOR UPDATE", did
    )
    if row["status"] != "pending" or row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, "Decision is no longer pending.")
    proposal = row["proposal"]
    if body.action == "override" and not body.changes:
        raise HTTPException(400, "An override requires changes.")
    if body.action != "override" and body.changes:
        raise HTTPException(400, "Changes require an override verdict.")
    result = None
    if row["kind"] == "deal":
        from .offers import decide_offer

        result = await decide_offer(conn, row, body, session, actor)
    elif body.action != "reject":
        if row["kind"] == "purchase_order":
            unknown = set(body.changes) - {"lines", "deliver_by"}
            if unknown:
                raise HTTPException(
                    400, "Purchase overrides support lines and deliver_by only."
                )
            result = await create_po(
                conn, Purchase.model_validate({**proposal, **body.changes}), actor, did
            )
            from .supplier import order_purchase

            result = await order_purchase(conn, result, actor)
        else:
            if set(body.changes) - {"fields"}:
                raise HTTPException(400, "Listing overrides support fields only.")
            proposed = ListingProposal.model_validate({**proposal, **body.changes})
            p = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id=$1 FOR UPDATE", proposed.product_id
            )
            if not p or p["archived_at"]:
                raise HTTPException(409, "Listing product is unavailable.")
            if proposed.fields.online_price is not None and (
                (p["min_price"] is not None and proposed.fields.online_price < p["min_price"])
                or (
                    p["max_price"] is not None and proposed.fields.online_price > p["max_price"]
                )
            ):
                raise HTTPException(409, "Online price is outside this product's guardrails.")
            fields = proposed.fields
            if fields.images and any(not x.startswith("https://") for x in fields.images):
                raise HTTPException(400, "Product images must use HTTPS URLs.")
            online = p["online"] if proposed.action == "update" else proposed.action == "list"
            # Explicit online_price:null returns to shelf-price parity.
            await conn.execute(
                """UPDATE products SET online=$2,description=$3,images=$4,online_price=$5,updated_at=now()
                WHERE product_id=$1""",
                p["product_id"],
                online,
                fields.description if fields.description is not None else p["description"],
                fields.images if fields.images is not None else p["images"],
                fields.online_price
                if "online_price" in fields.model_fields_set
                else p["online_price"],
            )
            result = {"product_id": p["product_id"], "online": online}
    status = (
        "rejected"
        if body.action == "reject"
        else "auto_approved"
        if automatic
        else "overridden"
        if body.action == "override"
        else "approved"
    )
    verdict = {
        **body.model_dump(),
        "actor": actor,
        "at": datetime.now(timezone.utc).isoformat(),
        "result": wire(result),
    }
    await conn.execute(
        "UPDATE commerce_decisions SET status=$2,verdict=$3 WHERE decision_id=$1",
        did,
        status,
        verdict,
    )
    await audit(conn, "decision_verdict", did, actor, verdict)
    return dict(
        await conn.fetchrow("SELECT * FROM commerce_decisions WHERE decision_id=$1", did)
    )


@router.post("/api/agent/decisions")
async def submit_decision(body: Submission, actor: Service):
    async with db.pool().acquire() as conn, conn.transaction():
        result = await submit(conn, body, actor)
    bus.emit("decision")
    return wire(result)


PRICE_VIEW = """SELECT p.decision_id,'price'::text AS kind,p.status,
 jsonb_build_object('product_id',p.product_id) AS subject,
 jsonb_build_object('proposed_price',p.proposed_price::text,'current_price',p.current_price::text,'reason_tag',p.reason_tag) AS proposal,
 '{}'::jsonb AS context,
 CASE WHEN s.decision_id IS NULL THEN NULL ELSE jsonb_build_object('summary',coalesce(p.rationale,p.reason_tag),'base_value',s.base_value,'shap',s.shap,'metadata',s.metadata) END AS explanation,
 '{}'::jsonb AS gate,
 CASE WHEN p.decided_at IS NULL THEN NULL ELSE jsonb_build_object('actor',p.decided_by,'at',p.decided_at,'reason',coalesce(f.reason,p.decline_reason),'tags',coalesce(f.tags,'[]'::jsonb),'final_price',p.final_price::text) END AS verdict,
 f.outcome,j.run_id AS run_id,p.agent_version,p.expires_at,p.ts AS created_at
 FROM proposals p LEFT JOIN shap_vectors s USING(decision_id) LEFT JOIN decision_feedback f USING(decision_id) LEFT JOIN agent_price_jobs j USING(decision_id)"""
UNIFIED = "SELECT * FROM commerce_decisions UNION ALL " + PRICE_VIEW


@router.get("/api/decisions")
async def inbox(
    user: Reader,
    status: str | None = "pending",
    kind: str | None = None,
    run_id: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    where = " WHERE ($1::text IS NULL OR status=$1) AND ($2::text IS NULL OR kind=$2) AND ($3::uuid IS NULL OR run_id=$3)"
    status = status or None
    kind = kind or None
    # Unified view is composed, so existing MQTT decisions cannot drift from a mirror.
    total = await db.fetchval(
        "SELECT count(*) FROM (" + UNIFIED + ") u" + where, status, kind, run_id
    )
    rows = await db.fetch(
        "SELECT * FROM ("
        + UNIFIED
        + ") u"
        + where
        + " ORDER BY expires_at,decision_id LIMIT $4 OFFSET $5",
        status,
        kind,
        run_id,
        page_size,
        (page - 1) * page_size,
    )
    return {
        "rows": wire(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
    }


@router.get("/api/decisions/{did}")
async def detail(did: UUID, user: Reader):
    row = await db.fetchrow("SELECT * FROM (" + UNIFIED + ") u WHERE decision_id=$1", did)
    if not row:
        raise HTTPException(404, "Decision not found.")
    return wire(row)


@router.post("/api/decisions/{did}/verdict")
async def verdict(did: UUID, body: Verdict, user: Manager):
    if set(body.tags) - TAGS or len(set(body.tags)) != len(body.tags):
        raise HTTPException(400, "Unknown or duplicate feedback tags.")
    if await db.fetchval("SELECT EXISTS(SELECT 1 FROM proposals WHERE decision_id=$1)", did):
        if (
            set(body.changes) - {"price"}
            or (body.action == "override" and "price" not in body.changes)
            or (body.action != "override" and body.changes)
        ):
            raise HTTPException(400, "A price override requires changes.price.")
        try:
            price = Decimal(str(body.changes["price"])) if body.changes else None
        except Exception:
            raise HTTPException(400, "Override price must be a decimal amount.")
        if price is not None and (
            not price.is_finite() or price <= 0 or price.quantize(Decimal(".01")) != price
        ):
            raise HTTPException(
                400, "Price must be positive and have at most two decimal places."
            )
        result = await price_decisions.decide(
            did,
            actor=user.username,
            action=body.action,
            price=price,
            reason=body.reason,
            feedback_tags=body.tags,
        )
    else:
        async with db.pool().acquire() as conn, conn.transaction():
            result = await apply_verdict(conn, did, body, user.username)
    bus.emit("decision")
    bus.emit("products")
    return wire(result)


class Outcome(Model):
    metrics: dict
    measured_at: datetime


@router.post("/api/decisions/{did}/outcome")
async def outcome(did: UUID, body: Outcome, actor: Service):
    if body.measured_at.tzinfo is None or body.measured_at > datetime.now(
        timezone.utc
    ) + timedelta(minutes=5):
        raise HTTPException(
            400, "Measured time must have a timezone and cannot be in the future."
        )
    value = wire(body.model_dump())
    async with db.pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT kind,status FROM (" + UNIFIED + ") u WHERE decision_id=$1", did
        )
        if not row or row["status"] == "pending":
            raise HTTPException(409, "Outcomes require a decided decision.")
        if row["kind"] == "price":
            await conn.execute(
                """INSERT INTO decision_feedback(decision_id,outcome) VALUES($1,$2)
                ON CONFLICT(decision_id) DO UPDATE SET outcome=$2,updated_at=now()""",
                did,
                value,
            )
        else:
            await conn.execute(
                "UPDATE commerce_decisions SET outcome=$2 WHERE decision_id=$1", did, value
            )
        await audit(conn, "decision_outcome", did, actor, value)
    return value


@router.get("/api/agent/feedback")
async def feedback(
    actor: Actor,
    since: datetime | None = None,
    kind: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
):
    where = " WHERE status NOT IN ('pending','superseded','expired') AND ($1::timestamptz IS NULL OR created_at>=$1) AND ($2::text IS NULL OR kind=$2)"
    rows = await db.fetch(
        "SELECT * FROM ("
        + UNIFIED
        + ") u"
        + where
        + " ORDER BY created_at,decision_id LIMIT $3 OFFSET $4",
        since,
        kind,
        page_size,
        (page - 1) * page_size,
    )
    total = await db.fetchval("SELECT count(*) FROM (" + UNIFIED + ") u" + where, since, kind)
    return {
        "rows": wire(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, -(-total // page_size)),
        "learning": "Human verdicts and outcomes for offline reward shaping; no automatic claim of retraining.",
    }
