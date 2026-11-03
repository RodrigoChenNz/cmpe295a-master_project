"""Live demo acceptance: fresh pipeline activity plus an authenticated render round trip."""

import argparse
import asyncio
import contextlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from dotenv import dotenv_values
from smoke import main as smoke


async def check(args):
    env = dotenv_values(".env")
    api_url = (
        args.api
        or os.getenv("API_URL")
        or env.get("VITE_API_URL", "http://localhost:8000")
    )
    email = os.getenv("SHELFIQ_BOOTSTRAP_EMAIL") or env.get("SHELFIQ_BOOTSTRAP_EMAIL")
    password = os.getenv("SHELFIQ_BOOTSTRAP_PASSWORD") or env.get(
        "SHELFIQ_BOOTSTRAP_PASSWORD"
    )
    if not email or not password:
        raise ValueError(
            "Configure the local account in .env before checking readiness"
        )
    result = dict(
        checked_at=datetime.now(timezone.utc).isoformat(),
        api=api_url,
        scope="Software demo acceptance; physical hardware is not certified",
        checks={},
    )
    async with httpx.AsyncClient(base_url=api_url, timeout=20) as api:
        response = await api.post(
            "/api/auth/signin", json={"login": email, "password": password}
        )
        response.raise_for_status()
        deadline = asyncio.get_running_loop().time() + args.wait_seconds
        while True:
            health = await api.get("/health")
            health.raise_for_status()
            status = await api.get("/api/system")
            status.raise_for_status()
            health, status = health.json(), status.json()
            checks = dict(
                database=health.get("database") is True,
                broker=health.get("broker", {}).get("connected") is True,
            )
            stages = status.get("pipeline", [])
            for stage in ["observations", "forecasting", "pricing", "receiver"]:
                checks[stage] = any(s["stage"] == stage and s["recent"] for s in stages)
            policy = os.getenv("PRICING_POLICY") or env.get("PRICING_POLICY", "rule")
            checks["configured_policy"] = (
                status.get("latest_pricing_model") or ""
            ).startswith(policy + "-")
            checks["outbox_drained"] = status["counts"]["queued"] == 0
            result.update(
                checks=checks,
                pipeline=stages,
                latest_pricing_model=status.get("latest_pricing_model"),
                counts=status["counts"],
                mqtt_tls=health["broker"].get("tls", False),
            )
            if all(checks.values()) or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(1)
    result["ready"] = all(result["checks"].values())
    if not args.no_smoke and result["ready"]:
        # Use the existing pricing identity to recommend; only the API publishes prices.
        if result["mqtt_tls"]:
            for name, value in dict(
                MQTT_PORT=health["broker"]["port"],
                MQTT_TLS="true",
                MQTT_CA_FILE="secrets/certs/ca.pem",
                MQTT_CERT_FILE="secrets/certs/pricing.pem",
                MQTT_KEY_FILE="secrets/certs/pricing.key",
            ).items():
                os.environ.setdefault(name, str(value))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            await smoke(SimpleNamespace(api=api_url, email=email, password=password))
        result["round_trip"] = json.loads(captured.getvalue())
        result["ready"] = result["round_trip"].get("result") == "confirmed"
    result["acceptance"] = (
        "passed"
        if result["ready"] and not args.no_smoke
        else "read_only_checks_passed"
        if result["ready"]
        else "failed"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api")
    parser.add_argument("--wait-seconds", type=float, default=30)
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="Read-only checks; no approval or render test",
    )
    parser.add_argument("--output", default="artifacts/verification/readiness.json")
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args))
    except Exception as exc:
        result = dict(
            ready=False, acceptance="failed", error=f"{type(exc).__name__}: {exc}"
        )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
