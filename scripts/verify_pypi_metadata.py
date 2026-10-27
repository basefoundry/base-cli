#!/usr/bin/env python3
"""Verify that PyPI exposes the README from the reviewed release source."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _normalized(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def validate_metadata(metadata: dict[str, Any], version: str, readme: str) -> list[str]:
    """Return violations between PyPI metadata and the reviewed README."""

    errors: list[str] = []
    info = metadata.get("info")
    if not isinstance(info, dict):
        return ["PyPI response has no info object"]
    if info.get("version") != version:
        errors.append(f"PyPI reports version {info.get('version')!r}, expected {version!r}")
    description = info.get("description")
    if not isinstance(description, str):
        errors.append("PyPI response has no Markdown description")
    elif _normalized(description) != _normalized(readme):
        errors.append("PyPI description does not match the reviewed README.md")
    if isinstance(description, str):
        if f"| `{version}` |" not in description:
            errors.append(f"PyPI description does not contain the {version} release row")
        lowered = description.casefold()
        for forbidden in ("source release candidate", "publication is pending"):
            if forbidden in lowered:
                errors.append(f"PyPI description still contains pre-publication wording: {forbidden!r}")
    return errors


def validate_artifact_hashes(metadata: dict[str, Any], artifacts_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Compare PyPI file hashes with the reviewed distribution and SHA256SUMS."""

    errors: list[str] = []
    artifacts = sorted((*artifacts_dir.glob("*.whl"), *artifacts_dir.glob("*.tar.gz")))
    observed = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}
    urls = metadata.get("urls")
    if not isinstance(urls, list):
        return ["PyPI response has no file URL list"], {}
    published: dict[str, str] = {}
    for entry in urls:
        if not isinstance(entry, dict):
            errors.append("PyPI file URL entry is not an object")
            continue
        filename = entry.get("filename")
        digest = (entry.get("digests") or {}).get("sha256") if isinstance(entry.get("digests"), dict) else None
        if not isinstance(filename, str) or not isinstance(digest, str):
            errors.append("PyPI file URL entry has no filename and SHA-256 digest")
            continue
        published[filename] = digest
    if set(published) != set(observed):
        errors.append(
            f"PyPI files differ from reviewed artifacts: expected {sorted(observed)!r}, observed {sorted(published)!r}"
        )
    for filename, digest in observed.items():
        if published.get(filename) != digest:
            errors.append(f"PyPI SHA-256 for {filename} does not match the reviewed artifact")

    checksums_path = artifacts_dir / "SHA256SUMS"
    checksums: dict[str, str] = {}
    if not checksums_path.is_file():
        errors.append("reviewed SHA256SUMS is missing")
    else:
        for line in checksums_path.read_text(encoding="utf-8").splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2:
                checksums[fields[1]] = fields[0]
        if checksums != observed:
            errors.append("reviewed SHA256SUMS does not match the reviewed artifacts")

    evidence = {
        "package": "base-cli",
        "version": metadata.get("info", {}).get("version") if isinstance(metadata.get("info"), dict) else None,
        "files": [
            {"filename": filename, "sha256": observed[filename], "pypi_sha256": published.get(filename)}
            for filename in sorted(observed)
        ],
        "sha256sums_verified": checksums == observed,
        "result": not errors,
    }
    return errors, evidence


def _fetch(url: str, attempts: int, delay: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                value = json.load(response)
            if not isinstance(value, dict):
                raise ValueError("response is not a JSON object")
            return value
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(delay)
    raise SystemExit(f"could not read {url} after {attempts} attempts: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    args = parser.parse_args()
    metadata = _fetch(f"https://pypi.org/pypi/base-cli/{args.version}/json", args.attempts, args.delay)
    errors = validate_metadata(metadata, args.version, args.readme.read_text(encoding="utf-8"))
    evidence: dict[str, Any] | None = None
    if args.artifacts_dir is not None:
        artifact_errors, evidence = validate_artifact_hashes(metadata, args.artifacts_dir)
        errors.extend(artifact_errors)
    if args.evidence_output is not None and evidence is not None:
        args.evidence_output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"PyPI metadata validation failed: {error}")
        raise SystemExit(1)
    print(f"Verified PyPI metadata for base-cli {args.version}.")


if __name__ == "__main__":
    main()
