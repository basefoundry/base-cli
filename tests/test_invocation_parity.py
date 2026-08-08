from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

import base_cli
import base_cli.app as app_module
from base_cli.testing import invoke


@dataclass(frozen=True)
class _Observation:
    exit_code: int
    stdout: str
    stderr: str
    logged_argv: list[str]
    log_text: str
    metadata: dict[str, object]
    home: Path


def _profile() -> base_cli.CliProfile:
    return replace(
        base_cli.CliProfile.generic(),
        display_command=lambda: "parity-tool",
    )


def _make_app(case: str) -> base_cli.App:
    app = base_cli.App(name="parity-tool", profile=_profile())

    if case == "success":

        @app.command()
        @base_cli.option("--name", required=True)
        def success(ctx: base_cli.Context, name: str) -> None:
            del ctx
            print(f"hello {name}")

        return app

    if case == "usage":
        import click

        @app.command()
        @base_cli.option("--target", required=True)
        def usage(ctx: base_cli.Context, target: str) -> None:
            del ctx, target
            raise click.UsageError("choose a valid target")

        return app

    if case == "unexpected":

        @app.command()
        def unexpected(ctx: base_cli.Context) -> None:
            del ctx
            raise RuntimeError("private invocation detail")

        return app

    if case == "group":

        @app.subcommand("show")
        @base_cli.option("--name", required=True)
        def show(ctx: base_cli.Context, name: str) -> None:
            print(f"{ctx.environment}:{name}")

        return app

    if case == "sensitive":

        @app.command()
        @base_cli.option("--token", sensitive=True, required=True)
        def sensitive(ctx: base_cli.Context, token: str) -> None:
            del ctx
            print(f"token-length={len(token)}")

        return app

    raise AssertionError(f"unknown parity case: {case}")


def _isolated_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "BASE_CLI_CACHE_DIR": str(home / ".cache"),
    }


def _load_run_artifacts(home: Path) -> tuple[dict[str, object], list[str], str]:
    metadata_paths = sorted((home / ".cache").glob("**/run.json"))
    if len(metadata_paths) != 1:
        raise AssertionError(f"expected one run.json below {home}, found {metadata_paths}")

    metadata_path = metadata_paths[0]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise AssertionError(f"run metadata is not an object: {metadata!r}")
    if metadata.get("status") == "running":
        raise AssertionError(f"run metadata was not finalized: {metadata!r}")
    for key in ("outcome", "exit_code", "ended_at", "duration_ms"):
        if key not in metadata:
            raise AssertionError(f"terminal run metadata is missing {key!r}: {metadata!r}")

    log_path = metadata_path.parent / "logs" / "primary.log"
    log_text = log_path.read_text(encoding="utf-8")
    invocation_lines = [line for line in log_text.splitlines() if "argv=" in line]
    if len(invocation_lines) != 1:
        raise AssertionError(f"expected one logged argv line in {log_path}: {invocation_lines!r}")
    logged_argv = ast.literal_eval(invocation_lines[0].split("argv=", 1)[1])
    if not isinstance(logged_argv, list) or not all(isinstance(value, str) for value in logged_argv):
        raise AssertionError(f"logged argv is not a string list: {logged_argv!r}")
    return metadata, logged_argv, log_text


def _observe_production(app: base_cli.App, args: list[str] | None, home: Path) -> _Observation:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch.dict(os.environ, _isolated_environment(home)), redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = base_cli.run_app(app, args)
    metadata, logged_argv, log_text = _load_run_artifacts(home)
    return _Observation(
        exit_code,
        stdout.getvalue(),
        stderr.getvalue(),
        logged_argv,
        log_text,
        metadata,
        home,
    )


def _observe_testing(app: base_cli.App, args: list[str], home: Path) -> _Observation:
    result = invoke(app, args, home=home)
    metadata, logged_argv, log_text = _load_run_artifacts(home)
    return _Observation(
        result.exit_code,
        result.stdout,
        result.stderr,
        logged_argv,
        log_text,
        metadata,
        home,
    )


def _stable_metadata(metadata: dict[str, object]) -> dict[str, object]:
    dynamic_keys = {"run_id", "started_at", "ended_at", "duration_ms"}
    return {key: value for key, value in metadata.items() if key not in dynamic_keys}


def _stable_stderr(observation: _Observation) -> str:
    value = observation.stderr.replace(str(observation.home), "<HOME>")
    run_id = observation.metadata.get("run_id")
    if isinstance(run_id, str):
        value = value.replace(run_id, "<RUN_ID>")
    return value


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class InvocationParityTests(unittest.TestCase):
    def test_production_compacts_launcher_home_path_in_retained_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = (Path(tmpdir) / "home").resolve()
            home.mkdir()
            launcher = home / ".venv" / "bin" / "parity-tool"
            with mock.patch.object(app_module.sys, "argv", [str(launcher), "--name=Ada"]):
                observation = _observe_production(_make_app("success"), None, home)

        self.assertEqual(observation.logged_argv[0], "~/.venv/bin/parity-tool")
        self.assertNotIn(str(home), observation.log_text)

    def test_production_and_testing_match_parser_usage_errors_without_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            production_home = root / "production"
            testing_home = root / "testing"
            production_home.mkdir()
            testing_home.mkdir()
            production_stdout = io.StringIO()
            production_stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, _isolated_environment(production_home)),
                redirect_stdout(production_stdout),
                redirect_stderr(production_stderr),
            ):
                production_status = base_cli.run_app(_make_app("success"), [])

            testing = invoke(_make_app("success"), [], home=testing_home)

            self.assertEqual(testing.exit_code, production_status)
            self.assertEqual(testing.stdout, production_stdout.getvalue())
            self.assertEqual(testing.stderr, production_stderr.getvalue())
            self.assertEqual(list(production_home.glob("**/run.json")), [])
            self.assertEqual(list(testing_home.glob("**/run.json")), [])

    def test_production_and_testing_match_representative_invocations(self) -> None:
        cases = (
            ("success", ["--name=Ada"]),
            ("usage", ["--target", "invalid"]),
            ("unexpected", []),
            ("group", ["--environment=prod", "show", "--name=grouped"]),
        )

        for case, args in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                production_home = root / "production"
                testing_home = root / "testing"
                production_home.mkdir()
                testing_home.mkdir()

                production = _observe_production(_make_app(case), args, production_home)
                testing = _observe_testing(_make_app(case), args, testing_home)

                self.assertEqual(testing.exit_code, production.exit_code)
                self.assertEqual(testing.stdout, production.stdout)
                self.assertEqual(_stable_stderr(testing), _stable_stderr(production))
                self.assertEqual(testing.logged_argv, production.logged_argv)
                self.assertEqual(_stable_metadata(testing.metadata), _stable_metadata(production.metadata))

    def test_equals_form_sensitive_value_is_accepted_and_redacted_in_both_paths(self) -> None:
        secret = "super-secret-value"
        args = [f"--token={secret}"]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            production_home = root / "production"
            testing_home = root / "testing"
            production_home.mkdir()
            testing_home.mkdir()

            production = _observe_production(_make_app("sensitive"), args, production_home)
            testing = _observe_testing(_make_app("sensitive"), args, testing_home)

        self.assertEqual(production.exit_code, 0)
        self.assertEqual(testing.exit_code, production.exit_code)
        self.assertEqual(testing.stdout, production.stdout)
        self.assertEqual(_stable_stderr(testing), _stable_stderr(production))
        self.assertEqual(
            production.logged_argv,
            ["parity-tool", "--token=[REDACTED]"],
        )
        self.assertEqual(testing.logged_argv, production.logged_argv)
        self.assertNotIn(secret, production.log_text)
        self.assertNotIn(secret, testing.log_text)
        self.assertIn("[REDACTED]", production.log_text)
        self.assertIn("[REDACTED]", testing.log_text)
        self.assertEqual(_stable_metadata(testing.metadata), _stable_metadata(production.metadata))


if __name__ == "__main__":
    unittest.main()
