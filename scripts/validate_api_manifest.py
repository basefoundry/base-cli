#!/usr/bin/env python3
"""Validate the reviewed public API tier manifest against the package facade."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from api_manifest import load_manifest
from generate_api_reference import _load_package

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "api-manifest.json"
PACKAGE_ROOT = ROOT / "lib" / "python"


def _validate() -> None:
    base_cli = _load_package()
    import base_cli.core as core

    manifest = load_manifest(MANIFEST)
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported API manifest schema version")
    if manifest.get("facade") != "base_cli":
        raise ValueError("API manifest facade must be base_cli")

    tiers = manifest.get("tiers")
    if not isinstance(tiers, dict):
        raise ValueError("API manifest tiers must be an object")
    expected_tiers = {"core", "integration", "advanced", "provisional"}
    if set(tiers) != expected_tiers:
        raise ValueError(f"API manifest tiers must be exactly {sorted(expected_tiers)}")

    tier_names: list[str] = []
    for tier in sorted(expected_tiers):
        names = tiers[tier]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError(f"API manifest tier {tier!r} must be a list of strings")
        if names != sorted(names):
            raise ValueError(f"API manifest tier {tier!r} must be sorted")
        tier_names.extend(names)

    duplicates = sorted(name for name in set(tier_names) if tier_names.count(name) > 1)
    if duplicates:
        raise ValueError(f"API manifest contains duplicate symbols: {duplicates}")

    exported = set(base_cli.__all__)
    manifest_exports = set(tier_names)
    if manifest_exports != exported:
        missing = sorted(exported - manifest_exports)
        extra = sorted(manifest_exports - exported)
        raise ValueError(f"API manifest mismatch; missing={missing}, extra={extra}")
    if any(name.startswith("_") and name != "__version__" for name in manifest_exports):
        raise ValueError("private names other than __version__ cannot be public API")
    for name in manifest_exports:
        if not hasattr(base_cli, name):
            raise ValueError(f"facade export is not resolvable: {name}")

    core_names = tiers["core"]
    if set(core.__all__) != set(core_names):
        raise ValueError("base_cli.core.__all__ must match the manifest core lifecycle symbols")
    if any(not hasattr(core, name) for name in core.__all__):
        raise ValueError("base_cli.core contains an unresolved export")
    for name in core_names:
        if getattr(core, name) is not getattr(base_cli, name):
            raise ValueError(f"base_cli.core.{name} is not identical to base_cli.{name}")

    print(f"Validated API manifest: {len(exported)} facade symbols, {len(core.__all__)} core symbols.")


def main() -> None:
    try:
        _validate()
    except (OSError, ValueError, TypeError) as exc:
        print(f"API manifest validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
