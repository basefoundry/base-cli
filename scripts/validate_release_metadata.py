#!/usr/bin/env python3
"""Validate release checksums, SPDX metadata, and source revision binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SBOM_NAME = "SBOM.spdx.json"
CHECKSUMS_NAME = "SHA256SUMS"
BOM_ROW_NAME = "RELEASE-BOM-ROW.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> None:
    raise SystemExit(f"release metadata validation failed: {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    args = parser.parse_args()
    checksums_path = args.dist / CHECKSUMS_NAME
    sbom_path = args.dist / SBOM_NAME
    if not checksums_path.is_file() or not sbom_path.is_file():
        _fail(f"{SBOM_NAME} and {CHECKSUMS_NAME} are required")
    rows: dict[str, str] = {}
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            _fail(f"invalid checksum row: {line!r}")
        rows[parts[1]] = parts[0]
    artifacts = sorted((*args.dist.glob("*.whl"), *args.dist.glob("*.tar.gz")))
    if set(rows) != {path.name for path in artifacts} or len(artifacts) != 2:
        _fail("SHA256SUMS must cover exactly one wheel and one sdist")
    for path in artifacts:
        if _sha256(path) != rows[path.name]:
            _fail(f"checksum mismatch for {path.name}")
    try:
        sbom: dict[str, Any] = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid SPDX JSON: {exc}")
    if sbom.get("spdxVersion") != "SPDX-2.3":
        _fail("SBOM must use SPDX-2.3")
    if sbom.get("dataLicense") != "CC0-1.0":
        _fail("SBOM data license must be CC0-1.0")
    expected_revision = os.environ.get("SOURCE_REVISION") or os.environ.get("GITHUB_SHA")
    if expected_revision and expected_revision not in str(sbom.get("documentNamespace")):
        _fail("SBOM namespace is not bound to SOURCE_REVISION")
    if expected_revision and expected_revision not in str(sbom.get("documentComment")):
        _fail("SBOM comment is not bound to SOURCE_REVISION")
    packages = sbom.get("packages")
    if not isinstance(packages, list) or not any(package.get("name") == "base-cli" for package in packages):
        _fail("SBOM does not describe base-cli")
    bom_path = args.dist / BOM_ROW_NAME
    try:
        bom_row: dict[str, Any] = json.loads(bom_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"invalid {BOM_ROW_NAME}: {exc}")
    version = str(sbom.get("name", "")).removeprefix("base-cli-")
    if bom_row.get("repository") != "basefoundry/base-cli":
        _fail(f"{BOM_ROW_NAME} repository must be basefoundry/base-cli")
    if bom_row.get("version") != version or bom_row.get("tag") != f"v{version}":
        _fail(f"{BOM_ROW_NAME} version/tag does not match the release")
    commit = bom_row.get("commit")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        _fail(f"{BOM_ROW_NAME} commit must be a lowercase full 40-character SHA")
    if expected_revision and commit != expected_revision:
        _fail(f"{BOM_ROW_NAME} commit is not bound to SOURCE_REVISION")
    if bom_row.get("source_mode") != "release" or bom_row.get("required") is not True:
        _fail(f"{BOM_ROW_NAME} must declare a required release source")
    if bom_row.get("result") != "passed" or not bom_row.get("evidence"):
        _fail(f"{BOM_ROW_NAME} must declare a passing result with evidence")
    print(f"Validated {len(artifacts)} artifact hashes and SPDX SBOM {sbom_path}.")


if __name__ == "__main__":
    main()
