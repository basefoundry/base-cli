#!/usr/bin/env python3
"""Enforce coverage floors for security- and contract-sensitive modules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

# These modules sit on filesystem, lifecycle, and machine-contract boundaries.
# Their floors are intentionally stricter than the aggregate project gate.
MODULE_FLOORS: dict[str, float] = {
    "lib/python/base_cli/_attach.py": 75.0,
    "lib/python/base_cli/_click_compat.py": 80.0,
    "lib/python/base_cli/_private_files.py": 75.0,
    "lib/python/base_cli/_runtime.py": 80.0,
    "lib/python/base_cli/command_protocol.py": 85.0,
    "lib/python/base_cli/history.py": 75.0,
    "lib/python/base_cli/redaction.py": 90.0,
}

# Windows compatibility runs exercise different filesystem and lifecycle
# branches than the canonical POSIX suite. Keep a meaningful floor there,
# while enforcing the strict contract floors in the Ubuntu quality job.
WINDOWS_MODULE_FLOORS: dict[str, float] = {
    **MODULE_FLOORS,
    "lib/python/base_cli/_private_files.py": 50.0,
    "lib/python/base_cli/_runtime.py": 55.0,
}


def fail(message: str) -> NoReturn:
    print(f"coverage policy failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _normalise_path(value: str) -> str:
    """Compare coverage paths consistently across Windows and POSIX hosts."""

    return value.replace("\\", "/")


def _file_summary(files: dict[str, Any], expected_path: str) -> dict[str, Any]:
    expected_path = _normalise_path(expected_path)
    for path, payload in files.items():
        normalised_path = _normalise_path(path)
        if normalised_path == expected_path or normalised_path.endswith(expected_path):
            summary = payload.get("summary")
            if isinstance(summary, dict):
                return summary
    fail(f"coverage report is missing {expected_path}")


def _module_floors() -> dict[str, float]:
    return WINDOWS_MODULE_FLOORS if sys.platform == "win32" else MODULE_FLOORS


def validate(report_path: Path) -> None:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {report_path}: {exc}")
    files = report.get("files") if isinstance(report, dict) else None
    if not isinstance(files, dict):
        fail(f"{report_path} does not contain a coverage 'files' mapping")

    floors = _module_floors()
    failures: list[str] = []
    for path, floor in floors.items():
        summary = _file_summary(files, path)
        covered = summary.get("percent_covered")
        if not isinstance(covered, (int, float)):
            failures.append(f"{path}: missing percent_covered")
        elif covered < floor:
            failures.append(f"{path}: {covered:.2f}% < {floor:.2f}%")
    if failures:
        fail("; ".join(failures))

    for path, floor in floors.items():
        summary = _file_summary(files, path)
        print(f"{path}: {summary['percent_covered']:.2f}% (floor {floor:.2f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage.py JSON report")
    args = parser.parse_args()
    validate(args.report)


if __name__ == "__main__":
    main()
