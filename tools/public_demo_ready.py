#!/usr/bin/env python3
"""Verify the synthetic public demo; staff credentials are used ONLY on localhost.

Public traffic consists of fresh synthetic buyer data, published profiles and
signed test requests. Private staff credentials and receipt tokens stay local.
"""

import json
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from retail.ucp_client import UCPClient

sys.path.insert(0, str(ROOT / "services/dashboard-api"))
from app.ucp.schemas import validate


def prepare_locally(env):
    """Only the CA-verified loopback connection receives staff credentials."""
    context = ssl.create_default_context(cafile=str(ROOT / "secrets/ucp/ca.pem"))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    with httpx.Client(
        base_url="https://localhost:8443",
        verify=context,
        timeout=20,
        follow_redirects=False,
    ) as staff:
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
        listed = [
            p for p in products if p["current_price"] and (p["stock_on_hand"] or 0) >= 2
        ][:3]
        assert listed
        for p in listed:
            staff.put(
                "/api/commerce/products/" + p["product_id"],
                json={
                    "online": True,
                    "supplier_id": p["supplier_id"],
                    "description": p["description"]
                    or "Synthetic ShelfIQ public demonstration item.",
                    "online_price": p["online_price"],
                    "images": p["images"],
                },
            ).raise_for_status()
        return listed[0]["product_id"]


def main():
    env = dotenv_values(ROOT / ".env.demo.public")
    base, platform = env["PUBLIC_URL"], env["PLATFORM_URL"]
    evidence = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "storefront": base + "/shop",
        "platform": platform,
        "commercial_platform_approved": False,
        "checks": [],
        "limitations": [],
    }
    client = None
    try:
        pid = prepare_locally(env)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        for url in (base, platform):
            host = urlsplit(url).hostname
            with socket.create_connection((host, 443), timeout=15) as connection:
                with context.wrap_socket(connection, server_hostname=host) as tls:
                    assert tls.version() == "TLSv1.3"
        evidence["checks"].append(
            "Both public hosts have trusted certificates and negotiate TLS 1.3"
        )
        old = ssl.create_default_context()
        old.minimum_version = old.maximum_version = ssl.TLSVersion.TLSv1_2
        try:
            host = urlsplit(base).hostname
            with socket.create_connection((host, 443), timeout=15) as connection:
                with old.wrap_socket(connection, server_hostname=host):
                    evidence["limitations"].append(
                        "Free tunnel edge also accepts TLS 1.2; strict minimum-TLS-1.3 public conformance is not achieved. Origin and test clients require TLS 1.3."
                    )
        except ssl.SSLError:
            evidence["checks"].append("Public edge rejects TLS 1.2")
        client = UCPClient(
            base, platform + "/.well-known/ucp", ROOT / "secrets/ucp/platform.pem", None
        )
        validate("ucp.json#/$defs/business_schema", client.merchant["ucp"])
        response = client.http.get("/shop")
        response.raise_for_status()
        assert '<div id="root">' in response.text
        response = client.http.get(platform + "/.well-known/ucp")
        response.raise_for_status()
        validate("ucp.json#/$defs/platform_schema", response.json()["ucp"])
        for entries in client.merchant["ucp"]["payment_handlers"].values():
            response = client.http.get(entries[0]["schema"])
            response.raise_for_status()
            assert response.json()["$id"] == entries[0]["schema"]
        assert (
            client.request("POST", "/catalog/search", {}, signed=False).status_code
            == 401
        )
        evidence["checks"].append(
            "Public storefront, discovery, extension schemas and rejection of unsigned platform requests"
        )
        response = client.request("POST", "/catalog/search", {"query": pid})
        response.raise_for_status()
        validate("shopping/catalog_search.json#/$defs/search_response", response.json())
        assert response.json()["products"]
        cart = {
            "line_items": [{"item": {"id": pid}, "quantity": 2}],
            "buyer": {"email": "public-demo@example.test"},
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
        assert (
            client.request(
                "POST", "/checkout-sessions", cart, idempotency_key=idem
            ).json()
            == created.json()
        )
        sid = created.json()["id"]
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
        oid = paid.json()["order"]["id"]
        evidence["order_id"] = oid
        order = client.request("GET", "/orders/" + oid)
        order.raise_for_status()
        validate("shopping/order.json", order.json())
        evidence["checks"].append(
            "Public HTTPS platform negotiation, signed catalog, idempotent checkout, test decline/approval and order retrieval"
        )
        other = UCPClient(
            base, platform + "/.well-known/ucp", ROOT / "secrets/ucp/platform.pem", None
        )
        try:
            assert other.request("GET", "/orders/" + oid).status_code == 404
        finally:
            other.close()
        assert (
            client.http.get(platform + "/receipts/" + str(uuid4())).status_code == 401
        )
        receipt = None
        for _ in range(15):
            for file in (ROOT / "secrets/ucp/demo-receipts").glob("*.json"):
                record = json.loads(file.read_text())
                if record["order_id"] == oid:
                    receipt = record
                    break
            if receipt:
                break
            time.sleep(2)
        assert receipt, "Public signed notification was not received."
        evidence["webhook_event_id"] = receipt["event_id"]
        evidence["checks"].append(
            "Buyer isolation and real signed notification across both public tunnels; receipt evidence inspected locally"
        )
        evidence["passed"] = True
        evidence["strict_public_conformance"] = not evidence["limitations"]
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if client:
            client.close()
        output = ROOT / "artifacts/verification/public-demo-readiness.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
