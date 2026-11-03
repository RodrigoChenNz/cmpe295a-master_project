#!/usr/bin/env python3
"""Configure private local order-hook signing without printing secret values."""

from pathlib import Path
import secrets


def main():
    target = Path(".env")
    text = target.read_text() if target.exists() else ""
    configured = False
    for line in text.splitlines():
        if (
            line.startswith("COMMERCE_WEBHOOK_KEY=")
            and len(line.split("=", 1)[1].strip()) >= 32
        ):
            configured = True
    if not configured:
        lines = [
            line
            for line in text.splitlines()
            if not line.startswith("COMMERCE_WEBHOOK_KEY=")
        ]
        text = (
            "\n".join(lines)
            + "\nCOMMERCE_WEBHOOK_KEY="
            + secrets.token_urlsafe(48)
            + "\n"
        )
        target.write_text(text)
        target.chmod(0o600)
    print(
        "Private local order-hook key is configured in .env. Recreate the API container to load it. No real payment provider is enabled."
    )


if __name__ == "__main__":
    main()
