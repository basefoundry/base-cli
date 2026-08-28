#!/usr/bin/env python3
"""Validate the repository changelog and its release-link contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSION_HEADING = re.compile(r"^## \[(?P<version>Unreleased|\d+\.\d+\.\d+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?$")
CATEGORY_HEADING = re.compile(r"^### (?P<category>.+?)\s*$")
BULLET = re.compile(r"^\s*[-*+]\s+(?P<text>.+?)\s*$")
REFERENCE_LINK = re.compile(r"^\[(?P<version>Unreleased|\d+\.\d+\.\d+)\]:\s+(?P<url>\S+)\s*$")
INTERNAL_MARKERS = (
    "agentic",
    "superpowers",
    "implementation plan",
    "private workflow",
    "unchecked implementation",
    "codex",
)


def validate_changelog(path: Path) -> list[str]:
    """Return human-readable violations found in ``path``."""
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    nonempty = [(index, line.strip()) for index, line in enumerate(lines) if line.strip()]
    if not nonempty or nonempty[0][1] != "# Changelog":
        errors.append("the first non-empty line must be '# Changelog'")

    sections: list[tuple[str, str | None, int, int]] = []
    for index, line in enumerate(lines):
        match = VERSION_HEADING.fullmatch(line.strip())
        if match is None:
            continue
        if sections:
            previous = sections[-1]
            sections[-1] = (*previous[:3], index)
        sections.append((match.group("version"), match.group("date"), index, len(lines)))

    if not sections:
        errors.append("no version sections found")
        return errors
    if sections[0][0] != "Unreleased":
        errors.append("[Unreleased] must be the first version section")

    versions = [version for version, _date, _start, _end in sections]
    for version in sorted(set(versions)):
        if versions.count(version) > 1:
            errors.append(f"duplicate version section [{version}]")

    for version, date, start, end in sections:
        if version == "Unreleased" and date is not None:
            errors.append("[Unreleased] must not have a release date")
        if version != "Unreleased" and date is None:
            errors.append(f"released section [{version}] is missing a YYYY-MM-DD date")

        categories: dict[str, int] = {}
        bullets: set[str] = set()
        saw_bullet = False
        current_category: str | None = None
        category_has_content = False
        for line_number in range(start + 1, end):
            line = lines[line_number]
            category_match = CATEGORY_HEADING.fullmatch(line.strip())
            if category_match is not None:
                if current_category is not None and not category_has_content:
                    errors.append(f"[{version}] section '{current_category}' is empty")
                current_category = category_match.group("category")
                category_has_content = False
                categories[current_category] = categories.get(current_category, 0) + 1
                if categories[current_category] > 1:
                    errors.append(f"[{version}] has duplicate '{current_category}' sections")
                continue

            bullet_match = BULLET.fullmatch(line)
            if bullet_match is None:
                if line.strip() and not line.lstrip().startswith("["):
                    category_has_content = True
                continue
            saw_bullet = True
            category_has_content = True
            text = " ".join(bullet_match.group("text").split()).casefold()
            if text in bullets:
                errors.append(f"[{version}] contains a duplicate bullet: {bullet_match.group('text')}")
            bullets.add(text)
            if any(marker in text for marker in INTERNAL_MARKERS):
                errors.append(f"[{version}] contains internal planning text: {bullet_match.group('text')}")

        if current_category is not None and not category_has_content:
            errors.append(f"[{version}] section '{current_category}' is empty")
        if not saw_bullet:
            errors.append(f"[{version}] contains no changelog bullets")

    references: dict[str, str] = {}
    for line in lines:
        match = REFERENCE_LINK.fullmatch(line.strip())
        if match is None:
            continue
        version = match.group("version")
        if version in references:
            errors.append(f"duplicate release link [{version}]")
        references[version] = match.group("url")
        if not match.group("url").startswith(("https://", "http://")):
            errors.append(f"release link [{version}] must use an absolute HTTP(S) URL")

    for version in versions:
        if version not in references:
            errors.append(f"missing release link [{version}]")
    for version in references:
        if version not in versions:
            errors.append(f"release link [{version}] has no matching version section")

    return errors


def main() -> None:
    path = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    errors = validate_changelog(path)
    if errors:
        for error in errors:
            print(f"changelog validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated changelog: {path.name}")


if __name__ == "__main__":
    main()
