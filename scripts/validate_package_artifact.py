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
DOCUMENTATION_URL = "Documentation, https://basefoundry.github.io/base-cli/"
ALLOWED_WHEEL_DIST_INFO_FILES = frozenset({"METADATA", "RECORD", "WHEEL", "top_level.txt", "entry_points.txt"})
ALLOWED_SDIST_FILES = frozenset(
    {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "SECURITY.md",
        "setup.cfg",
        "VERSION",
        "base_manifest.yaml",
        "pyproject.toml",
    }
)
ALLOWED_SDIST_PREFIXES = (
    ".github/",
    "docs/",
    "examples/",
    "compatibility/",
    "lib/python/base_cli/",
    "scripts/",
    "tests/",
    "base_cli.egg-info/",
    "lib/python/base_cli.egg-info/",
)


def fail(message: str) -> NoReturn:
    print(f"artifact validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_expected_version() -> str:
    version_path = Path(__file__).resolve().parents[1] / "VERSION"
    lines = version_path.read_text(encoding="utf-8").splitlines()
    if not lines or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?", lines[0].strip()):
        fail(f"invalid VERSION file: {version_path}")
    return lines[0].strip()


def expected_package_files() -> set[str]:
    package_root = Path(__file__).resolve().parents[1] / "lib" / "python" / IMPORT_NAME
    if not package_root.is_dir():
        fail(f"package source directory does not exist: {package_root}")
    return {path.relative_to(package_root).as_posix() for path in package_root.rglob("*") if path.is_file()}


def validate_wheel(path: Path, expected_version: str, package_files: set[str]) -> None:
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
        if DOCUMENTATION_URL not in metadata.get_all("Project-URL", []):
            fail(f"{path.name} is missing the canonical Documentation project URL")

        if f"{IMPORT_NAME}/py.typed" not in names:
            fail(f"{path.name} does not contain {IMPORT_NAME}/py.typed")
        if not any(
            name.endswith(".dist-info/LICENSE") or name.endswith(".dist-info/licenses/LICENSE") for name in names
        ):
            fail(f"{path.name} does not contain the packaged LICENSE file")
        if any(name.startswith("tests/") or f"/{IMPORT_NAME}/tests/" in name for name in names):
            fail(f"{path.name} contains repository test files")

        dist_info_prefix = metadata_names[0].rsplit("/", 1)[0] + "/"
        for name in names:
            if name.startswith(f"{IMPORT_NAME}/"):
                relative = name[len(IMPORT_NAME) + 1 :]
                if relative not in package_files:
                    fail(f"{path.name} contains non-allowlisted package file {name!r}")
                continue
            if name.startswith(dist_info_prefix):
                relative = name[len(dist_info_prefix) :]
                if relative in {"LICENSE", "licenses/LICENSE"}:
                    continue
                if relative not in ALLOWED_WHEEL_DIST_INFO_FILES:
                    fail(f"{path.name} contains non-allowlisted metadata file {name!r}")
                continue
            fail(f"{path.name} contains non-allowlisted archive member {name!r}")


def validate_sdist(path: Path, expected_version: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        roots = {name.split("/", 1)[0] for name in names if name}
        if len(roots) != 1:
            fail(f"{path.name} must contain exactly one top-level directory")
        root = next(iter(roots))
        root_prefix = f"{root}/"
        for member in members:
            name = member.name
            if name == root or member.isdir():
                continue
            if not name.startswith(root_prefix):
                fail(f"{path.name} contains member outside its top-level directory: {name!r}")
            relative = name[len(root_prefix) :]
            if relative not in ALLOWED_SDIST_FILES and not relative.startswith(ALLOWED_SDIST_PREFIXES):
                fail(f"{path.name} contains non-allowlisted archive member {relative!r}")
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

    validate_wheel(wheels[0], expected_version, expected_package_files())
    validate_sdist(sdists[0], expected_version)
    print(f"Validated {PACKAGE_NAME} {expected_version}: wheel, sdist, metadata, package data, and test boundary.")


if __name__ == "__main__":
    main()
