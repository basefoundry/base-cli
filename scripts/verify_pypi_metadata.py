#!/usr/bin/env python3
"""Verify that PyPI exposes the README from the reviewed release source."""

from __future__ import annotations

import argparse
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
    args = parser.parse_args()
    metadata = _fetch(f"https://pypi.org/pypi/base-cli/{args.version}/json", args.attempts, args.delay)
    errors = validate_metadata(metadata, args.version, args.readme.read_text(encoding="utf-8"))
    if errors:
        for error in errors:
            print(f"PyPI metadata validation failed: {error}")
        raise SystemExit(1)
    print(f"Verified PyPI metadata for base-cli {args.version}.")


if __name__ == "__main__":
    main()
