#!/usr/bin/env python3
"""Validate release-facing documentation before building a tagged package."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?$")
RELEASE_ROW_PATTERN = re.compile(
    r"^\| `(?P<version>[^`]+)` \| \[Apache-2\.0\]\(LICENSE\) \| "
    r"`python -m pip install base-cli` \| \[v(?P<link_version>[^]]+)\]"
    r"\(https://github\.com/basefoundry/base-cli/releases/tag/v(?P<tag_version>[^)]+)\) \|$"
)


def validate_release_docs(version_path: Path, readme_path: Path, tag: str = "") -> list[str]:
    """Return release documentation violations for *version_path* and README."""

    errors: list[str] = []
    version = version_path.read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        errors.append(f"VERSION is not a valid release version: {version!r}")
        return errors
    expected_tag = f"v{version}"
    if tag and tag != expected_tag:
        errors.append(f"release tag {tag!r} does not match VERSION {version!r}")

    readme = readme_path.read_text(encoding="utf-8")
    rows = [RELEASE_ROW_PATTERN.match(line) for line in readme.splitlines()]
    matching_rows = [match for match in rows if match is not None]
    if len(matching_rows) != 1:
        errors.append("README.md must contain exactly one canonical release row")
    else:
        row = matching_rows[0]
        assert row is not None
        values = (row.group("version"), row.group("link_version"), row.group("tag_version"))
        if values != (version, version, version):
            errors.append(f"README.md release row versions {values!r} do not match {version!r}")

    lowered = readme.casefold()
    for forbidden in ("source release candidate", "publication is pending"):
        if forbidden in lowered:
            errors.append(f"README.md still contains pre-publication wording: {forbidden!r}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    args = parser.parse_args()
    errors = validate_release_docs(args.version_file, args.readme, args.tag)
    if errors:
        for error in errors:
            print(f"release documentation validation failed: {error}")
        raise SystemExit(1)
    version = args.version_file.read_text(encoding="utf-8").strip()
    print(f"Validated release-facing documentation for {args.tag or version}.")


if __name__ == "__main__":
    main()
