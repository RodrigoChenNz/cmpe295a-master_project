"""Validate schema definitions and example payloads, including UUIDs and money."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retail.contracts import validator, validate

for path in sorted(Path("contracts").glob("*.schema.json")):
    family = path.name.split(".")[0]
    validator(family)
    for example in json.loads(path.read_text()).get("examples", []):
        validate(family, example)
    print("ok", path)
