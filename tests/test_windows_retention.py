from __future__ import annotations

import os
from pathlib import Path

import pytest
from base_cli._windows_retention import _pin_directory, remove_bundle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows retention contract")


def test_pinned_directory_rejects_concurrent_rename(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    bundle = runs_root / "bundle"
    bundle.mkdir()
    replacement = runs_root / "replacement"

    with _pin_directory(bundle):
        with pytest.raises(OSError):
            os.rename(bundle, replacement)

    assert bundle.is_dir()
    assert not replacement.exists()


def test_reparse_point_child_is_removed_as_a_leaf(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    bundle = runs_root / "bundle"
    bundle.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    link = bundle / "linked-directory"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    remove_bundle(runs_root, bundle)

    assert not bundle.exists()
    assert external.is_dir()
