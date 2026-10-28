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


def _write_valid_wheel(path: Path, *, include_directory: bool = False, include_symlink: bool = False) -> None:
    package_init = b"from __future__ import annotations\n"
    metadata = """Metadata-Version: 2.1
Name: base-cli
Version: 0.5.1
License: Apache-2.0
Requires-Python: >=3.10
Requires-Dist: click<8.6,>=8.1
Provides-Extra: yaml
Requires-Dist: PyYAML<7,>=6.0; extra == \"yaml\"
Project-URL: Documentation, https://basefoundry.github.io/base-cli/

README
""".encode()
    with zipfile.ZipFile(path, "w") as archive:
        if include_directory:
            directory = zipfile.ZipInfo("base_cli/")
            directory.create_system = 3
            directory.external_attr = 0o40755 << 16
            archive.writestr(directory, b"")
        archive.writestr("base_cli/__init__.py", package_init)
        archive.writestr("base_cli/py.typed", b"")
        archive.writestr("base_cli-0.5.1.dist-info/METADATA", metadata)
        archive.writestr("base_cli-0.5.1.dist-info/LICENSE", b"license")
        archive.writestr("base_cli-0.5.1.dist-info/WHEEL", b"Wheel-Version: 1.0\n")
        archive.writestr("base_cli-0.5.1.dist-info/RECORD", b"")
        if include_symlink:
            symlink = zipfile.ZipInfo("base_cli/linked.py")
            symlink.create_system = 3
            symlink.external_attr = 0o120777 << 16
            archive.writestr(symlink, b"base_cli/__init__.py")


def _validate_test_wheel(path: Path) -> None:
    validate_wheel(
        path,
        "0.5.1",
        {"__init__.py", "py.typed"},
        {
            "__init__.py": "5384bfdb2df380b6557cc7a71d16891415bccaa87699406e236f752c6415389f",
            "py.typed": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        },
        "README",
    )


def test_artifact_validator_matches_declared_click_window() -> None:
    assert "click<8.6,>=8.1" in REQUIRED_DEPENDENCIES


def test_artifact_validator_normalizes_only_line_endings_and_outer_whitespace() -> None:
    assert normalized_text("\r\nREADME\r\n") == "README"


def test_wheel_validator_allows_executable_bits_on_directory_entries(tmp_path: Path) -> None:
    wheel = tmp_path / "base_cli-0.5.1-py3-none-any.whl"
    _write_valid_wheel(wheel, include_directory=True)

    _validate_test_wheel(wheel)


def test_wheel_validator_rejects_symbolic_links(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = tmp_path / "base_cli-0.5.1-py3-none-any.whl"
    _write_valid_wheel(wheel, include_symlink=True)

    with pytest.raises(SystemExit):
        _validate_test_wheel(wheel)
    assert "symbolic link" in capsys.readouterr().err


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
