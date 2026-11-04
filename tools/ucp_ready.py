#!/usr/bin/env python3
"""Verify actual HTTPS, public UCP schemas, signatures and a synthetic purchase."""

import argparse
import json
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retail.ucp_client import UCPClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/dashboard-api"))
from app.ucp.schemas import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://localhost:8443")
    parser.add_argument("--env", default=".env.deploy.local")
    parser.add_argument("--ca", default="secrets/ucp/ca.pem")
    parser.add_argument("--key", default="secrets/ucp/platform.pem")
    parser.add_argument("--output", default="artifacts/verification/ucp-readiness.json")
    args = parser.parse_args()
    evidence = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "base": args.base,
        "checks": [],
    }
    client = None
    try:
        env = dotenv_values(args.env)
        url = urlsplit(args.base)
        context = ssl.create_default_context(cafile=args.ca)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        with socket.create_connection(
            (url.hostname, url.port or 443), timeout=10
        ) as connection:
            with context.wrap_socket(connection, server_hostname=url.hostname) as tls:
                assert tls.version() == "TLSv1.3"
        old = ssl.create_default_context(cafile=args.ca)
        old.maximum_version = ssl.TLSVersion.TLSv1_2
        try:
            with socket.create_connection(
                (url.hostname, url.port or 443), timeout=10
            ) as connection:
                with old.wrap_socket(connection, server_hostname=url.hostname):
                    raise AssertionError("TLS 1.2 was accepted.")
        except ssl.SSLError:
            pass
        evidence["checks"].append(
            "TLS 1.3 certificate validation and TLS 1.2 rejection"
        )
        client = UCPClient(
            args.base, args.base + "/.well-known/ucp-platform", args.key, args.ca
        )
        validate("ucp.json#/$defs/business_schema", client.merchant["ucp"])
        assert (
            client.request("POST", "/catalog/search", {}, signed=False).status_code
            == 401
        )
        evidence["checks"].append("Signed discovery and fail-closed unsigned requests")
        staff = client.http
        signed_in = staff.post(
            "/api/auth/signin",
            json={
                "login": env.get("SHELFIQ_BOOTSTRAP_EMAIL", "admin@example.test"),
                "password": env["SHELFIQ_BOOTSTRAP_PASSWORD"],
            },
        )
        signed_in.raise_for_status()
        assert any("Secure" in c for c in signed_in.headers.get_list("set-cookie"))
        settings = staff.get("/api/commerce/settings").json()
        settings.pop("timezone", None)
        settings.update(commerce_enabled=True, nightly_enabled=False)
        staff.put("/api/commerce/settings", json=settings).raise_for_status()
        products = staff.get("/api/commerce/products").json()["rows"]
        product = next(
            p for p in products if p["current_price"] and (p["stock_on_hand"] or 0) >= 2
        )
        staff.put(
            "/api/commerce/products/" + product["product_id"],
            json={
                "online": True,
                "supplier_id": product["supplier_id"],
                "description": product["description"],
                "online_price": product["online_price"],
                "images": product["images"],
            },
        ).raise_for_status()
        pid = product["product_id"]
        result = client.request("POST", "/catalog/search", {"query": pid})
        result.raise_for_status()
        validate("shopping/catalog_search.json#/$defs/search_response", result.json())
        result = client.request("POST", "/catalog/lookup", {"ids": [pid]})
        result.raise_for_status()
        validate("shopping/catalog_lookup.json#/$defs/lookup_response", result.json())
        evidence["checks"].append(
            "Official catalogue search and correlated lookup schemas"
        )
        cart = {
            "line_items": [{"item": {"id": pid}, "quantity": 2}],
            "buyer": {"email": "ucp-demo@example.test"},
            "fulfillment": {
                "methods": [
                    {
                        "id": "pickup",
                        "type": "pickup",
                        "line_item_ids": [pid],
                        "selected_destination_id": "store",
                        "groups": [
                            {
                                "id": "group-1",
                                "line_item_ids": [pid],
                                "selected_option_id": "pickup",
                            }
                        ],
                    }
                ]
            },
        }
        idem = str(uuid4())
        created = client.request(
            "POST", "/checkout-sessions", cart, idempotency_key=idem
        )
        assert created.status_code == 201, created.text
        validate("shopping/checkout.json", created.json())
        validate(
            "shopping/fulfillment.json#/$defs/dev.ucp.shopping.checkout", created.json()
        )
        retry = client.request("POST", "/checkout-sessions", cart, idempotency_key=idem)
        assert retry.json() == created.json()
        sid = created.json()["id"]
        evidence["checkout_id"] = sid
        get = client.request("GET", "/checkout-sessions/" + sid)
        get.raise_for_status()
        validate("shopping/checkout.json", get.json())
        for token, expected in [("test_declined", 402), ("test_approved", 200)]:
            payment = {
                "payment": {
                    "instruments": [
                        {
                            "id": "test",
                            "handler_id": "shelfiq-test",
                            "type": "test_token",
                            "selected": True,
                            "credential": {"type": "test_token", "token": token},
                        }
                    ]
                }
            }
            paid = client.request("POST", f"/checkout-sessions/{sid}/complete", payment)
            assert paid.status_code == expected, paid.text
        validate("shopping/checkout.json", paid.json())
        oid = paid.json()["order"]["id"]
        evidence["order_id"] = oid
        order = client.request("GET", "/orders/" + oid)
        order.raise_for_status()
        validate("shopping/order.json", order.json())
        evidence["checks"].append(
            "Idempotent checkout, fulfillment, declined/approved test instruments and order schema"
        )
        wrong = UCPClient(
            args.base, args.base + "/.well-known/ucp-platform", args.key, args.ca
        )
        try:
            assert wrong.request("GET", "/orders/" + oid).status_code == 404
        finally:
            wrong.close()
        evidence["checks"].append(
            "Buyer isolation and verified signed responses on success/error"
        )
        storefront = staff.get("/shop")
        storefront.raise_for_status()
        assert '<div id="root">' in storefront.text
        evidence["checks"].append(
            "Built storefront and secure staff-session cookie through the gateway"
        )
        evidence["passed"] = True
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if client:
            client.close()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    raise SystemExit(0 if evidence["passed"] else 1)


if __name__ == "__main__":
    main()
