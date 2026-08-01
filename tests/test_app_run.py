from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest import mock

import base_cli


def generic_app(**kwargs: object) -> base_cli.App:
    return base_cli.App(profile=base_cli.CliProfile.generic(), **kwargs)


class RunAppTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_reports_config_errors_without_traceback(self) -> None:
        profile = base_cli.CliProfile.generic(
            load_config=lambda _project, _explicit: (_ for _ in ()).throw(
                ValueError("workspace must be a mapping when provided.")
            )
        )
        app = base_cli.App(profile=profile, name="bad-config", log_to_file=False)
        seen = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["called"] = True
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "BASE_CLI_CACHE_DIR": str(home / ".cache"),
                },
            ), redirect_stderr(stderr):
                status = base_cli.run_app(app, [])

        self.assertEqual(status, 1)
        self.assertEqual(seen, {})
        self.assertIn("workspace must be a mapping", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_preserves_unexpected_command_exceptions(self) -> None:
        app = base_cli.App(name="boom", log_to_file=False)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "BASE_CLI_CACHE_DIR": str(home / ".cache"),
                },
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    base_cli.run_app(app, [])

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_reports_invalid_command_return_values(self) -> None:
        app = base_cli.App(name="invalid-return", log_to_file=False)

        @app.command()
        def main(ctx: base_cli.Context) -> dict[str, str]:
            del ctx
            return {"status": "bad"}

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "BASE_CLI_CACHE_DIR": str(home / ".cache"),
                },
            ), redirect_stderr(stderr):
                status = base_cli.run_app(app, [])

        self.assertEqual(status, 1)
        self.assertIn("Commands must return None or an int exit code", stderr.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_rejects_equals_form_long_option_values(self) -> None:
        app = base_cli.App(name="space-options", log_to_file=False)
        seen = {}

        @app.command()
        @base_cli.option("--name", required=True)
        def main(ctx: base_cli.Context, name: str) -> None:
            del ctx
            seen["name"] = name

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "BASE_CLI_CACHE_DIR": str(home / ".cache"),
                },
            ), redirect_stderr(stderr):
                status = base_cli.run_app(app, ["--name=demo"])

        self.assertEqual(status, 2)
        self.assertEqual(seen, {})
        self.assertIn(
            "Option '--name' uses unsupported equals syntax. Use '--name demo' instead.",
            stderr.getvalue(),
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_uses_delegated_display_command_for_usage_errors(self) -> None:
        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=lambda: "tool demo",
        )
        app = base_cli.App(profile=profile, name="internal-cli", log_to_file=False)

        @app.command(context_settings={"help_option_names": ["-h", "--help"]})
        def main(ctx: base_cli.Context) -> None:
            del ctx

        stderr = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"BASE_CLI_DISPLAY_COMMAND": "tool demo"},
        ), redirect_stderr(stderr):
            status = base_cli.run_app(app, ["--bad-option"])

        self.assertEqual(status, 2)
        self.assertIn("Usage: tool demo", stderr.getvalue())
        self.assertIn("No such option '--bad-option'.", stderr.getvalue())
        self.assertNotIn("internal-cli", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
