"""Verify the integrity and offline reference closure of the pinned UCP registry."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urldefrag

ROOT = Path(__file__).resolve().parents[1] / "vendor/ucp/2026-08-25"


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    documents = {}
    for name, source in manifest.items():
        raw = (ROOT / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"Vendored UCP checksum mismatch: {name}")
        documents[source["url"]] = json.loads(raw)

    def visit(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("https://") and urldefrag(ref)[0] not in documents:
                raise ValueError(f"Unresolved offline schema: {ref}")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for document in documents.values():
        visit(document)
    print(f"Verified {len(documents)} pinned UCP schemas and their offline references.")


if __name__ == "__main__":
    main()
