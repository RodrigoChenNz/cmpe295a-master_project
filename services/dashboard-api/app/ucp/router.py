"""Schema-validated public binding over the existing transaction engine."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from .. import db
from ..config import settings
from ..commerce import catalog, checkout, offers
from ..commerce.common import buyer, rate_limit
from . import profiles
from .schemas import VERSION, validate

router = APIRouter(tags=["UCP 2026-08-25"], dependencies=[Depends(rate_limit)])


def checked(schema, payload, operation=None):
    try:
        validate(schema, payload, operation=operation)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return payload


def parse(model, **values):
    try:
        return model(**values)
    except ValidationError as exc:
        raise HTTPException(422, str(exc))


def pay_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": profiles.origin() + "/ucp/schemas/test-payment.json",
        "title": "ShelfIQ test payment instrument",
        "description": "Synthetic tokens only. This handler never charges money.",
        "allOf": [
            {"$ref": f"https://ucp.dev/{VERSION}/schemas/common/types/payment_instrument.json"},
            {
                "properties": {
                    "handler_id": {"const": "shelfiq-test"},
                    "type": {"const": "test_token"},
                    "credential": {
                        "type": "object",
                        "required": ["type", "token"],
                        "properties": {
                            "type": {"const": "test_token"},
                            "token": {"enum": ["test_approved", "test_declined"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["credential"],
            },
        ],
    }


def extension_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": profiles.origin() + "/ucp/schemas/negotiation.json",
        "name": profiles.namespace() + ".negotiation",
        "version": VERSION,
        "$defs": {
            "dev.ucp.shopping.checkout": {
                "allOf": [
                    {"$ref": f"https://ucp.dev/{VERSION}/schemas/shopping/checkout.json"},
                    {
                        "type": "object",
                        "properties": {
                            "shelfiq_offer": {
                                "type": "object",
                                "required": ["id", "status"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "status": {
                                        "enum": [
                                            "pending_review",
                                            "countered",
                                            "accepted",
                                            "declined",
                                            "expired",
                                            "withdrawn",
                                        ]
                                    },
                                },
                            }
                        },
                    },
                ]
            }
        },
    }


@router.get("/.well-known/ucp")
async def discovery():
    result = profiles.business_profile()
    checked("ucp.json#/$defs/business_schema", result["ucp"])
    return JSONResponse(result, headers={"Cache-Control": "public, max-age=60"})


@router.get("/.well-known/ucp-platform")
async def own_platform():
    if settings.ucp_public_mode:
        raise HTTPException(404, "Development platform disabled.")
    result = profiles.platform_profile()
    checked("ucp.json#/$defs/platform_schema", result["ucp"])
    return result


@router.get("/ucp/schemas/{name}.json")
async def schema(name: str):
    if name == "test-payment":
        return pay_schema()
    if name == "negotiation":
        return extension_schema()
    raise HTTPException(404, "Schema not found.")


@router.get("/ucp/spec", response_class=HTMLResponse)
async def spec():
    return "<h1>ShelfIQ UCP extensions</h1><p>Version 2026-08-25. The test payment handler accepts only test_approved and test_declined tokens. No actual charges occur.</p><p>Negotiation extends checkout. POST checkout-sessions/{id}/offers with line_items [{id,quantity,unit_price:{amount,currency}}]. Poll the offer, accept a counter or withdraw. Pending offers require owner review and block completion.</p>"


@router.get("/policies/{name}", response_class=HTMLResponse)
async def policies(name: str):
    texts = {
        "terms": "Demonstration store: all payments and supplier purchases are simulated. No goods will be shipped.",
        "privacy": "Use synthetic buyer data. Buyer/order records persist in the demonstration database. Do not enter card information.",
        "refunds": "Unfulfilled units can be canceled. Shipped units can be returned through staff. Merchandise and tax are refunded proportionally. Test shipping is nonrefundable.",
    }
    if name not in texts:
        raise HTTPException(404, "Policy not found.")
    return "<h1>ShelfIQ demonstration policy</h1><p>" + texts[name] + "</p>"


async def negotiate(request, required):
    return await profiles.negotiate(request, required)


@router.post("/ucp/v1/catalog/search")
async def search(body: dict, request: Request):
    ctx = await negotiate(request, "dev.ucp.shopping.catalog.search")
    checked("shopping/catalog_search.json#/$defs/search_request", body)
    filters = body.get("filters", {})
    supported = {"categories", "price"}
    if set(filters) - supported:
        raise HTTPException(422, "Unsupported catalogue filter.")
    band = filters.get("price", {})
    if band and body.get("context", {}).get("currency", "USD") != "USD":
        raise HTTPException(
            422, "Price filters require USD; currency conversion is unsupported."
        )
    requested = body.get("pagination", {})
    limit = requested.get("limit", 24)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise HTTPException(422, "Pagination limit must be 1–100.")
    after = requested.get("cursor") or ""
    async with db.pool().acquire() as conn:
        from ..commerce.common import enabled

        await enabled(conn)
        # Apply every supported filter in SQL before LIMIT. Sparse matches must
        # remain reachable even in catalogues larger than a single fetch batch.
        rows = await conn.fetch(
            catalog.QUERY
            + """ AND product_id>$1 AND (name ILIKE $2 OR product_id ILIKE $2)
            AND coalesce(online_price,(SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1)) IS NOT NULL
            AND (cardinality($3::text[])=0 OR category=ANY($3::text[]))
            AND round(coalesce(online_price,(SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1))*100) BETWEEN $4::bigint AND $5::bigint
            ORDER BY product_id LIMIT $6""",
            after,
            "%" + body.get("query", "") + "%",
            filters.get("categories", []),
            band.get("min", 0),
            band.get("max", 9007199254740991),
            limit + 1,
        )
    products = [catalog.product(row, settings.ucp_base_url.rstrip("/")) for row in rows]
    more = len(products) > limit
    result = {
        "ucp": profiles.envelope(ctx["capabilities"]),
        "products": products[:limit],
        "pagination": {
            "has_next_page": more,
            **({"cursor": products[limit - 1]["id"]} if more else {}),
        },
    }
    return checked("shopping/catalog_search.json#/$defs/search_response", result)


@router.post("/ucp/v1/catalog/lookup")
async def lookup(body: dict, request: Request):
    ctx = await negotiate(request, "dev.ucp.shopping.catalog.lookup")
    checked("shopping/catalog_lookup.json#/$defs/lookup_request", body)
    filters = body.get("filters", {})
    if set(filters) - {"categories", "price"}:
        raise HTTPException(422, "Unsupported catalogue filter.")
    if filters.get("price") and body.get("context", {}).get("currency", "USD") != "USD":
        raise HTTPException(
            422, "Price filters require USD; currency conversion is unsupported."
        )
    local = await catalog.lookup(parse(catalog.Lookup, ids=body["ids"]), request)
    band = filters.get("price", {})
    local["products"] = [
        p
        for p in local["products"]
        if (
            not filters.get("categories")
            or any(c["value"] in filters["categories"] for c in p["categories"])
        )
        and band.get("min", 0)
        <= p["variants"][0]["price"]["amount"]
        <= band.get("max", 9007199254740991)
    ]
    for product in local["products"]:
        product["url"] = settings.ucp_base_url.rstrip("/") + "/shop/p/" + product["id"]
        for variant in product["variants"]:
            variant["inputs"] = [{"id": variant["id"], "match": "exact"}]
    local["ucp"] = profiles.envelope(ctx["capabilities"])
    return checked("shopping/catalog_lookup.json#/$defs/lookup_response", local)


@router.post("/ucp/v1/catalog/product")
async def get_product(body: dict, request: Request):
    checked("shopping/catalog_lookup.json#/$defs/get_product_request", body)
    if body.get("selected"):
        raise HTTPException(
            422,
            "This catalogue has single-variant products; option selections are unsupported.",
        )
    data = await lookup({"ids": [body["id"]]}, request)
    if not data["products"]:
        raise HTTPException(404, "Product not found.")
    result = {"ucp": data["ucp"], "product": data["products"][0]}
    return checked("shopping/catalog_lookup.json#/$defs/get_product_response", result)


def fulfillment_input(body, ids):
    methods = body.get("fulfillment", {}).get("methods", [])
    if not methods:
        return None
    if len(methods) != 1:
        raise HTTPException(422, "The store supports one fulfillment method per checkout.")
    method = methods[0]
    if method.get("type") not in ("pickup", "shipping") or set(
        method.get("line_item_ids", ids)
    ) != set(ids):
        raise HTTPException(
            422, "Fulfillment must cover every cart item with pickup or shipping."
        )
    groups = method.get("groups", [])
    if len(groups) > 1 or any(
        g.get("selected_option_id") not in (None, "standard", "pickup") for g in groups
    ):
        raise HTTPException(422, "Unsupported fulfillment option.")
    if method["type"] == "pickup":
        if method.get("selected_destination_id") not in (None, "store"):
            raise HTTPException(422, "Unknown pickup location.")
        return (
            {"method": "pickup"}
            if method.get("selected_destination_id") == "store"
            and groups
            and groups[0].get("selected_option_id") == "pickup"
            else None
        )
    destinations = method.get("destinations", [])
    selected = next(
        (d for d in destinations if d.get("id") == method.get("selected_destination_id")), None
    )
    if not selected:
        return None
    if not all(
        selected.get(k)
        for k in ("street_address", "address_locality", "address_country", "postal_code")
    ):
        raise HTTPException(
            422, "Shipping destination requires street, locality, country and postal code."
        )
    if not groups or groups[0].get("selected_option_id") != "standard":
        return None
    return {
        "method": "shipping",
        "address": ", ".join(
            selected.get(k, "")
            for k in (
                "street_address",
                "address_locality",
                "address_region",
                "postal_code",
                "address_country",
            )
        ),
    }


def local_cart(body, ctx):
    for line in body["line_items"]:
        basis = line["item"].get("quantity_unit")
        if basis is not None and (basis.get("unit") != "C62" or basis.get("scale", 0) != 0):
            raise HTTPException(
                422, "Only whole-item (C62, scale 0) quantities are sold by this catalogue."
            )
    lines = [
        {"item": {"id": x["item"]["id"]}, "quantity": x["quantity"]} for x in body["line_items"]
    ]
    context = {**ctx, "fulfillment": deepcopy(body.get("fulfillment", {}))}
    buyer_data = body.get("buyer")
    buyer_data = (
        {k: v for k, v in buyer_data.items() if k in ("email", "first_name", "last_name")}
        if buyer_data and buyer_data.get("email")
        else None
    )
    result = parse(
        checkout.Cart,
        line_items=lines,
        buyer=buyer_data,
        fulfillment=fulfillment_input(body, [x["item"]["id"] for x in lines]),
        channel="ucp",
    )
    result._ucp_context = context
    return result


def fulfillment_output(payload):
    context = payload.get("_ucp", {})
    ids = [x["id"] for x in payload["line_items"]]
    supplied = context.get("fulfillment", {}).get("methods", [])
    if not supplied:
        return {
            "available_methods": [
                {"type": kind, "line_item_ids": ids, "fulfillable_on": "now"}
                for kind in ("pickup", "shipping")
            ]
        }
    method = deepcopy(supplied[0])
    method.setdefault("id", "method-1")
    method["line_item_ids"] = ids
    kind = method["type"]
    if kind == "pickup":
        method["destinations"] = [
            {"id": "store", "type": "business_location", "name": "ShelfIQ store"}
        ]
    if kind == "shipping":
        for destination in method.get("destinations", []):
            destination.setdefault("type", "shipping_address")
    shipping = context.get("shipping_minor")
    if shipping is None:
        shipping = next((x["amount"] for x in payload["totals"] if x["type"] == "shipping"), 0)
    method["groups"] = [
        {
            "id": "group-1",
            "line_item_ids": ids,
            "selected_option_id": method.get("groups", [{}])[0].get("selected_option_id")
            if method.get("groups")
            else None,
            "options": [
                {
                    "id": "pickup" if kind == "pickup" else "standard",
                    "title": "Store pickup" if kind == "pickup" else "Standard shipping",
                    "totals": [{"type": "fulfillment", "amount": shipping}],
                }
            ],
        }
    ]
    return {"methods": [method]}


def checkout_output(payload, ctx):
    result = {
        k: deepcopy(payload[k])
        for k in ("id", "status", "currency", "line_items", "totals", "messages", "expires_at")
        if k in payload
    }
    if payload.get("buyer"):
        result["buyer"] = deepcopy(payload["buyer"])
    result["ucp"] = profiles.envelope(ctx["capabilities"], checkout=True)
    result["ucp"]["payment_handlers"] = ctx.get("payment_handlers", {})
    if not result["ucp"]["payment_handlers"] and result["status"] not in (
        "completed",
        "canceled",
    ):
        result["status"] = "requires_escalation"
        result["continue_url"] = settings.ucp_base_url.rstrip("/") + "/shop"
        result["messages"].append(
            {
                "type": "error",
                "code": "payment_handler_unavailable",
                "content": "No shared test payment handler; continue in the storefront.",
                "severity": "recoverable",
            }
        )
    if "dev.ucp.shopping.fulfillment" in ctx["capabilities"]:
        result["fulfillment"] = fulfillment_output(payload)
    result["links"] = [
        {"type": name, "url": settings.ucp_base_url.rstrip("/") + "/policies/" + path}
        for name, path in [
            ("terms_of_service", "terms"),
            ("privacy_policy", "privacy"),
            ("refund_policy", "refunds"),
        ]
    ]
    result["payment"] = {"instruments": []}
    if payload.get("order"):
        result["order"] = {
            "id": payload["order"]["id"],
            "permalink_url": settings.ucp_base_url.rstrip("/")
            + payload["order"]["permalink_url"],
        }
    for total in result["totals"]:
        if total["type"] == "shipping":
            total["type"] = "fulfillment"
    checked("shopping/checkout.json", result)
    if "fulfillment" in result:
        checked("shopping/fulfillment.json#/$defs/dev.ucp.shopping.checkout", result)
    return result


async def session_context(sid, request, ctx):
    row = await db.fetchrow(
        "SELECT payload FROM checkout_sessions WHERE checkout_id=$1 AND owner_hash=$2",
        sid,
        buyer(request),
    )
    if not row or not row["payload"].get("_ucp"):
        raise HTTPException(404, "UCP checkout not found.")
    original = row["payload"]["_ucp"]
    if ctx["profile_url"] != original["profile_url"]:
        raise HTTPException(409, "The checkout belongs to another platform profile.")
    if ctx["capabilities"] != original["capabilities"]:
        raise HTTPException(409, "Negotiated capabilities changed; start a new checkout.")
    return row["payload"]


@router.post("/ucp/v1/checkout-sessions", status_code=201)
async def create(body: dict, request: Request):
    ctx = await negotiate(request, profiles.CORE)
    checked("shopping/checkout.json", body, "create")
    if "fulfillment" in body and "dev.ucp.shopping.fulfillment" not in ctx["capabilities"]:
        raise HTTPException(422, "Fulfillment extension was not negotiated.")
    if "fulfillment" in body:
        checked("shopping/fulfillment.json#/$defs/dev.ucp.shopping.checkout", body, "create")
    ctx["shipping_minor"] = await db.fetchval(
        "SELECT shipping_minor FROM store_settings WHERE id"
    )
    result = await checkout.create_cart(local_cart(body, ctx), request)
    return checkout_output(result, ctx)


@router.get("/ucp/v1/checkout-sessions/{sid}")
async def get(sid: UUID, request: Request):
    ctx = await negotiate(request, profiles.CORE)
    await session_context(sid, request, ctx)
    return checkout_output(await checkout.get_cart(sid, request), ctx)


@router.put("/ucp/v1/checkout-sessions/{sid}")
async def update(sid: UUID, body: dict, request: Request):
    ctx = await negotiate(request, profiles.CORE)
    await session_context(sid, request, ctx)
    checked("shopping/checkout.json", body, "update")
    if "fulfillment" in body and "dev.ucp.shopping.fulfillment" not in ctx["capabilities"]:
        raise HTTPException(422, "Fulfillment extension was not negotiated.")
    if "fulfillment" in body:
        checked("shopping/fulfillment.json#/$defs/dev.ucp.shopping.checkout", body, "update")
    ctx["shipping_minor"] = await db.fetchval(
        "SELECT shipping_minor FROM store_settings WHERE id"
    )
    return checkout_output(await checkout.update_cart(sid, local_cart(body, ctx), request), ctx)


@router.post("/ucp/v1/checkout-sessions/{sid}/complete")
async def complete(sid: UUID, body: dict, request: Request):
    ctx = await negotiate(request, profiles.CORE)
    await session_context(sid, request, ctx)
    checked("shopping/checkout.json", body, "complete")
    if not ctx.get("payment_handlers"):
        raise HTTPException(422, "No mutually supported payment handler.")
    instruments = body.get("payment", {}).get("instruments", [])
    if len(instruments) != 1:
        raise HTTPException(422, "Exactly one test payment instrument is required.")
    instrument = instruments[0]
    if (
        instrument.get("handler_id") != "shelfiq-test"
        or instrument.get("type") != "test_token"
        or not instrument.get("selected", True)
    ):
        raise HTTPException(422, "Only the advertised test handler can be selected.")
    credential = instrument.get("credential", {})
    if credential.get("type") != "test_token" or credential.get("token") not in (
        "test_approved",
        "test_declined",
    ):
        raise HTTPException(422, "Invalid test token; card data is not accepted.")
    result = await checkout.complete_cart(
        sid, checkout.Complete(payment_token=credential["token"]), request
    )
    return checkout_output(result, ctx)


@router.post("/ucp/v1/checkout-sessions/{sid}/cancel")
async def cancel(sid: UUID, request: Request):
    ctx = await negotiate(request, profiles.CORE)
    await session_context(sid, request, ctx)
    return checkout_output(await checkout.cancel_cart(sid, request), ctx)


def order_output(payload, created_at=None):
    result = {
        "id": payload["id"],
        "checkout_id": payload["checkout_id"],
        "permalink_url": settings.ucp_base_url.rstrip("/") + "/shop/orders/" + payload["id"],
        "ucp": profiles.envelope(payload["_ucp"]["capabilities"]),
        "currency": payload["currency"],
        "line_items": [],
        "totals": deepcopy(payload["totals"]),
        "fulfillment": {"expectations": [], "events": []},
    }
    for old in payload["line_items"]:
        total = old["quantity"] - old["canceled"] - old["returned"]
        fulfilled = max(0, old["fulfilled"] - old["returned"])
        status = (
            "removed"
            if total == 0
            else "fulfilled"
            if fulfilled == total
            else "partial"
            if fulfilled
            else "processing"
        )
        result["line_items"].append(
            {
                "id": old["id"],
                "item": old["item"],
                "quantity": {
                    "original": old["quantity"],
                    "total": total,
                    "fulfilled": fulfilled,
                },
                "status": status,
                "totals": [
                    {"type": "subtotal", "amount": old["quantity"] * old["item"]["price"]},
                    {"type": "total", "amount": old["quantity"] * old["item"]["price"]},
                ],
            }
        )
    result["adjustments"] = deepcopy(payload.get("adjustments", []))
    # The pinned upstream adjustment text allows signed refunds, but its shared
    # amount schema enforces >=0. Do not emit invalid negative totals or pretend
    # refunds are charges. Optional totals are omitted; the exact refund remains
    # readable here and structured in the staff ledger/local order API.
    for adjustment in result["adjustments"]:
        amounts = adjustment.pop("totals", [])
        refunded = -next((t["amount"] for t in amounts if t["type"] == "total"), 0)
        if refunded:
            adjustment["description"] += (
                f"; refund {payload['currency']} {refunded // 100}.{refunded % 100:02d}"
            )
    for old in payload["fulfillment"]["events"]:
        event = {k: v for k, v in old.items() if v is not None}
        event["occurred_at"] = (
            event.get("occurred_at") or created_at or datetime.now(timezone.utc).isoformat()
        )
        result["fulfillment"]["events"].append(event)
    for total in result["totals"]:
        if total["type"] == "shipping":
            total["type"] = "fulfillment"
    return checked("shopping/order.json", result)


@router.get("/ucp/v1/orders/{oid}")
async def order(oid: UUID, request: Request):
    ctx = await negotiate(request, "dev.ucp.shopping.order")
    row = await db.fetchrow(
        "SELECT * FROM orders WHERE order_id=$1 AND owner_hash=$2", oid, buyer(request)
    )
    if not row or row["payload"].get("_ucp", {}).get("profile_url") != ctx["profile_url"]:
        raise HTTPException(404, "Order not found.")
    return order_output(row["payload"], row["created_at"].isoformat())


@router.post("/ucp/v1/checkout-sessions/{sid}/offers")
async def offer(sid: UUID, body: dict, request: Request):
    ctx = await negotiate(request, profiles.namespace() + ".negotiation")
    await session_context(sid, request, ctx)
    result = await offers.make_offer(sid, parse(offers.Offer, **body), request)
    return {"ucp": profiles.envelope(ctx["capabilities"]), **result}


@router.get("/ucp/v1/checkout-sessions/{sid}/offers/{oid}")
async def offer_detail(sid: UUID, oid: UUID, request: Request):
    ctx = await negotiate(request, profiles.namespace() + ".negotiation")
    await session_context(sid, request, ctx)
    return {
        "ucp": profiles.envelope(ctx["capabilities"]),
        **await offers.get_offer(sid, oid, request),
    }


@router.post("/ucp/v1/checkout-sessions/{sid}/offers/{oid}/{action}")
async def offer_response(sid: UUID, oid: UUID, action: str, request: Request):
    ctx = await negotiate(request, profiles.namespace() + ".negotiation")
    await session_context(sid, request, ctx)
    return {
        "ucp": profiles.envelope(ctx["capabilities"]),
        **await offers.respond_offer(sid, oid, action, request),
    }
