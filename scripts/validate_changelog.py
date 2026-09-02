#!/usr/bin/env python3
"""Validate the repository changelog and its release-link contract."""

from __future__ import annotations

import re
import subprocess
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


def validate_changelog(path: Path, *, verify_tags: bool | None = None) -> list[str]:
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

    if verify_tags is None:
        verify_tags = (path.parent / ".git").exists()
    if verify_tags:
        errors.extend(_validate_published_sections(path, lines, versions))

    return errors


def _validate_published_sections(
    path: Path,
    lines: list[str],
    versions: list[str],
) -> list[str]:
    """Ensure every released section remains identical to its version tag."""

    errors: list[str] = []
    for version in versions:
        if version == "Unreleased":
            continue
        tag = f"v{version}"
        try:
            completed = subprocess.run(
                ["git", "-C", str(path.parent), "show", f"{tag}:CHANGELOG.md"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            errors.append(
                f"cannot verify [{version}] against tag {tag}: {detail.strip()}; "
                "fetch the release tags before validating"
            )
            continue
        tagged_lines = completed.stdout.splitlines()
        current_section = _section_text(lines, version)
        tagged_section = _section_text(tagged_lines, version)
        if current_section is None:
            continue
        if tagged_section is None:
            errors.append(f"tag {tag} has no [{version}] changelog section")
        elif current_section != tagged_section:
            errors.append(f"published changelog section [{version}] differs from tag {tag}")
    return errors


def _section_text(lines: list[str], version: str) -> str | None:
    """Return one complete version section without trailing blank lines."""

    start: int | None = None
    for index, line in enumerate(lines):
        match = VERSION_HEADING.fullmatch(line.strip())
        if match is not None and match.group("version") == version:
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if VERSION_HEADING.fullmatch(lines[index].strip()) is not None:
            end = index
            break
        # Keep Markdown reference definitions at file scope rather than
        # treating them as part of the final release section.
        if REFERENCE_LINK.fullmatch(lines[index].strip()) is not None:
            end = index
            break
    return "\n".join(lines[start:end]).rstrip()


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
