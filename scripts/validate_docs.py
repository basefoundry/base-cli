#!/usr/bin/env python3
"""Check repository Markdown links and compile example programs."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")
PUBLIC_DOCS = frozenset(
    {
        "adopter-readiness.md",
        "adoption-evidence.md",
        "api-reference.md",
        "api-stability.md",
        "cache-ownership-and-layout.md",
        "compatibility-dashboard.md",
        "consumer-profiles.md",
        "coverage-policy.md",
        "dependency-support.md",
        "extensions.md",
        "framework-choice.md",
        "index.md",
        "integrations.md",
        "json-contracts.md",
        "local-config.md",
        "migration-argparse.md",
        "migration-cement.md",
        "migration-click.md",
        "migration-typer.md",
        "migrations.md",
        "output-contracts.md",
        "performance.md",
        "platform-support.md",
        "releasing.md",
        "security-review.md",
        "security-threat-model.md",
        "schemas.md",
        "typer-adapter.md",
        "user-config-typing.md",
    }
)


def fail(message: str) -> None:
    print(f"documentation validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_unexpected_docs(root: Path) -> list[Path]:
    """Return Markdown files under ``docs/`` that are outside the public nav."""
    docs_root = root / "docs"
    return sorted(
        (
            path.relative_to(root)
            for path in docs_root.rglob("*.md")
            if path.relative_to(docs_root).as_posix() not in PUBLIC_DOCS
        ),
        key=lambda path: path.as_posix(),
    )


def validate_links(root: Path) -> None:
    unexpected_docs = find_unexpected_docs(root)
    if unexpected_docs:
        paths = ", ".join(path.as_posix() for path in unexpected_docs)
        fail(f"Markdown files outside declared public navigation: {paths}")
    markdown_files = [
        root / "README.md",
        root / "CONTRIBUTING.md",
        root / "SECURITY.md",
        *sorted((root / "docs").rglob("*.md")),
        *sorted((root / "examples").rglob("*.md")),
        *sorted((root / "compatibility").rglob("*.md")),
    ]
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
    for example in sorted((root / "examples").rglob("*.py")):
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
