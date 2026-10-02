from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import base_cli
from base_cli import _runtime as runtime
from base_cli.testing import invoke


def test_missing_directory_primitives_warns_once_and_command_succeeds(tmp_path: Path) -> None:
    app = base_cli.App(name="unsupported-retention", max_run_bundles=1)

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        pass

    # Keep native Windows on its real audited fallback; model an unsupported
    # POSIX-like platform without directory flags or descriptor-relative calls.
    if os.name == "nt":
        pytest.skip("unsupported platform model is POSIX-only")
    with (
        patch.object(os, "supports_dir_fd", set()),
        patch.object(runtime, "_supports_fd_relative_bundle_removal", return_value=False),
    ):
        result = invoke(app, [], home=tmp_path)
    assert result.exit_code == 0
    assert result.stderr.count("retention unavailable on this platform") == 1


def test_missing_flags_raise_oserror_not_attributeerror() -> None:
    with patch.dict(os.__dict__):
        os.__dict__.pop("O_DIRECTORY", None)
        os.__dict__.pop("O_NOFOLLOW", None)
        with pytest.raises(OSError, match="unavailable"):
            runtime._directory_open_flags()


def test_native_count_bound_is_enforced(tmp_path: Path) -> None:
    app = base_cli.App(name="native-retention", max_run_bundles=2)
    roots = []

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        roots.append(ctx.run_root)

    for _ in range(6):
        result = invoke(app, [], home=tmp_path)
        assert result.exit_code == 0, result.output
    assert sum(root.exists() for root in roots) <= 2


def test_unsupported_pass_warns_only_once(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows has a native fallback")
    logger = Mock()
    with patch.object(runtime, "_supports_fd_relative_bundle_removal", return_value=False):
        runtime.prune_run_bundles(tmp_path, max_bundles=1, logger=logger)
    assert logger.warning.call_count == 1
