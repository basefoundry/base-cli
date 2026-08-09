#!/usr/bin/env python3
"""Create deterministic release checksums and an SPDX 2.3 dependency SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib  # type: ignore[import-untyped]

PACKAGE_NAME = "base-cli"
SBOM_NAME = "SBOM.spdx.json"
CHECKSUMS_NAME = "SHA256SUMS"


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _revision(root: Path) -> str:
    value = os.environ.get("SOURCE_REVISION") or os.environ.get("GITHUB_SHA")
    if value:
        return value
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _created_at() -> str:
    try:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    except ValueError:
        epoch = 0
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spdx_id(value: str) -> str:
    return "SPDXRef-" + "".join(character if character.isalnum() else "-" for character in value)


def _dependency_packages(project: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    packages: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = []
    source_id = _spdx_id(PACKAGE_NAME)
    dependencies: list[tuple[str, str]] = []
    dependencies.extend(("runtime", value) for value in project.get("project", {}).get("dependencies", []))
    for extra, values in project.get("project", {}).get("optional-dependencies", {}).items():
        dependencies.extend((extra, value) for value in values)
    for extra, requirement in dependencies:
        name = requirement.split(";", 1)[0].split("[", 1)[0].strip()
        for delimiter in ("<", ">", "=", "!", "~", " "):
            name = name.split(delimiter, 1)[0].strip()
        dependency_id = _spdx_id(f"{extra}-{name}")
        packages.append(
            {
                "SPDXID": dependency_id,
                "name": name,
                "versionInfo": requirement,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": source_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": dependency_id,
            }
        )
    return packages, relationships


def generate(dist: Path, root: Path) -> None:
    artifacts = sorted((*dist.glob("*.whl"), *dist.glob("*.tar.gz")))
    if len(artifacts) != 2:
        raise SystemExit(f"expected one wheel and one sdist in {dist}, found {len(artifacts)}")
    version = (root / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    revision = _revision(root)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    source_id = _spdx_id(PACKAGE_NAME)
    dependency_packages, relationships = _dependency_packages(project)
    (dist / CHECKSUMS_NAME).write_text(
        "\n".join(f"{_sha256(path)}  {path.name}" for path in artifacts) + "\n", encoding="utf-8"
    )
    sbom: dict[str, Any] = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{PACKAGE_NAME}-{version}",
        "documentNamespace": f"https://basefoundry.github.io/base-cli/sbom/{version}/{revision}",
        "creationInfo": {
            "created": _created_at(),
            "creators": ["Tool: base-cli release metadata generator"],
            "comment": f"Source revision: {revision}",
        },
        "documentComment": f"Source revision: {revision}; artifacts are listed in SHA256SUMS.",
        "packages": [
            {
                "SPDXID": source_id,
                "name": PACKAGE_NAME,
                "versionInfo": version,
                "downloadLocation": "https://pypi.org/project/base-cli/",
                "filesAnalyzed": False,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "NOASSERTION",
            },
            *dependency_packages,
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": source_id,
            },
            *relationships,
        ],
    }
    (dist / SBOM_NAME).write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Generated {SBOM_NAME} and {CHECKSUMS_NAME} for {PACKAGE_NAME} {version} at {revision}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="directory containing the wheel and sdist")
    args = parser.parse_args()
    if not args.dist.is_dir():
        raise SystemExit(f"distribution directory does not exist: {args.dist}")
    generate(args.dist, _root())


if __name__ == "__main__":
    main()
