"""Exercise the running stack through MQTT, authenticated approval, and render ACK."""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiomqtt
import asyncpg
import httpx
from dotenv import dotenv_values
from retail.contracts import dumps, validate
from retail.mqtt import options


async def main(args):
    env = dotenv_values(".env")
    email = (
        args.email
        or os.getenv("SHELFIQ_BOOTSTRAP_EMAIL")
        or env.get("SHELFIQ_BOOTSTRAP_EMAIL")
    )
    password = (
        args.password
        or os.getenv("SHELFIQ_BOOTSTRAP_PASSWORD")
        or env.get("SHELFIQ_BOOTSTRAP_PASSWORD")
    )
    if not email or not password:
        raise RuntimeError(
            "Supply --email and --password or local .env account credentials"
        )
    conn = await asyncpg.connect(
        os.getenv(
            "DATABASE_URL",
            env.get(
                "DATABASE_URL",
                "postgresql://retail:retail_dev_only@localhost:5432/retail_pricing",
            ),
        )
    )
    try:
        async with httpx.AsyncClient(base_url=args.api, timeout=15) as api:
            response = await api.post(
                "/api/auth/signin", json={"email": email, "password": password}
            )
            response.raise_for_status()
            row = await conn.fetchrow("""SELECT p.product_id,h.price,l.label_id FROM products p
                JOIN shelf_labels l ON l.product_id=p.product_id
                JOIN LATERAL(SELECT price FROM price_history WHERE product_id=p.product_id ORDER BY id DESC LIMIT 1)h ON TRUE
                WHERE (p.min_price IS NULL OR h.price>=p.min_price) AND (p.max_price IS NULL OR h.price<=p.max_price)
                ORDER BY p.product_id LIMIT 1""")
            if row is None:
                raise RuntimeError(
                    "Create a product with a current price and pair one label before running smoke"
                )
            identifier = str(uuid4())
            now = datetime.now(timezone.utc)
            amount = float(row["price"])
            p = dict(
                product_id=row["product_id"],
                decision_id=identifier,
                current_price=amount,
                proposed_price=amount,
                currency="USD",
                reason_tag="Software check",
                agent_version="smoke-v1",
                ts=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
            )
            e = dict(
                product_id=row["product_id"],
                decision_id=identifier,
                base_value=amount,
                shap=[{"feature": "current_price", "value": 0}],
                ts=now.isoformat(),
            )
            validate("proposal", p)
            validate("explain", e)
            started = time.perf_counter()
            async with aiomqtt.Client(
                os.getenv("MQTT_HOST", "localhost"),
                port=int(os.getenv("MQTT_PORT", "1883")),
                **options(),
            ) as mqtt:
                # Early explanation deliberately tests durable out-of-order handling.
                await mqtt.publish(
                    "retail/explain/" + row["product_id"], dumps(e), qos=1
                )
                await mqtt.publish(
                    "retail/proposal/" + row["product_id"], dumps(p), qos=1
                )
                for _ in range(100):
                    status = await conn.fetchval(
                        "SELECT status FROM proposals WHERE decision_id=$1", identifier
                    )
                    if status:
                        break
                    await asyncio.sleep(0.1)
                if status == "pending":
                    response = await api.post(
                        f"/api/suggestions/{identifier}/approve", json={}
                    )
                    response.raise_for_status()
                elif status != "auto_approved":
                    raise RuntimeError(f"Smoke proposal was not actionable: {status}")
                for _ in range(150):
                    result = await conn.fetchrow(
                        """SELECT lp.publication_id,lp.result,l.emulated,l.rendered_line1,l.rendered_line2
                        FROM label_pushes lp JOIN shelf_labels l USING(label_id)
                        WHERE lp.decision_id=$1 ORDER BY lp.id DESC LIMIT 1""",
                        identifier,
                    )
                    if result and result["result"] == "confirmed":
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError(
                        "No matching render acknowledgement within 15 seconds"
                    )
                assert await conn.fetchval(
                    "SELECT 1 FROM shap_vectors WHERE decision_id=$1", identifier
                )
                output = dict(
                    decision_id=identifier,
                    product_id=row["product_id"],
                    **dict(result),
                    elapsed_seconds=round(time.perf_counter() - started, 3),
                    scope="software integration; not physical hardware validation",
                )
                print(json.dumps(output, indent=2))
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--api",
        default=os.getenv("API_URL")
        or dotenv_values(".env").get("VITE_API_URL", "http://localhost:8000"),
    )
    p.add_argument("--email")
    p.add_argument("--password")
    asyncio.run(main(p.parse_args()))
