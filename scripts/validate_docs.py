#!/usr/bin/env python3
"""Check repository Markdown links and compile example programs."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def fail(message: str) -> None:
    print(f"documentation validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_links(root: Path) -> None:
    markdown_files = [root / "README.md", root / "CONTRIBUTING.md", *sorted((root / "docs").glob("*.md"))]
    for document in markdown_files:
        text = document.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.strip().split("#", 1)[0].strip("<>")
            if not target or target.startswith(SKIP_PREFIXES):
                continue
            if target.startswith("/"):
                candidate = root / target.lstrip("/")
            else:
                candidate = (document.parent / target).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                fail(f"{document.relative_to(root)} links outside the repository: {raw_target}")
            if not candidate.exists():
                fail(f"{document.relative_to(root)} links to missing path: {raw_target}")


def validate_examples(root: Path) -> None:
    for example in sorted((root / "examples").glob("*.py")):
        try:
            ast.parse(example.read_text(encoding="utf-8"), filename=str(example))
        except SyntaxError as exc:
            fail(f"example {example.relative_to(root)} is not valid Python: {exc}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    validate_links(root)
    validate_examples(root)
    print("Validated Markdown links and Python examples.")


if __name__ == "__main__":
    main()
