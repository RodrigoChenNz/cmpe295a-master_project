"""Capability negotiation and pinned-address HTTPS platform discovery."""

import asyncio
import copy
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from ..config import settings
from .schemas import VERSION, validate
from .signatures import jwk, verify

CORE = "dev.ucp.shopping.checkout"
_cache = {}


def origin():
    return settings.ucp_schema_origin.rstrip("/")


def namespace():
    host = urlsplit(origin()).hostname or ""
    if len(host.split(".")) < 2 or urlsplit(origin()).scheme != "https":
        raise ValueError("UCP_SCHEMA_ORIGIN needs an HTTPS domain with at least two labels.")
    return ".".join(reversed(host.split("."))) + ".shelfiq"


def capabilities():
    caps = {
        name: [
            {
                "version": VERSION,
                "spec": f"https://ucp.dev/{VERSION}/specification/shopping/{path}",
                "schema": f"https://ucp.dev/{VERSION}/schemas/shopping/{schema}.json",
            }
        ]
        for name, path, schema in [
            (CORE, "checkout", "checkout"),
            ("dev.ucp.shopping.catalog.search", "catalog/search", "catalog_search"),
            ("dev.ucp.shopping.catalog.lookup", "catalog/lookup", "catalog_lookup"),
            ("dev.ucp.shopping.order", "order", "order"),
            ("dev.ucp.shopping.fulfillment", "fulfillment", "fulfillment"),
        ]
    }
    caps["dev.ucp.shopping.fulfillment"][0]["extends"] = CORE
    caps[namespace() + ".negotiation"] = [
        {
            "version": VERSION,
            "spec": origin() + "/ucp/spec",
            "schema": origin() + "/ucp/schemas/negotiation.json",
            "extends": CORE,
        }
    ]
    return caps


def payment_handlers():
    return {
        namespace() + ".test_payment": [
            {
                "id": "shelfiq-test",
                "version": VERSION,
                "spec": origin() + "/ucp/spec",
                "schema": origin() + "/ucp/schemas/test-payment.json",
                "available_instruments": [{"type": "test_token"}],
                "config": {"test_only": True},
            }
        ]
    }


def business_profile():
    if not settings.ucp_signing_key_file:
        raise HTTPException(
            503, "Configure UCP signing and HTTPS before enabling public discovery."
        )
    root = {
        "ucp": {
            "version": VERSION,
            "services": {
                "dev.ucp.shopping": [
                    {
                        "version": VERSION,
                        "transport": "rest",
                        "endpoint": settings.ucp_base_url.rstrip("/") + "/ucp/v1",
                        "spec": f"https://ucp.dev/{VERSION}/specification/overview",
                        "schema": f"https://ucp.dev/{VERSION}/services/shopping/rest.openapi.json",
                    }
                ]
            },
            "capabilities": capabilities(),
            "payment_handlers": payment_handlers(),
        },
        "keys": [jwk(settings.ucp_signing_key_file, "shelfiq-business")]
        if settings.ucp_signing_key_file
        else [],
    }

    return root


def platform_profile():
    return {
        "ucp": {
            "version": VERSION,
            "services": {},
            "capabilities": capabilities(),
            "payment_handlers": payment_handlers(),
        },
        "keys": [jwk(settings.ucp_platform_key_file, "shelfiq-platform")]
        if settings.ucp_platform_key_file
        else [],
    }


def authority(name, url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    prefix = ".".join(reversed(host.split(".")))
    return (
        parts.scheme == "https"
        and not parts.username
        and not parts.password
        and len(host.split(".")) >= 2
        and (name == prefix or name.startswith(prefix + "."))
    )


def validate_authorities(profile):
    for registry in ("services", "capabilities", "payment_handlers"):
        for name, entries in profile["ucp"].get(registry, {}).items():
            for entry in entries:
                if entry.get("schema") and not authority(name, entry["schema"]):
                    raise HTTPException(422, "profile_malformed: namespace authority mismatch.")


def intersect(business, platform):
    result = {}
    for name, entries in business.items():
        versions = {x["version"] for x in platform.get(name, [])}
        shared = [x for x in entries if x["version"] in versions]
        if shared:
            result[name] = [copy.deepcopy(max(shared, key=lambda x: x["version"]))]
    while True:
        missing = []
        for name, entries in result.items():
            parents = entries[0].get("extends", [])
            if isinstance(parents, str):
                parents = [parents]
            if parents and not any(p in result for p in parents):
                missing.append(name)
        if not missing:
            break
        for name in missing:
            del result[name]
    return result


def fetch_https(url):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or parts.port not in (None, 443)
    ):
        raise HTTPException(
            400, "invalid_profile_url: a public HTTPS URL on port 443 is required."
        )
    try:
        addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise HTTPException(
                400, "invalid_profile_url: private and special network addresses are forbidden."
            )
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        # Resolve once, connect to that IP, then verify the original hostname.
        connection = http.client.HTTPSConnection(parts.hostname, timeout=5, context=context)
        connection.sock = context.wrap_socket(
            socket.create_connection((addresses[0][4][0], 443), timeout=5),
            server_hostname=parts.hostname,
        )
        try:
            connection.request(
                "GET",
                parts.path + ("?" + parts.query if parts.query else "") or "/",
                headers={"Accept": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(262145)
            if response.status != 200 or len(raw) > 262144:
                raise ValueError("Profile response is oversized or not HTTP 200.")
            return json.loads(raw)
        finally:
            connection.close()
    except HTTPException:
        raise
    except (OSError, ValueError, ssl.SSLError) as exc:
        raise HTTPException(424, "profile_unreachable: " + str(exc))


async def load_profile(url):
    if not settings.ucp_public_mode:
        if url == settings.ucp_base_url.rstrip("/") + "/.well-known/ucp-platform":
            return platform_profile()
        configured = json.loads(settings.ucp_platform_profiles_json)
        if url in configured:
            return copy.deepcopy(configured[url])
    if url in _cache and _cache[url][0] > time.monotonic():
        return copy.deepcopy(_cache[url][1])
    profile = await asyncio.to_thread(fetch_https, url)
    if len(_cache) >= 256:
        _cache.clear()
    _cache[url] = (time.monotonic() + 60, copy.deepcopy(profile))
    return profile


async def negotiate(request: Request, required):
    if not settings.ucp_signing_key_file:
        raise HTTPException(503, "Public UCP is not configured.")
    raw = request.headers.get("ucp-agent", "")
    match = re.fullmatch(r'profile="(https://[^"\s]{1,2000})"', raw)
    if not match:
        raise HTTPException(
            400, 'invalid_profile_url: UCP-Agent must contain profile="https://…".'
        )
    if not request.headers.get("request-id") or len(request.headers["request-id"]) > 200:
        raise HTTPException(400, "Request-Id is required (maximum 200 characters).")
    profile = await load_profile(match[1])
    try:
        validate("ucp.json#/$defs/platform_schema", profile["ucp"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, "profile_malformed: " + str(exc))
    validate_authorities(profile)
    if profile["ucp"]["version"] != VERSION:
        raise HTTPException(422, "version_unsupported")
    active = intersect(capabilities(), profile["ucp"].get("capabilities", {}))
    if required not in active:
        raise HTTPException(
            200,
            {
                "code": "capabilities_incompatible",
                "content": "No compatible capability for this operation.",
            },
        )
    if (
        settings.ucp_public_mode
        or settings.ucp_require_signatures
        or request.headers.get("signature")
    ):
        verify(
            await request.body(),
            {
                "@method": request.method,
                "@target-uri": str(request.url),
                "ucp-agent": raw,
                "request-id": request.headers["request-id"],
            },
            request.headers,
            profile.get("keys", []),
        )
    handlers = {}
    for name, entries in payment_handlers().items():
        versions = {
            entry["version"]
            for entry in profile["ucp"].get("payment_handlers", {}).get(name, [])
        }
        selected = [entry for entry in entries if entry["version"] in versions]
        if selected:
            handlers[name] = selected
    order_entries = profile["ucp"].get("capabilities", {}).get("dev.ucp.shopping.order", [])
    webhook = next(
        (
            entry.get("config", {}).get("webhook_url")
            for entry in order_entries
            if entry["version"] == VERSION
        ),
        None,
    )
    if webhook:
        hook = urlsplit(webhook)
        platform = urlsplit(match[1])
        if (
            hook.scheme != "https"
            or hook.hostname != platform.hostname
            or hook.username
            or hook.password
            or hook.fragment
            or hook.port not in (None, 443)
        ):
            raise HTTPException(
                422,
                "profile_malformed: webhook must use public HTTPS on the platform profile host.",
            )
    return {
        "profile_url": match[1],
        "capabilities": active,
        "payment_handlers": handlers,
        "order_webhook_url": webhook,
    }


def envelope(active, *, checkout=False):
    result = {
        "version": VERSION,
        "capabilities": {k: [{"version": v[0]["version"]}] for k, v in active.items()},
    }
    if checkout:
        result["payment_handlers"] = payment_handlers()
    return result


def check_configuration():
    if not settings.ucp_public_mode:
        return
    base = urlsplit(settings.ucp_base_url)
    schema = urlsplit(settings.ucp_schema_origin)
    for url in (base, schema):
        host = url.hostname or ""
        try:
            ipaddress.ip_address(host)
            numeric = True
        except ValueError:
            numeric = False
        if (
            numeric
            or url.scheme != "https"
            or url.username
            or url.password
            or url.port not in (None, 443)
            or url.path not in ("", "/")
            or url.query
            or url.fragment
            or len(host.split(".")) < 2
            or host.endswith((".test", ".example", ".invalid", ".localhost"))
        ):
            raise ValueError(
                "Public UCP mode requires configured owned HTTPS domains; reserved development hosts are forbidden."
            )
    if not settings.ucp_signing_key_file:
        raise ValueError("Public UCP mode requires a private business signing identity.")
    jwk(settings.ucp_signing_key_file, "shelfiq-business")
