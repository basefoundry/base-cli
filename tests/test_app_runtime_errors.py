from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import base_cli
from base_cli.testing import invoke


def generic_app(**kwargs: object) -> base_cli.App:
    return base_cli.App(profile=base_cli.CliProfile.generic(), **kwargs)


class AppRuntimeErrorTests(unittest.TestCase):
    def test_missing_click_error_recommends_pip_install(self) -> None:
        app = base_cli.App(name="missing-click")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with mock.patch.dict(sys.modules, {"click": None}):
            with self.assertRaisesRegex(RuntimeError, r"Install it with 'pip install click'"):
                _ = app.click_command

    def test_testing_missing_click_error_recommends_pip_install(self) -> None:
        app = base_cli.App(name="missing-click")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with mock.patch.dict(sys.modules, {"click": None}):
            with self.assertRaisesRegex(RuntimeError, r"Install it with 'pip install click'"):
                invoke(app, [])

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_reports_unusable_cache_root_without_traceback(self) -> None:
        app = generic_app(name="cache-failure", version="0.1.0")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            self.fail("command body should not run when context creation fails")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = root / "cache-root"
            home.mkdir()
            # A regular file is unusable as a cache root on every platform and
            # also behaves consistently when the test suite runs as root in a
            # Linux distribution container (where mode bits are bypassed).
            cache_root.write_text("not a directory", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {"HOME": str(home), "BASE_CLI_CACHE_DIR": str(cache_root)}):
                with redirect_stderr(stderr):
                    try:
                        exit_code = base_cli.run_app(app, [])
                    except PermissionError as exc:
                        self.fail(f"run_app should handle context creation permission errors: {exc}")

        error = stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Error:", error)
        self.assertIn("Unable to create runtime directory", error)
        self.assertIn(str(cache_root / "cache-failure" / "runs"), error)
        self.assertNotIn("Traceback", error)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_reports_framework_log_path_failure_without_traceback(self) -> None:
        app = generic_app(name="log-open-failure")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            self.fail("command body should not run when logging setup fails")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            not_a_directory = root / "not-a-directory"
            not_a_directory.write_text("file", encoding="utf-8")
            log_file = not_a_directory / "primary.log"
            stderr = io.StringIO()
            with (
                mock.patch.dict(
                    os.environ,
                    {"HOME": str(home), "BASE_CLI_CACHE_DIR": str(root / "cache")},
                ),
                redirect_stderr(stderr),
            ):
                exit_code = base_cli.run_app(app, ["--log-file", str(log_file)])

        error = stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn(f"Unable to create runtime directory '{not_a_directory}'", error)
        self.assertNotIn("Unexpected internal error", error)
        self.assertNotIn("Traceback", error)
