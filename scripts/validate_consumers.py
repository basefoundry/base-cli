#!/usr/bin/env python3
"""Validate the three independent downstream consumer fixtures."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

EXPECTED_SLUGS = ("atlas_click", "beacon_typer", "cinder_automation")
SCRIPT_PATTERN = re.compile(r"^\s*[a-z0-9-]+-consumer\s*=\s*\"[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+\"\s*$", re.MULTILINE)


def fail(message: str) -> None:
    print(f"consumer compatibility validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_python(path: Path) -> None:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail(f"{path} is not valid Python: {exc}")


def validate_consumer(root: Path, record: dict[str, object]) -> None:
    slug = record.get("slug")
    if not isinstance(slug, str) or slug not in EXPECTED_SLUGS:
        fail(f"manifest contains an unknown consumer slug: {slug!r}")
    directory = root / "compatibility" / "consumers" / slug
    for filename in ("pyproject.toml", "README.md", "src", "tests"):
        if not (directory / filename).exists():
            fail(f"{directory / filename} is required")
    metadata = (directory / "pyproject.toml").read_text(encoding="utf-8")
    if "base-cli" not in metadata or "[project.scripts]" not in metadata:
        fail(f"{directory / 'pyproject.toml'} must depend on base-cli and expose a script")
    if not SCRIPT_PATTERN.search(metadata):
        fail(f"{directory / 'pyproject.toml'} has no consumer console script")
    readme = (directory / "README.md").read_text(encoding="utf-8").lower()
    if "base-cli" not in readme or "install" not in readme or "test" not in readme:
        fail(f"{directory / 'README.md'} must document installation and testing")
    if not any(path.suffix == ".py" for path in (directory / "tests").rglob("*.py")):
        fail(f"{directory / 'tests'} must contain tests")
    for path in directory.rglob("*.py"):
        validate_python(path)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "compatibility" / "consumers" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"unable to read {manifest_path}: {exc}")
    records = manifest.get("consumers") if isinstance(manifest, dict) else None
    if not isinstance(records, list) or len(records) != len(EXPECTED_SLUGS):
        fail("manifest must contain exactly three consumers")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            fail("each manifest consumer must be an object")
        validate_consumer(root, record)
        seen.add(str(record["slug"]))
    if seen != set(EXPECTED_SLUGS):
        fail(f"manifest slugs must be {EXPECTED_SLUGS!r}")
    print(f"Validated {len(records)} independent downstream consumer fixtures.")


if __name__ == "__main__":
    main()
