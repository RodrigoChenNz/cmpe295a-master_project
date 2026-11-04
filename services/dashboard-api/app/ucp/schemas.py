"""Offline official schemas; runtime resolution never makes network requests."""

import copy
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

VERSION = "2026-08-25"
ORIGIN = f"https://ucp.dev/{VERSION}/"
ROOT = next(
    (
        p / "vendor/ucp" / VERSION
        for p in Path(__file__).resolve().parents
        if (p / "vendor/ucp" / VERSION).is_dir()
    ),
    Path("/srv/vendor/ucp") / VERSION,
)


def request_schema(schema, operation):
    if isinstance(schema, list):
        return [request_schema(v, operation) for v in schema]
    if not isinstance(schema, dict):
        return schema
    result = {k: request_schema(v, operation) for k, v in schema.items()}
    original_required = result.get("required", [])
    if not isinstance(original_required, list):
        return result
    required = set(original_required)
    for name, prop in result.get("properties", {}).items():
        annotation = prop.get("ucp_request")
        if isinstance(annotation, dict):
            annotation = annotation.get(operation)
        if annotation == "required":
            required.add(name)
        elif annotation in ("optional", "omit"):
            required.discard(name)
        # Read-only properties can be returned wholesale by clients; ignored by
        # business operations. They do not retain response constraints on requests.
        if annotation == "omit":
            result["properties"][name] = {}
    if "required" in result:
        result["required"] = sorted(required)
    elif required:
        result["required"] = sorted(required)
    return result


@lru_cache(maxsize=8)
def registry(operation=None):
    resources = []
    for path in ROOT.rglob("*.json"):
        value = json.loads(path.read_text())
        if "$id" not in value:
            continue
        if operation:
            value = request_schema(value, operation)
        resources.append((value["$id"], Resource.from_contents(value)))
    return Registry().with_resources(resources)


def validate(path, payload, *, operation=None):
    schema = {"$ref": ORIGIN + "schemas/" + path}
    errors = list(
        Draft202012Validator(
            schema, registry=registry(operation), format_checker=FormatChecker()
        ).iter_errors(payload)
    )
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "$"
        raise ValueError(f"{location}: {error.message}")


def document(path):
    return copy.deepcopy(json.loads((ROOT / "schemas" / path).read_text()))
