"""One validator for publishers, ingestion, tests, and contract examples."""

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def validator(family):
    path = ROOT / "contracts" / f"{family}.schema.json"
    schema = json.loads(path.read_text(), parse_float=Decimal)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def loads(raw):
    def reject(value):
        raise ValueError(f"Non-finite JSON value: {value}")

    return json.loads(raw, parse_float=Decimal, parse_constant=reject)


def validate(family, payload):
    # Round-trip decimal parsing keeps multipleOf 0.01 mathematically exact.
    if not isinstance(payload, dict):
        raise ValueError("Payload must be an object")
    validator(family).validate(loads(dumps(payload)))
    return payload


def dumps(payload):
    return json.dumps(
        payload,
        default=lambda v: float(v) if isinstance(v, Decimal) else str(v),
        allow_nan=False,
        separators=(",", ":"),
    )
