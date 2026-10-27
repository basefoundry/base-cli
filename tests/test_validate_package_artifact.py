from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from scripts.validate_package_artifact import (
    REQUIRED_DEPENDENCIES,
    normalized_text,
    validate_sdist,
    validate_wheel,
)


def test_artifact_validator_matches_declared_click_window() -> None:
    assert "click<8.6,>=8.1" in REQUIRED_DEPENDENCIES


def test_artifact_validator_normalizes_only_line_endings_and_outer_whitespace() -> None:
    assert normalized_text("\r\nREADME\r\n") == "README"


def test_wheel_validator_rejects_duplicate_members_before_metadata_parsing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = tmp_path / "base_cli-0.5.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("base_cli/__init__.py", b"first")
        archive.writestr("base_cli/__init__.py", b"second")

    with pytest.raises(SystemExit):
        validate_wheel(wheel, "0.5.1", {"__init__.py"}, {"__init__.py": ""}, "")
    assert "duplicate archive members" in capsys.readouterr().err


def test_wheel_validator_rejects_startup_hooks_before_metadata_parsing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = tmp_path / "base_cli-0.5.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("base_cli/sitecustomize.py", b"import os")

    with pytest.raises(SystemExit):
        validate_wheel(wheel, "0.5.1", {"sitecustomize.py"}, {"sitecustomize.py": ""}, "")
    assert "startup hook" in capsys.readouterr().err


def test_sdist_validator_rejects_traversal_and_startup_hooks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sdist = tmp_path / "base_cli-0.5.1.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name in ("base-cli-0.5.1/../escape.txt", "base-cli-0.5.1/usercustomize.py"):
            payload = b"unsafe"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, __import__("io").BytesIO(payload))

    with pytest.raises(SystemExit):
        validate_sdist(sdist, "0.5.1", {}, "")
    assert "unsafe archive member path" in capsys.readouterr().err
