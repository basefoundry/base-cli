#!/usr/bin/env python3
"""Validate the installable reference-application contract."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

EXAMPLES = (
    "minimal_cli",
    "nested_click_app",
    "typer_app",
    "automation_observability_app",
)
REQUIRED_SECTIONS = (
    "Install",
    "Configuration",
    "Output and errors",
    "Tests",
    "Completion",
    "Release guidance",
    "Operational troubleshooting",
)
SCRIPT_PATTERN = re.compile(r"^\s*base-[a-z0-9-]+\s*=\s*\"[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+\"\s*$", re.MULTILINE)


def fail(message: str) -> None:
    print(f"reference example validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_python(path: Path) -> None:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail(f"{path} is not valid Python: {exc}")


def validate_example(root: Path, name: str) -> None:
    example = root / "examples" / name
    if not example.is_dir():
        fail(f"missing example directory: {example.relative_to(root)}")
    pyproject = example / "pyproject.toml"
    readme = example / "README.md"
    tests = example / "tests"
    sources = example / "src"
    for required in (pyproject, readme, tests, sources):
        if not required.exists():
            fail(f"{required.relative_to(root)} is required")
    metadata = pyproject.read_text(encoding="utf-8")
    if "base-cli" not in metadata or "[project.scripts]" not in metadata:
        fail(f"{pyproject.relative_to(root)} must declare base-cli and a console script")
    if not SCRIPT_PATTERN.search(metadata):
        fail(f"{pyproject.relative_to(root)} has no base-* console script")
    document = readme.read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        if f"## {section}" not in document:
            fail(f"{readme.relative_to(root)} is missing the '{section}' section")
    if not any(path.suffix == ".py" for path in tests.rglob("*.py")):
        fail(f"{tests.relative_to(root)} must contain at least one test module")
    for path in example.rglob("*.py"):
        validate_python(path)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in EXAMPLES:
        validate_example(root, name)
    print(f"Validated {len(EXAMPLES)} installable reference applications.")


if __name__ == "__main__":
    main()
