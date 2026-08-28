#!/usr/bin/env python3
"""Validate golden contract fixtures against the versioned JSON Schemas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

SCHEMA_DIR = Path("lib/python/base_cli/schemas/v1")
FIXTURE_DIR = Path("tests/fixtures/contracts")
FIXTURE_SCHEMAS = {
    "output-success.json": "output.schema.json",
    "error-usage.json": "error.schema.json",
    "inspection-warn.json": "inspection.schema.json",
    "log-record.json": "log.schema.json",
    "ndjson-record.json": "ndjson.schema.json",
    "command-protocol.json": "command-protocol.schema.json",
}


def fail(message: str) -> NoReturn:
    print(f"contract fixture validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot parse {path}: {exc}")


def _matches_type(value: Any, schema_type: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(schema_type, True)


def validate_instance(schema: dict[str, Any], value: Any, label: str) -> None:
    if schema.get("type") == "object" and not isinstance(value, dict):
        fail(f"{label} must be an object")
    for field in schema.get("required", []):
        if field not in value:
            fail(f"{label} is missing required field {field!r}")
    if schema.get("additionalProperties") is False:
        unknown = set(value) - set(schema.get("properties", {}))
        if unknown:
            fail(f"{label} has unexpected fields: {', '.join(sorted(unknown))}")
    for field, field_schema in schema.get("properties", {}).items():
        if field not in value:
            continue
        field_value = value[field]
        if "const" in field_schema and field_value != field_schema["const"]:
            fail(f"{label}.{field} does not equal its contract constant")
        allowed = field_schema.get("type")
        if isinstance(allowed, str):
            allowed = [allowed]
        if isinstance(allowed, list) and not any(_matches_type(field_value, item) for item in allowed):
            fail(f"{label}.{field} has an invalid type")
        if "enum" in field_schema and field_value not in field_schema["enum"]:
            fail(f"{label}.{field} is outside its contract enum")


def validate(root: Path) -> None:
    for fixture_name, schema_name in FIXTURE_SCHEMAS.items():
        schema = load_json(root / SCHEMA_DIR / schema_name)
        fixture = load_json(root / FIXTURE_DIR / fixture_name)
        if not isinstance(schema, dict) or not isinstance(fixture, dict):
            fail(f"{fixture_name} and {schema_name} must contain JSON objects")
        validate_instance(schema, fixture, fixture_name)
        print(f"Validated {fixture_name} against {schema_name}")

    invalid = load_json(root / FIXTURE_DIR / "invalid-output-extra-field.json")
    output_schema = load_json(root / SCHEMA_DIR / "output.schema.json")
    try:
        validate_instance(output_schema, invalid, "invalid-output-extra-field.json")
    except SystemExit:
        print("Rejected invalid-output-extra-field.json as expected")
        return
    fail("invalid-output-extra-field.json was accepted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    validate(parser.parse_args().root)


if __name__ == "__main__":
    main()
