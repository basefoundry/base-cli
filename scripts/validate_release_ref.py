#!/usr/bin/env python3
"""Validate that a version tag has matching package and release notes."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

RELEASE_HEADING = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\] - (?P<date>\d{4}-\d{2}-\d{2})$")
BULLET = re.compile(r"^\s*[-*+]\s+\S")


def validate_release_ref(version_path: Path, changelog_path: Path, tag: str) -> list[str]:
    """Return violations for a release ``tag`` and its source files."""
    errors: list[str] = []
    if not tag.startswith("v") or tag == "v":
        return [f"release tag must be a v-prefixed version, got {tag!r}"]
    version = tag[1:]
    declared = version_path.read_text(encoding="utf-8").strip()
    if declared != version:
        errors.append(f"VERSION declares {declared!r}, but the release tag is {tag!r}")

    lines = changelog_path.read_text(encoding="utf-8").splitlines()
    heading = f"## [{version}] - "
    heading_index = next(
        (index for index, line in enumerate(lines) if line.startswith(heading)),
        None,
    )
    if heading_index is None or RELEASE_HEADING.fullmatch(lines[heading_index]) is None:
        errors.append(f"CHANGELOG.md is missing a dated release section for [{version}]")
        return errors

    next_section = next(
        (index for index in range(heading_index + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    if not any(BULLET.match(line) for line in lines[heading_index + 1 : next_section]):
        errors.append(f"CHANGELOG.md release section [{version}] has no release-note bullets")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args()
    errors = validate_release_ref(args.version_file, args.changelog, args.tag)
    if errors:
        for error in errors:
            print(f"release reference validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated release reference {args.tag}")


if __name__ == "__main__":
    main()
