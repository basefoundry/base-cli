from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_package_artifact import REQUIRED_DEPENDENCIES


def test_artifact_validator_matches_declared_click_window() -> None:
    assert "click<8.6,>=8.1" in REQUIRED_DEPENDENCIES
