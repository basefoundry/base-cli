#!/usr/bin/env python3
"""Validate the reviewed public API tier manifest against the package facade."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "api-manifest.json"
PACKAGE_ROOT = ROOT / "lib" / "python"


def _load_manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("API manifest must contain a JSON object")
    return value


def main() -> None:
    sys.path.insert(0, str(PACKAGE_ROOT))
    import base_cli
    import base_cli.core as core

    manifest = _load_manifest()
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

    print(f"Validated API manifest: {len(exported)} facade symbols, {len(core.__all__)} core symbols.")


if __name__ == "__main__":
    main()
