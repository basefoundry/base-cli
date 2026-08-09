#!/usr/bin/env python3
"""Write a dated, reproducible record for a compatibility workflow run."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ("atlas_click", "beacon_typer", "cinder_automation")
PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
TYPER_VERSIONS = ("0.25.1", "0.26.0", "0.27.1")


def _revision() -> str:
    value = os.environ.get("GITHUB_SHA")
    if value:
        return value
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()


def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record: dict[str, Any] = {
        "schema_version": 1,
        "status": "passed",
        "recorded_at": now,
        "source_revision": _revision(),
        "framework_version": _version(),
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "ref": os.environ.get("GITHUB_REF_NAME", "local"),
        "fixtures": list(FIXTURES),
        "matrix": {"python": list(PYTHON_VERSIONS), "typer": list(TYPER_VERSIONS)},
        "reproduce": [
            "python scripts/validate_consumers.py",
            "python -m build --wheel",
            "python -m pytest compatibility/consumers/*/tests",
        ],
        "claim_policy": "Reference fixtures demonstrate compatibility only; they are not external adoption claims.",
    }
    print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
