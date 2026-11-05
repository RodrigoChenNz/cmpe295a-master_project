#!/usr/bin/env python3
"""Exercise the local test store through authenticated HTTP, without raw DB writes.

Creates explicitly labelled demo records. Requires local .env credentials and an
already running stack. All supplier transactions and payments are simulated.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values


def check(args):
    env = dotenv_values(".env")
    args.api = args.api or env.get("VITE_API_URL", "http://localhost:8000")
    buyer_token = str(uuid4()) + str(uuid4())
    started = datetime.now(timezone.utc)
    evidence = {
        "checked_at": started.isoformat(),
        "environment": "local development",
        "payments": "test only",
        "supplier": "simulated",
        "physical_hardware_tested": False,
        "checks": [],
    }
    with httpx.Client(base_url=args.api, timeout=20) as client:

        def call(method, path, body=None, buyer=False):
            headers = {"Idempotency-Key": str(uuid4())}
            if buyer:
                headers["Authorization"] = "Bearer " + buyer_token
            response = client.request(method, path, json=body, headers=headers)
            if not response.is_success:
                raise RuntimeError(
                    f"{method} {path}: {response.status_code} {response.text[:600]}"
                )
            return response.json()

        call(
            "POST",
            "/api/auth/signin",
            {
                "login": env.get("SHELFIQ_BOOTSTRAP_EMAIL", "admin@admin.com"),
                "password": env.get("SHELFIQ_BOOTSTRAP_PASSWORD", ""),
                "remember": False,
            },
        )
        setting = call("GET", "/api/commerce/settings")
        call(
            "PUT",
            "/api/commerce/settings",
            {
                k: v
                for k, v in {**setting, "commerce_enabled": True}.items()
                if k != "timezone"
            },
        )
        suppliers = call("GET", "/api/suppliers")["rows"]
        supplier = next(
            (s for s in suppliers if s["name"] == "Demo supplier (simulated)"), None
        )
        if not supplier:
            supplier = call(
                "POST", "/api/suppliers", {"name": "Demo supplier (simulated)"}
            )
        products = call("GET", "/api/commerce/products")["rows"]
        product = next(
            p
            for p in products
            if p["current_price"] and p["unit_cost"] and p["stock_on_hand"] is not None
        )
        pid = product["product_id"]
        call(
            "PUT",
            "/api/commerce/products/" + pid,
            {
                "supplier_id": supplier["supplier_id"],
                "online": True,
                "description": product["description"]
                or "ShelfIQ software demonstration product.",
                "online_price": product["online_price"],
                "images": product["images"],
            },
        )
        if product["stock_on_hand"] < 5:
            call(
                "POST",
                "/api/inventory/movements",
                {
                    "product_id": pid,
                    "type": "adjustment",
                    "quantity": 10,
                    "note": "Demo replenishment for commerce acceptance test",
                },
            )
        catalogue = call("POST", "/api/storefront/catalog/search", {}, buyer=True)
        assert any(p["id"] == pid for p in catalogue["products"])
        evidence["checks"].append(
            {"name": "public_catalogue", "pass": True, "product_id": pid}
        )
        checkout = call(
            "POST",
            "/api/storefront/checkout-sessions",
            {
                "line_items": [{"item": {"id": pid}, "quantity": 5}],
                "buyer": {"email": "demo-buyer@example.test"},
                "fulfillment": {"method": "pickup"},
                "channel": "storefront",
            },
            buyer=True,
        )
        sid = checkout["id"]
        price = checkout["line_items"][0]["item"]["price"]
        offer = call(
            "POST",
            f"/api/storefront/checkout-sessions/{sid}/offers",
            {
                "line_items": [
                    {
                        "id": pid,
                        "quantity": 5,
                        "unit_price": {"amount": price, "currency": "USD"},
                    }
                ],
                "note": "End-to-end software acceptance test.",
            },
            buyer=True,
        )
        if offer["status"] == "pending_review":
            call(
                "POST",
                "/api/decisions/" + offer["decision_id"] + "/verdict",
                {
                    "action": "approve",
                    "reason": "Accept the listed price for a test checkout.",
                    "tags": ["agree"],
                },
            )
        paid = call(
            "POST",
            f"/api/storefront/checkout-sessions/{sid}/complete",
            {"payment_token": "test_approved"},
            buyer=True,
        )
        oid = paid["order"]["id"]
        evidence["checks"].append(
            {
                "name": "offer_owner_review_checkout",
                "pass": True,
                "checkout_id": sid,
                "offer_id": offer["offer_id"],
                "order_id": oid,
            }
        )
        for kind, n in [
            ("shipped", 3),
            ("delivered", 3),
            ("canceled", 2),
            ("returned_to_sender", 1),
        ]:
            call(
                "POST",
                f"/api/orders/{oid}/events",
                {"type": kind, "line_items": [{"id": pid, "quantity": n}]},
            )
        order = call("GET", "/api/storefront/orders/" + oid, buyer=True)
        assert order["line_items"][0]["returned"] == 1
        assert order["line_items"][0]["canceled"] == 2
        evidence["checks"].append(
            {
                "name": "partial_fulfillment_cancellation_return",
                "pass": True,
                "order_status": order["status"],
            }
        )
        po = call(
            "POST",
            "/api/purchase-orders",
            {
                "supplier_id": supplier["supplier_id"],
                "lines": [
                    {
                        "product_id": pid,
                        "quantity": 4,
                        "unit_cost": product["unit_cost"],
                    }
                ],
            },
        )
        poid = po["po_id"]
        placed = call("POST", f"/api/purchase-orders/{poid}/order", {})
        assert placed["supplier_order_id"].startswith("sim-")
        for n in [1, 3]:
            receipt = call(
                "POST",
                f"/api/purchase-orders/{poid}/receive",
                {"lines": [{"product_id": pid, "quantity_received": n}]},
            )
        assert receipt["status"] == "received"
        evidence["checks"].append(
            {
                "name": "supplier_checkout_and_partial_receipts",
                "pass": True,
                "po_id": poid,
                "supplier_order_id": placed["supplier_order_id"],
            }
        )
        report = call("GET", "/api/accounting/summary")
        assert report["orders"] >= 1
        evidence["checks"].append(
            {
                "name": "accounting_and_feedback",
                "pass": True,
                "summary": report,
                "feedback_rows": call("GET", "/api/agent/feedback")["total"],
            }
        )
        run = call("POST", "/api/agent/runs", {})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            run = call("GET", "/api/agent/runs/" + run["run_id"])
            if run["status"] in ("succeeded", "failed"):
                break
            time.sleep(2)
        assert run["status"] == "succeeded", run
        evidence["checks"].append(
            {"name": "nightly_seven_step_agent", "pass": True, "run": run}
        )
        notification_deadline = time.monotonic() + 30
        while True:
            status = call("GET", "/api/commerce/status")
            if (
                not status["webhooks"]["configured"]
                or not status["webhooks"]["queued"]
                or time.monotonic() > notification_deadline
            ):
                break
            time.sleep(1)
        assert status["webhooks"]["configured"], (
            "Configure COMMERCE_WEBHOOK_KEY for the signed receiver acceptance check."
        )
        assert status["webhooks"]["queued"] == 0, status["webhooks"]
        evidence["checks"].append(
            {
                "name": "signed_order_notifications",
                "pass": True,
                "webhooks": status["webhooks"],
            }
        )
        evidence["passed"] = True
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "checks": len(evidence["checks"]),
                "artifact": str(path),
                "order_id": oid,
                "run_id": run["run_id"],
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api")
    parser.add_argument(
        "--output", default="artifacts/verification/commerce-readiness.json"
    )
    args = parser.parse_args()
    try:
        check(args)
    except Exception as exc:
        failed = {
            "passed": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "physical_hardware_tested": False,
        }
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(failed, indent=2) + "\n")
        print(json.dumps(failed, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
