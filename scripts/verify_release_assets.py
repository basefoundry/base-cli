#!/usr/bin/env python3
"""Verify that an existing GitHub Release is an exact replay of reviewed assets."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .release_metadata_helpers import sha256_file
except ImportError:  # pragma: no cover - direct script execution
    from release_metadata_helpers import sha256_file

SBOM_NAME = "SBOM.spdx.json"
CHECKSUMS_NAME = "SHA256SUMS"
BOM_ROW_NAME = "RELEASE-BOM-ROW.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _files(directory: Path, label: str, errors: list[str]) -> dict[str, Path]:
    if not directory.is_dir():
        errors.append(f"{label} directory does not exist: {directory}")
        return {}
    result: dict[str, Path] = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            errors.append(f"{label} contains a non-regular asset: {path.name}")
            continue
        result[path.name] = path
    return result


def _read_json(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is not valid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object")
        return {}
    return value


def validate_release_assets(
    expected_dir: Path,
    existing_dir: Path,
    release_json: Path,
    version_file: Path,
    *,
    tag: str,
    source_commit: str,
    resolved_tag_commit: str,
) -> list[str]:
    """Return violations between reviewed files and an existing immutable release."""

    errors: list[str] = []
    expected = _files(expected_dir, "reviewed artifact", errors)
    existing = _files(existing_dir, "existing release", errors)

    required_names = {SBOM_NAME, CHECKSUMS_NAME, BOM_ROW_NAME}
    wheels = {name for name in expected if name.endswith(".whl")}
    sdists = {name for name in expected if name.endswith(".tar.gz")}
    if len(wheels) != 1 or len(sdists) != 1 or not required_names.issubset(expected):
        errors.append("reviewed artifacts must contain one wheel, one sdist, SHA256SUMS, SBOM, and release BOM row")

    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"could not read package VERSION: {exc}")
        version = ""
    if tag != f"v{version}" or not version:
        errors.append(f"release tag {tag!r} does not match package version {version!r}")
    if not SHA_RE.fullmatch(source_commit):
        errors.append("source commit must be a lowercase full 40-character SHA")
    if resolved_tag_commit != source_commit:
        errors.append(
            f"release tag resolves to {resolved_tag_commit!r}, not the reviewed source commit {source_commit!r}"
        )

    release = _read_json(release_json, "existing release metadata", errors)
    if release.get("tag_name") != tag:
        errors.append(f"existing release tag {release.get('tag_name')!r} does not match {tag!r}")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        errors.append("an existing release must be published and non-prerelease")

    if SBOM_NAME in expected:
        sbom = _read_json(expected[SBOM_NAME], SBOM_NAME, errors)
        namespace = str(sbom.get("documentNamespace", ""))
        if sbom.get("name") != f"base-cli-{version}":
            errors.append(f"{SBOM_NAME} does not identify base-cli version {version}")
        if f"/sbom/{version}/{source_commit}" not in namespace:
            errors.append(f"{SBOM_NAME} namespace is not bound to tag {tag} and commit {source_commit}")
        if source_commit not in str(sbom.get("documentComment", "")):
            errors.append(f"{SBOM_NAME} comment is not bound to source commit {source_commit}")

    if BOM_ROW_NAME in expected:
        bom_row = _read_json(expected[BOM_ROW_NAME], BOM_ROW_NAME, errors)
        if bom_row.get("repository") != "basefoundry/base-cli":
            errors.append(f"{BOM_ROW_NAME} identifies the wrong repository")
        if bom_row.get("version") != version or bom_row.get("tag") != tag:
            errors.append(f"{BOM_ROW_NAME} version/tag does not match {tag}")
        if bom_row.get("commit") != source_commit:
            errors.append(f"{BOM_ROW_NAME} is not bound to source commit {source_commit}")

    if CHECKSUMS_NAME in expected:
        checksum_rows: dict[str, str] = {}
        try:
            lines = expected[CHECKSUMS_NAME].read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{CHECKSUMS_NAME} could not be read: {exc}")
            lines = []
        for line in lines:
            fields = line.split(maxsplit=1)
            if len(fields) != 2 or not HEX_SHA256_RE.fullmatch(fields[0]):
                errors.append(f"invalid checksum row in {CHECKSUMS_NAME}: {line!r}")
                continue
            checksum_rows[fields[1]] = fields[0]
        binary_names = wheels | sdists
        if set(checksum_rows) != binary_names:
            errors.append(f"{CHECKSUMS_NAME} must cover exactly the reviewed wheel and sdist")
        for name in binary_names & set(expected):
            actual = sha256_file(expected[name])
            if checksum_rows.get(name) != actual:
                errors.append(f"{CHECKSUMS_NAME} digest for {name} does not match the reviewed artifact")

    expected_names = set(expected)
    existing_names = set(existing)
    if expected_names != existing_names:
        errors.append(
            "release asset filenames differ: "
            f"expected {sorted(expected_names)!r}, observed {sorted(existing_names)!r}; "
            "refusing to replace immutable release assets"
        )
    for name in sorted(expected_names & existing_names):
        expected_digest = sha256_file(expected[name])
        existing_digest = sha256_file(existing[name])
        if expected_digest != existing_digest:
            errors.append(
                f"release asset {name} differs: expected SHA-256 {expected_digest}, "
                f"observed SHA-256 {existing_digest}; refusing to replace immutable release assets"
            )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-dir", type=Path, required=True)
    parser.add_argument("--existing-dir", type=Path, required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--resolved-tag-commit", required=True)
    args = parser.parse_args()
    errors = validate_release_assets(
        args.expected_dir,
        args.existing_dir,
        args.release_json,
        args.version_file,
        tag=args.tag,
        source_commit=args.source_commit,
        resolved_tag_commit=args.resolved_tag_commit,
    )
    if errors:
        for error in errors:
            print(f"release asset verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Verified existing release {args.tag}: all assets and source identities are unchanged.")


if __name__ == "__main__":
    main()
