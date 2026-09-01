from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_coverage import MODULE_FLOORS, WINDOWS_MODULE_FLOORS, validate


def test_validate_accepts_windows_style_coverage_paths(tmp_path) -> None:
    report = {
        "files": {
            path.replace("/", "\\"): {"summary": {"percent_covered": floor}} for path, floor in MODULE_FLOORS.items()
        }
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    validate(report_path)


def test_windows_floors_retain_strict_defaults_except_platform_paths() -> None:
    assert WINDOWS_MODULE_FLOORS["lib/python/base_cli/_private_files.py"] == 50.0
    assert WINDOWS_MODULE_FLOORS["lib/python/base_cli/_runtime.py"] == 55.0
    assert WINDOWS_MODULE_FLOORS["lib/python/base_cli/_attach.py"] == MODULE_FLOORS["lib/python/base_cli/_attach.py"]
