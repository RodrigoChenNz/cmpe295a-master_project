#!/usr/bin/env python3
"""Start/status/stop a free public demo using only the isolated HTTPS-test store.

No paid resources are created. Temporary URLs change after tunnel restart.
Credentials stay in ignored local files and are never printed.
"""

import argparse
import json
import re
import secrets
import subprocess
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env.demo.public"
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ENV),
    "-f",
    "infra/docker-compose.deploy.yml",
    "-f",
    "infra/docker-compose.https-test.yml",
    "-f",
    "infra/docker-compose.public-demo.yml",
]


def compose(*args, capture=False):
    return subprocess.run(
        COMPOSE + list(args),
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def save(values):
    ENV.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    ENV.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "stop"])
    args = parser.parse_args()
    if not ENV.exists():
        if args.action != "start":
            raise SystemExit("No public demo has been configured.")
        if not (ROOT / ".env.deploy.local").exists():
            raise SystemExit(
                "Run make https-keys and provision broker identities first."
            )
        values = dotenv_values(ROOT / ".env.deploy.local")
        values.update(
            PUBLIC_URL="https://demo.shelfiq.test",
            SCHEMA_ORIGIN="https://demo.shelfiq.test",
            PLATFORM_URL="https://platform.shelfiq.test",
            PUBLIC_DEMO_READY="false",
            DEMO_RECEIVER_TOKEN=secrets.token_urlsafe(48),
        )
        save(values)
    if args.action == "stop":
        compose("stop", "tunnel", "platform-tunnel")
        print("Both public tunnels stopped. Local data was preserved.")
        return
    if args.action == "start":
        (ROOT / "secrets/ucp/demo-receipts").mkdir(exist_ok=True)
        compose("up", "-d", "--build", "--wait")
        found = {}
        for _ in range(40):
            for name in ("tunnel", "platform-tunnel"):
                output = compose("logs", "--no-color", name, capture=True)
                urls = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", output)
                if urls:
                    found[name] = urls[-1]
            if len(found) == 2:
                break
            time.sleep(2)
        if len(found) != 2:
            compose("stop", "tunnel", "platform-tunnel")
            raise SystemExit(
                "Could not allocate both public URLs. Tunnels stopped; inspect their logs."
            )
        values = dotenv_values(ENV)
        values.update(
            PUBLIC_URL=found["tunnel"],
            SCHEMA_ORIGIN=found["tunnel"],
            PLATFORM_URL=found["platform-tunnel"],
            PUBLIC_DEMO_READY="true",
        )
        save(values)
        compose("up", "-d", "--no-deps", "--wait", "api", "platform")
    values = dotenv_values(ENV)
    running = compose(
        "ps", "--status", "running", "--services", capture=True
    ).splitlines()
    print(
        json.dumps(
            {
                "public_active": all(
                    name in running for name in ("tunnel", "platform-tunnel")
                ),
                "storefront": values["PUBLIC_URL"] + "/shop",
                "staff_console": values["PUBLIC_URL"],
                "platform_profile": values["PLATFORM_URL"] + "/.well-known/ucp",
                "environment": "synthetic HTTPS-test database",
                "stop": ".venv/bin/python tools/public_demo.py stop",
                "availability": "Mac, Docker and both tunnels must stay running",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
