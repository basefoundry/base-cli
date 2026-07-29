#!/usr/bin/env python3
"""Validate the distributions produced for a base-cli release."""

from __future__ import annotations

import argparse
import email
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import NoReturn


PACKAGE_NAME = "base-cli"
IMPORT_NAME = "base_cli"
MINIMUM_PYTHON = ">=3.10"
REQUIRED_DEPENDENCIES = ("click>=8.1", "PyYAML>=6.0")


def fail(message: str) -> NoReturn:
    print(f"artifact validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_expected_version() -> str:
    version_path = Path(__file__).resolve().parents[1] / "VERSION"
    lines = version_path.read_text(encoding="utf-8").splitlines()
    if not lines or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?", lines[0].strip()):
        fail(f"invalid VERSION file: {version_path}")
    return lines[0].strip()


def validate_wheel(path: Path, expected_version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            fail(f"{path.name} must contain exactly one dist-info METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))

        expected_headers = {
            "Name": PACKAGE_NAME,
            "Version": expected_version,
            "License": "Apache-2.0",
            "Requires-Python": MINIMUM_PYTHON,
        }
        for header, expected in expected_headers.items():
            if metadata.get(header) != expected:
                fail(f"{path.name} has {header}={metadata.get(header)!r}; expected {expected!r}")

        dependencies = set(metadata.get_all("Requires-Dist", []))
        for dependency in REQUIRED_DEPENDENCIES:
            if dependency not in dependencies:
                fail(f"{path.name} is missing runtime dependency {dependency!r}")

        if f"{IMPORT_NAME}/py.typed" not in names:
            fail(f"{path.name} does not contain {IMPORT_NAME}/py.typed")
        if not any(
            name.endswith(".dist-info/LICENSE") or name.endswith(".dist-info/licenses/LICENSE")
            for name in names
        ):
            fail(f"{path.name} does not contain the packaged LICENSE file")
        if any(name.startswith("tests/") or f"/{IMPORT_NAME}/tests/" in name for name in names):
            fail(f"{path.name} contains repository test files")


def validate_sdist(path: Path, expected_version: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = [member.name for member in archive.getmembers()]
        required_suffixes = {"pyproject.toml", "README.md", "LICENSE", "VERSION"}
        present_suffixes = {name.rsplit("/", 1)[-1] for name in names}
        missing = required_suffixes - present_suffixes
        if missing:
            fail(f"{path.name} is missing sdist files: {', '.join(sorted(missing))}")
        version_members = [member for member in archive.getmembers() if member.name.endswith("/VERSION")]
        if len(version_members) != 1:
            fail(f"{path.name} must contain exactly one VERSION file")
        version_text = archive.extractfile(version_members[0])
        if version_text is None or version_text.read().decode().splitlines()[0].strip() != expected_version:
            fail(f"{path.name} VERSION does not match {expected_version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="directory containing the built distributions")
    args = parser.parse_args()
    if not args.dist.is_dir():
        fail(f"distribution directory does not exist: {args.dist}")

    expected_version = read_expected_version()
    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        fail(f"expected one wheel and one sdist, found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)")
    expected_stem = f"base_cli-{expected_version}"
    if not wheels[0].name.startswith(expected_stem) or not sdists[0].name.startswith(expected_stem):
        fail(f"artifact filenames do not match version {expected_version}")

    validate_wheel(wheels[0], expected_version)
    validate_sdist(sdists[0], expected_version)
    print(f"Validated {PACKAGE_NAME} {expected_version}: wheel, sdist, metadata, package data, and test boundary.")


if __name__ == "__main__":
    main()
