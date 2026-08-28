#!/usr/bin/env python3
"""Validate and compare the packaged and documentation JSON Schema artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

SCHEMA_ROOT = Path("schemas/v1")
REQUIRED_CONTRACT_KEYS = {"$schema", "$id", "title", "type", "required", "properties"}


def fail(message: str) -> NoReturn:
    print(f"schema validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot parse {path}: {exc}")


def validate(root: Path) -> None:
    package_root = root / "lib/python/base_cli" / SCHEMA_ROOT
    docs_root = root / "docs" / SCHEMA_ROOT
    package_files = sorted(package_root.glob("*.json"))
    docs_files = sorted(docs_root.glob("*.json"))
    if not package_files:
        fail(f"no schemas found under {package_root}")
    if [path.name for path in package_files] != [path.name for path in docs_files]:
        fail("packaged and documentation schema filenames differ")

    for package_path, docs_path in zip(package_files, docs_files, strict=True):
        package_payload = _load(package_path)
        docs_payload = _load(docs_path)
        if package_payload != docs_payload:
            fail(f"packaged and documentation schemas differ for {package_path.name}")
        if not isinstance(package_payload, dict) or not REQUIRED_CONTRACT_KEYS <= package_payload.keys():
            fail(f"{package_path.name} is missing required schema metadata")
        if package_payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            fail(f"{package_path.name} must use JSON Schema draft 2020-12")
        if package_payload.get("type") != "object":
            fail(f"{package_path.name} must describe an object")
        if not isinstance(package_payload.get("required"), list):
            fail(f"{package_path.name} must declare required fields")
        print(f"Validated {package_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    validate(parser.parse_args().root)


if __name__ == "__main__":
    main()
