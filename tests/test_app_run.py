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
    def test_malformed_click_exit_code_before_context_is_an_unexpected_error(self) -> None:
        import click

        class MalformedExit(click.ClickException):
            exit_code = object()

        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=lambda: (_ for _ in ()).throw(
                MalformedExit("private pre-context detail")
            ),
        )
        app = base_cli.App(name="malformed-pre-context", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = base_cli.run_app(app, [])

        output = stderr.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", output)
        self.assertIn("Diagnostic context was unavailable", output)
        self.assertNotIn("private pre-context detail", output)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_profile_programming_errors_use_the_unexpected_error_boundary(self) -> None:
        callbacks = (
            ("discover_project", RuntimeError),
            ("load_user_config", ValueError),
            ("resolve_workspace_root", RuntimeError),
            ("load_config", ValueError),
            ("resolve_runtime", RuntimeError),
            ("display_command", ValueError),
        )
        for field_name, error_type in callbacks:
            with self.subTest(field=field_name), tempfile.TemporaryDirectory() as tmpdir:
                detail = f"private {field_name} detail"

                def fail_callback(*_args: object) -> object:
                    raise error_type(detail)

                profile = replace(base_cli.CliProfile.generic(), **{field_name: fail_callback})
                app = base_cli.App(name=f"profile-error-{field_name}", profile=profile)

                @app.command()
                def main(ctx: base_cli.Context) -> None:
                    del ctx

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

                output = stderr.getvalue()
                self.assertEqual(status, 1)
                self.assertIn("Error: Unexpected internal error.", output)
                if field_name == "display_command":
                    self.assertIn("Diagnostic context was unavailable", output)
                else:
                    self.assertIn("Re-run with --debug for a traceback.", output)
                self.assertNotIn(detail, output)
                self.assertNotIn("Traceback", output)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_reports_config_errors_without_traceback(self) -> None:
        profile = base_cli.CliProfile.generic(
            load_config=lambda _project, _explicit: (_ for _ in ()).throw(
                base_cli.ConfigurationError("workspace must be a mapping when provided.")
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

        self.assertEqual(status, 2)
        self.assertEqual(seen, {})
        self.assertIn("workspace must be a mapping", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_generic_invalid_yaml_is_a_safe_usage_error(self) -> None:
        app = base_cli.App(name="invalid-yaml", log_to_file=False)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            self.fail("command should not run")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config = home / "invalid.yml"
            config.write_text("broken: [", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {"HOME": str(home), "BASE_CLI_CACHE_DIR": str(home / ".cache")},
            ), redirect_stderr(stderr):
                status = base_cli.run_app(app, ["--config", str(config)])

        self.assertEqual(status, 2)
        self.assertIn("contains invalid YAML", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_run_app_can_reraise_unexpected_command_exceptions(self) -> None:
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
                    base_cli.run_app(app, [], reraise_unexpected=True)

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
    def test_run_app_accepts_click_native_equals_form_long_option_values(self) -> None:
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

        self.assertEqual(status, 0)
        self.assertEqual(seen, {"name": "demo"})
        self.assertEqual(stderr.getvalue(), "")

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
        self.assertIn("No such option", stderr.getvalue())
        self.assertIn("--bad-option", stderr.getvalue())
        self.assertNotIn("internal-cli", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
