from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import base_cli
import base_cli._lifecycle as lifecycle_module
import base_cli.app as app_module
from base_cli._lifecycle import RunRecorder
from base_cli._runtime import runtime_layout


def _run(app: base_cli.App, home: Path, args: list[str] | None = None) -> tuple[int, str]:
    stderr = io.StringIO()
    with (
        mock.patch.dict(
            os.environ,
            {
                "HOME": str(home),
                "BASE_CLI_CACHE_DIR": str(home / "cache"),
            },
        ),
        redirect_stderr(stderr),
    ):
        status = base_cli.run_app(app, args or [])
    return status, stderr.getvalue()


def _metadata_files(home: Path) -> list[Path]:
    return sorted((home / "cache").glob("**/run.json"))


def _load_only_metadata(test: unittest.TestCase, home: Path) -> tuple[Path, dict[str, object]]:
    paths = _metadata_files(home)
    test.assertEqual(len(paths), 1, paths)
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    test.assertIsInstance(payload, dict)
    return paths[0], payload


def _assert_terminal_metadata(
    test: unittest.TestCase,
    payload: dict[str, object],
    *,
    status: str,
    outcome: str,
    exit_code: int,
) -> None:
    test.assertEqual(payload["schema_version"], 1)
    test.assertEqual(payload["status"], status)
    test.assertEqual(payload["outcome"], outcome)
    test.assertEqual(payload["exit_code"], exit_code)
    test.assertIsInstance(payload["run_id"], str)
    test.assertIsInstance(payload["owner"], str)
    test.assertIsInstance(payload["cli"], str)
    started_text = str(payload["started_at"])
    ended_text = str(payload["ended_at"])
    test.assertTrue(started_text.endswith("Z"), started_text)
    test.assertTrue(ended_text.endswith("Z"), ended_text)
    started_at = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
    ended_at = datetime.fromisoformat(ended_text.replace("Z", "+00:00"))
    test.assertEqual(started_at.utcoffset(), timezone.utc.utcoffset(started_at))
    test.assertEqual(ended_at.utcoffset(), timezone.utc.utcoffset(ended_at))
    test.assertGreaterEqual(ended_at, started_at)
    test.assertIs(type(payload["duration_ms"]), int)
    test.assertGreaterEqual(payload["duration_ms"], 0)


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class AppRunMetadataTests(unittest.TestCase):
    def test_normal_returns_finalize_core_owned_metadata(self) -> None:
        cases = (
            ("none", None, 0, "ok", "success"),
            ("zero", 0, 0, "ok", "success"),
            ("usage", 2, 2, "error", "usage_error"),
            ("nonzero", 7, 7, "error", "nonzero_return"),
            ("returned-interrupted", 130, 130, "aborted", "nonzero_return"),
        )
        for name, returned, expected_code, expected_status, expected_outcome in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                app = base_cli.App(name=f"metadata-{name}")

                @app.command()
                def main(
                    ctx: base_cli.Context,
                    returned: int | None = returned,
                ) -> int | None:
                    del ctx
                    return returned

                home = Path(tmpdir)
                status, stderr = _run(app, home)
                _, metadata = _load_only_metadata(self, home)

                self.assertEqual(status, expected_code)
                self.assertEqual(stderr, "")
                _assert_terminal_metadata(
                    self,
                    metadata,
                    status=expected_status,
                    outcome=expected_outcome,
                    exit_code=expected_code,
                )

    def test_command_usage_error_preserves_click_rendering_and_exit_code(self) -> None:
        import click

        app = base_cli.App(name="metadata-usage-error")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise click.UsageError("choose a valid target")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 2)
        self.assertIn("Usage:", stderr)
        self.assertIn("Error: choose a valid target", stderr)
        _assert_terminal_metadata(self, metadata, status="error", outcome="usage_error", exit_code=2)

    def test_click_exception_preserves_custom_exit_code(self) -> None:
        import click

        class Unavailable(click.ClickException):
            exit_code = 78

        app = base_cli.App(name="metadata-click-error")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise Unavailable("service unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 78)
        self.assertIn("Error: service unavailable", stderr)
        _assert_terminal_metadata(self, metadata, status="error", outcome="click_error", exit_code=78)

    def test_zero_code_click_exception_keeps_code_and_status_consistent(self) -> None:
        import click

        class InformationalExit(click.ClickException):
            exit_code = 0

        app = base_cli.App(name="metadata-zero-click-error")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise InformationalExit("informational stop")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertIn("Error: informational stop", stderr)
        _assert_terminal_metadata(self, metadata, status="ok", outcome="click_error", exit_code=0)

    def test_malformed_click_exit_code_is_an_unexpected_error(self) -> None:
        import click

        class MalformedExit(click.ClickException):
            exit_code = "not-an-exit-code"

        app = base_cli.App(name="metadata-malformed-click-error")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise MalformedExit("private malformed exception detail")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", stderr)
        self.assertNotIn("private malformed exception detail", stderr)
        _assert_terminal_metadata(self, metadata, status="error", outcome="unexpected_error", exit_code=1)

    def test_abort_and_keyboard_interrupt_have_distinct_outcomes(self) -> None:
        import click

        cases = (
            ("abort", click.Abort(), 1, "error", "aborted", "Aborted!"),
            ("interrupt", KeyboardInterrupt(), 130, "aborted", "interrupted", "Interrupted."),
        )
        for name, raised, expected_code, expected_status, expected_outcome, expected_message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                app = base_cli.App(name=f"metadata-{name}")

                @app.command()
                def main(
                    ctx: base_cli.Context,
                    raised: BaseException = raised,
                ) -> None:
                    del ctx
                    raise raised

                home = Path(tmpdir)
                status, stderr = _run(app, home)
                metadata_path, metadata = _load_only_metadata(self, home)
                log_text = (metadata_path.parent / "logs" / "primary.log").read_text(encoding="utf-8")

                self.assertEqual(status, expected_code)
                self.assertIn(expected_message, stderr)
                self.assertNotIn("Traceback", stderr)
                if expected_outcome == "interrupted":
                    self.assertIn("Interrupted.", log_text)
                _assert_terminal_metadata(
                    self,
                    metadata,
                    status=expected_status,
                    outcome=expected_outcome,
                    exit_code=expected_code,
                )

    def test_explicit_click_and_system_exits_are_normalized(self) -> None:
        import click

        cases = (
            ("click", click.exceptions.Exit(9), 9, "error", "nonzero_return", ""),
            ("click-interrupted-code", click.exceptions.Exit(130), 130, "aborted", "nonzero_return", ""),
            ("system-none", SystemExit(None), 0, "ok", "system_exit", ""),
            ("system-success", SystemExit(0), 0, "ok", "system_exit", ""),
            ("system-failure", SystemExit(5), 5, "error", "system_exit", ""),
            ("system-message", SystemExit("exit detail"), 1, "error", "system_exit", "exit detail\n"),
        )
        for name, raised, expected_code, expected_status, expected_outcome, expected_stderr in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                app = base_cli.App(name=f"metadata-{name}")

                @app.command()
                def main(
                    ctx: base_cli.Context,
                    raised: BaseException = raised,
                ) -> None:
                    del ctx
                    raise raised

                home = Path(tmpdir)
                status, stderr = _run(app, home)
                _, metadata = _load_only_metadata(self, home)

                self.assertEqual(status, expected_code)
                self.assertEqual(stderr, expected_stderr)
                _assert_terminal_metadata(
                    self,
                    metadata,
                    status=expected_status,
                    outcome=expected_outcome,
                    exit_code=expected_code,
                )

    def test_unexpected_error_is_clean_but_persists_traceback(self) -> None:
        app = base_cli.App(name="metadata-unexpected")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise RuntimeError("private failure detail")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            metadata_path, metadata = _load_only_metadata(self, home)
            log_text = (metadata_path.parent / "logs" / "primary.log").read_text(encoding="utf-8")

        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", stderr)
        self.assertIn(f"Run ID: {metadata['run_id']}", stderr)
        self.assertIn("Diagnostic log:", stderr)
        self.assertIn("Re-run with --debug for a traceback.", stderr)
        self.assertNotIn("private failure detail", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertIn("Traceback", log_text)
        self.assertIn("RuntimeError: private failure detail", log_text)
        _assert_terminal_metadata(self, metadata, status="error", outcome="unexpected_error", exit_code=1)

    def test_debug_mirrors_unexpected_traceback_to_stderr(self) -> None:
        app = base_cli.App(name="metadata-debug")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise RuntimeError("debug failure detail")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home, ["--debug"])
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 1)
        self.assertIn("Traceback", stderr)
        self.assertIn("RuntimeError: debug failure detail", stderr)
        self.assertNotIn("Re-run with --debug", stderr)
        _assert_terminal_metadata(self, metadata, status="error", outcome="unexpected_error", exit_code=1)

    def test_unexpected_error_without_file_logging_reports_only_available_diagnostics(self) -> None:
        app = base_cli.App(name="metadata-no-file-error", log_to_file=False)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise RuntimeError("no-file private detail")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)

        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", stderr)
        self.assertIn("Run ID:", stderr)
        self.assertNotIn("Diagnostic log:", stderr)
        self.assertNotIn("no-file private detail", stderr)
        self.assertIn("Re-run with --debug for a traceback.", stderr)
        self.assertEqual(_metadata_files(home), [])

    def test_debug_shows_traceback_for_failure_before_context_activation(self) -> None:
        def fail_discovery(_cwd: Path) -> base_cli.ProjectInfo | None:
            raise RuntimeError("pre-context private detail")

        profile = base_cli.CliProfile.generic(discover_project=fail_discovery)
        app = base_cli.App(name="metadata-pre-context", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home, ["--debug"])

        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", stderr)
        self.assertIn("Traceback", stderr)
        self.assertIn("RuntimeError: pre-context private detail", stderr)
        self.assertNotIn("Re-run with --debug", stderr)
        self.assertNotIn("Run ID:", stderr)
        self.assertNotIn("Diagnostic log:", stderr)
        self.assertEqual(_metadata_files(home), [])

    def test_leading_debug_shows_traceback_for_failure_before_click_parsing(self) -> None:
        def fail_display_command() -> str | None:
            raise RuntimeError("pre-parser private detail")

        profile = replace(base_cli.CliProfile.generic(), display_command=fail_display_command)
        app = base_cli.App(name="metadata-pre-parser", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home, ["--debug"])

        self.assertEqual(status, 1)
        self.assertIn("Traceback", stderr)
        self.assertIn("RuntimeError: pre-parser private detail", stderr)
        self.assertNotIn("Diagnostic context was unavailable", stderr)

    def test_debug_in_an_option_value_position_never_exposes_pre_parser_failure(self) -> None:
        def fail_display_command() -> str | None:
            raise RuntimeError("value-position private detail")

        profile = replace(base_cli.CliProfile.generic(), display_command=fail_display_command)
        app = base_cli.App(name="metadata-value-position", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home, ["--config", "--debug"])

        self.assertEqual(status, 1)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("value-position private detail", stderr)
        self.assertIn("Diagnostic context was unavailable", stderr)

    def test_debug_literal_after_option_terminator_does_not_expose_traceback(self) -> None:
        def fail_discovery(_cwd: Path) -> base_cli.ProjectInfo | None:
            raise KeyError("literal-debug private detail")

        profile = base_cli.CliProfile.generic(discover_project=fail_discovery)
        app = base_cli.App(name="metadata-literal-debug", profile=profile)

        @app.command()
        @base_cli.argument("value")
        def main(ctx: base_cli.Context, value: str) -> None:
            del ctx, value

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home, ["--", "--debug"])

        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("literal-debug private detail", stderr)
        self.assertIn("Re-run with --debug for a traceback.", stderr)

    def test_config_derived_debug_applies_to_later_startup_failure(self) -> None:
        def fail_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
            raise KeyError("configured-debug private detail")

        profile = replace(
            base_cli.CliProfile.generic(
                load_config=lambda _project, _explicit: base_cli.ConfigSnapshot(
                    config={},
                    framework=base_cli.FrameworkConfig(log_level="debug"),
                    provenance={},
                ),
            ),
            resolve_runtime=fail_runtime,
        )
        app = base_cli.App(name="metadata-configured-debug-startup", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)

        self.assertEqual(status, 1)
        self.assertIn("Traceback", stderr)
        self.assertIn("KeyError: 'configured-debug private detail'", stderr)
        self.assertNotIn("Re-run with --debug", stderr)

    def test_config_debug_with_quiet_keeps_traceback_out_of_stderr_and_shows_hint(self) -> None:
        profile = base_cli.CliProfile.generic(
            load_config=lambda _project, _explicit: base_cli.ConfigSnapshot(
                config={},
                framework=base_cli.FrameworkConfig(log_level="debug"),
                provenance={},
            ),
        )
        app = base_cli.App(name="metadata-config-debug-quiet", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise RuntimeError("quiet private detail")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home, ["--quiet"])
            metadata_path, metadata = _load_only_metadata(self, home)
            log_text = (metadata_path.parent / "logs" / "primary.log").read_text(encoding="utf-8")

        self.assertEqual(status, 1)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("quiet private detail", stderr)
        self.assertIn("Re-run with --debug for a traceback.", stderr)
        self.assertIn("RuntimeError: quiet private detail", log_text)
        _assert_terminal_metadata(self, metadata, status="error", outcome="unexpected_error", exit_code=1)

    def test_plain_consumer_lifecycle_keys_do_not_change_framework_state(self) -> None:
        profile = base_cli.CliProfile.generic(
            load_config=lambda _project, _explicit: {
                "environment": "production",
                "log_level": "debug",
                "keep_temp": "false",
                "answer": 42,
            }
        )
        app = base_cli.App(name="opaque-config", profile=profile, log_to_file=False)
        seen: dict[str, object] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen.update(
                environment=ctx.environment,
                debug=ctx.debug,
                keep_temp=ctx.keep_temp,
                config=ctx.config,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            status, _stderr = _run(app, Path(tmpdir))

        self.assertEqual(status, 0)
        self.assertEqual(seen["environment"], "dev")
        self.assertFalse(seen["debug"])
        self.assertFalse(seen["keep_temp"])
        self.assertEqual(
            seen["config"],
            {"environment": "production", "log_level": "debug", "keep_temp": "false", "answer": 42},
        )

    def test_traceback_logging_interruption_cannot_replace_primary_exception(self) -> None:
        app = base_cli.App(name="metadata-traceback-interrupt")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            ctx.log.debug = mock.Mock(side_effect=KeyboardInterrupt())
            raise RuntimeError("primary private detail")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", stderr)
        self.assertNotIn("Interrupted.", stderr)
        _assert_terminal_metadata(self, metadata, status="error", outcome="unexpected_error", exit_code=1)

    def test_history_writer_cannot_override_core_terminal_outcome(self) -> None:
        observed_codes: list[int] = []

        def history_writer(
            ctx: base_cli.Context,
            _argv: list[str],
            _sensitive: set[str],
            _started: object,
            exit_code: int,
        ) -> None:
            observed_codes.append(exit_code)
            assert ctx.run_root is not None
            (ctx.run_root / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": ctx.run_id,
                        "status": "error",
                        "exit_code": 99,
                        "command": "metadata-history-authority",
                        "custom_history_field": "preserved",
                    }
                ),
                encoding="utf-8",
            )

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="metadata-history-authority", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, _ = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertEqual(observed_codes, [0])
        self.assertEqual(metadata["command"], "metadata-history-authority")
        self.assertEqual(metadata["custom_history_field"], "preserved")
        _assert_terminal_metadata(self, metadata, status="ok", outcome="success", exit_code=0)

    def test_history_failure_does_not_block_core_finalization(self) -> None:
        def fail_history(*_args: object) -> None:
            raise OSError("history unavailable")

        profile = replace(base_cli.CliProfile.generic(), history_writer=fail_history)
        app = base_cli.App(name="metadata-history-failure", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertIn("History finalization failed: history unavailable", stderr)
        _assert_terminal_metadata(self, metadata, status="ok", outcome="success", exit_code=0)

    def test_command_duration_excludes_history_writer_latency(self) -> None:
        observed: list[None] = []

        def history_writer(*_args: object) -> None:
            observed.append(None)

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="metadata-duration", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(
                app_module.time,
                "monotonic_ns",
                side_effect=(1_000_000_000, 1_025_600_000),
            ):
                status, _ = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertEqual(observed, [None])
        self.assertEqual(metadata["duration_ms"], 26)

    def test_terminal_write_failure_preserves_primary_outcome_and_discards_running_marker(self) -> None:
        cases = (("success", False, 0), ("failure", True, 1))
        for name, fail_command, expected_code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                app = base_cli.App(name=f"metadata-finalize-{name}")

                @app.command()
                def main(
                    ctx: base_cli.Context,
                    fail_command: bool = fail_command,
                ) -> None:
                    del ctx
                    if fail_command:
                        raise RuntimeError("command failure")

                home = Path(tmpdir)
                with mock.patch.object(RunRecorder, "finish", side_effect=RuntimeError("metadata unavailable")):
                    status, stderr = _run(app, home)

                self.assertEqual(status, expected_code)
                self.assertIn("Run metadata finalization failed", stderr)
                self.assertEqual(_metadata_files(home), [])
                self.assertEqual(logging.getLogger(f"base_cli.metadata-finalize-{name}").handlers, [])
                with self.assertRaisesRegex(RuntimeError, "context is not active"):
                    base_cli.get_current_context()

    def test_metadata_recovery_failure_cannot_replace_command_outcome(self) -> None:
        app = base_cli.App(name="metadata-recovery-failure")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with (
                mock.patch.object(RunRecorder, "finish", side_effect=RuntimeError("finish unavailable")),
                mock.patch.object(
                    RunRecorder,
                    "discard_owned_record",
                    side_effect=RuntimeError("recovery unavailable"),
                ),
            ):
                status, stderr = _run(app, home)

        self.assertEqual(status, 0)
        self.assertIn("Run metadata finalization failed", stderr)
        self.assertIn("Run metadata recovery failed", stderr)
        self.assertEqual(logging.getLogger("base_cli.metadata-recovery-failure").handlers, [])

    def test_finish_failure_removes_matching_history_owned_terminal_fields(self) -> None:
        def history_writer(
            ctx: base_cli.Context,
            _argv: list[str],
            _sensitive: set[str],
            _started: object,
            _exit_code: int,
        ) -> None:
            assert ctx.run_root is not None
            (ctx.run_root / "run.json").write_text(
                json.dumps({"run_id": ctx.run_id, "status": "error", "exit_code": 99}),
                encoding="utf-8",
            )

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="metadata-history-finish-failure", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(RunRecorder, "finish", side_effect=OSError("finish unavailable")):
                status, stderr = _run(app, home)
            metadata_files = _metadata_files(home)

        self.assertEqual(status, 0)
        self.assertIn("Run metadata finalization failed", stderr)
        self.assertEqual(metadata_files, [])

    def test_partial_terminal_write_is_removed_without_masking_success(self) -> None:
        app = base_cli.App(name="metadata-partial-write")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        real_write = lifecycle_module.write_private_json
        calls = 0

        def fail_second_write(path: Path, value: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text("{", encoding="utf-8")
                raise OSError("partial terminal write")
            real_write(path, value)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(lifecycle_module, "write_private_json", side_effect=fail_second_write):
                status, stderr = _run(app, home)
            metadata_files = _metadata_files(home)

        self.assertEqual(status, 0)
        self.assertEqual(calls, 2)
        self.assertIn("Run metadata finalization failed", stderr)
        self.assertEqual(metadata_files, [])

    def test_terminal_merge_rejects_metadata_from_a_different_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            layout = runtime_layout(cache_root, "metadata-stale-merge", "new-run")
            layout.run_root.mkdir(parents=True)
            metadata_path = layout.run_root / "run.json"
            metadata_path.write_text(
                json.dumps({"run_id": "old-run", "status": "ok", "stale_field": "do not copy"}),
                encoding="utf-8",
            )

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="custom-owner",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id="new-run",
                )

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="metadata-stale-merge", profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx

            with mock.patch.object(RunRecorder, "start", return_value=None):
                status, _ = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertEqual(metadata["run_id"], "new-run")
        self.assertEqual(metadata["owner"], "custom-owner")
        self.assertNotIn("stale_field", metadata)
        _assert_terminal_metadata(self, metadata, status="ok", outcome="success", exit_code=0)

    def test_start_write_failure_can_recover_with_terminal_snapshot(self) -> None:
        app = base_cli.App(name="metadata-start-failure")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(RunRecorder, "start", side_effect=RuntimeError("metadata unavailable")):
                status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertIn("Run metadata start failed: metadata unavailable", stderr)
        _assert_terminal_metadata(self, metadata, status="ok", outcome="success", exit_code=0)

    def test_interrupt_during_metadata_start_still_finalizes_and_tears_down(self) -> None:
        app = base_cli.App(name="metadata-start-interrupt")
        called: list[None] = []

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            called.append(None)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(RunRecorder, "start", side_effect=KeyboardInterrupt()):
                status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 130)
        self.assertEqual(called, [])
        self.assertIn("Interrupted.", stderr)
        _assert_terminal_metadata(self, metadata, status="aborted", outcome="interrupted", exit_code=130)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_interrupt_during_history_cannot_replace_settled_command_outcome(self) -> None:
        def interrupt_history(*_args: object) -> None:
            raise KeyboardInterrupt()

        profile = replace(base_cli.CliProfile.generic(), history_writer=interrupt_history)
        app = base_cli.App(name="metadata-history-interrupt", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertIn("History finalization failed: KeyboardInterrupt", stderr)
        _assert_terminal_metadata(self, metadata, status="ok", outcome="success", exit_code=0)
        self.assertEqual(logging.getLogger("base_cli.metadata-history-interrupt").handlers, [])

    def test_interrupt_during_terminal_write_cannot_replace_settled_command_outcome(self) -> None:
        app = base_cli.App(name="metadata-finish-interrupt")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        calls = 0

        def interrupt_finish(recorder: RunRecorder, *args: object, **kwargs: object) -> None:
            nonlocal calls
            del recorder, args, kwargs
            calls += 1
            raise KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(RunRecorder, "finish", new=interrupt_finish):
                status, stderr = _run(app, home)
            metadata_files = _metadata_files(home)

        self.assertEqual(status, 0)
        self.assertEqual(calls, 1)
        self.assertIn("Run metadata finalization failed", stderr)
        self.assertEqual(metadata_files, [])
        self.assertEqual(logging.getLogger("base_cli.metadata-finish-interrupt").handlers, [])

    def test_interrupt_during_cleanup_cannot_replace_settled_command_outcome(self) -> None:
        app = base_cli.App(name="metadata-cleanup-interrupt")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            ctx.on_cleanup(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, stderr = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertIn("Cleanup hook failed", stderr)
        _assert_terminal_metadata(self, metadata, status="ok", outcome="success", exit_code=0)
        self.assertEqual(logging.getLogger("base_cli.metadata-cleanup-interrupt").handlers, [])
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_terminal_metadata_refreshes_project_binding(self) -> None:
        app = base_cli.App(name="metadata-project-binding")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            ctx.bind_project("bound-project", Path("/tmp/bound-project"), Path("/tmp/bound-project/project.yml"))

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            status, _ = _run(app, home)
            _, metadata = _load_only_metadata(self, home)

        self.assertEqual(status, 0)
        self.assertEqual(metadata["project"], "bound-project")
        self.assertEqual(metadata["project_root"], str(Path("/tmp/bound-project").resolve()))
        self.assertEqual(metadata["manifest"], str(Path("/tmp/bound-project/project.yml").resolve()))

    def test_metadata_and_identity_compact_home_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            project = home / "project"
            manifest = project / "project.yml"
            project.mkdir()
            manifest.write_text("name: demo\n", encoding="utf-8")

            def discover(_cwd: Path) -> base_cli.ProjectInfo:
                return base_cli.ProjectInfo(root=project, manifest=manifest, name="demo")

            base_profile = base_cli.CliProfile.generic(
                cache_root=home / "cache",
                discover_project=discover,
            )

            def resolve_runtime(
                cli_name: str,
                project_info: base_cli.ProjectInfo | None,
            ) -> base_cli.RuntimeBinding:
                binding = base_profile.resolve_runtime(cli_name, project_info)
                return replace(binding, write_identity=True)

            profile = replace(base_profile, resolve_runtime=resolve_runtime)
            app = base_cli.App(name="metadata-path-compaction", profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx

            status, _ = _run(app, home)
            metadata_path, metadata = _load_only_metadata(self, home)
            identity_paths = sorted((home / "cache").glob("**/identity.json"))
            self.assertEqual(status, 0)
            self.assertEqual(len(identity_paths), 1, identity_paths)
            identity = json.loads(identity_paths[0].read_text(encoding="utf-8"))
            metadata_text = metadata_path.read_text(encoding="utf-8")

        self.assertEqual(metadata["project_root"], "~/project")
        self.assertEqual(metadata["manifest"], "~/project/project.yml")
        self.assertEqual(identity["project_root"], "~/project")
        self.assertEqual(identity["manifest"], "~/project/project.yml")
        self.assertNotIn(str(home), metadata_text)

    def test_parse_error_and_no_file_modes_do_not_own_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            parse_app = base_cli.App(name="metadata-parse-error")

            @parse_app.command()
            @base_cli.option("--name", required=True)
            def parse_main(ctx: base_cli.Context, name: str) -> None:
                del ctx, name

            status, _ = _run(parse_app, home)
            self.assertEqual(status, 2)
            self.assertEqual(_metadata_files(home), [])

            informational_app = base_cli.App(name="metadata-informational", version="1.2.3")

            @informational_app.command()
            def informational_main(ctx: base_cli.Context) -> None:
                del ctx

            for args in (["--help"], ["--version"]):
                status, _ = _run(informational_app, home, args)
                self.assertEqual(status, 0)
                self.assertEqual(_metadata_files(home), [])

            no_file_app = base_cli.App(name="metadata-no-file", log_to_file=False)

            @no_file_app.command()
            def no_file_main(ctx: base_cli.Context) -> None:
                del ctx

            status, _ = _run(no_file_app, home)
            self.assertEqual(status, 0)
            self.assertEqual(_metadata_files(home), [])

            dry_run_app = base_cli.App(name="metadata-dry-run")

            @dry_run_app.command()
            @base_cli.option("--dry-run", is_flag=True, dry_run=True)
            def dry_run_main(ctx: base_cli.Context, dry_run: bool) -> None:
                self.assertTrue(ctx.dry_run)
                self.assertTrue(dry_run)

            status, _ = _run(dry_run_app, home, ["--dry-run"])
            self.assertEqual(status, 0)
            self.assertEqual(_metadata_files(home), [])

    def test_inherited_runtime_does_not_mutate_parent_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            parent_run = cache_root / "parent" / "runs" / "parent-run"
            parent_run.mkdir(parents=True)
            parent_metadata = parent_run / "run.json"
            parent_payload = {"run_id": "parent-run", "status": "running", "custom": "parent-owned"}
            parent_metadata.write_text(json.dumps(parent_payload), encoding="utf-8")

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=runtime_layout(
                        cache_root,
                        "metadata-inherited",
                        "child-run",
                        namespace="parent",
                        inherited_run_root=parent_run,
                    ),
                    application_home=None,
                    runtime_owner="parent",
                    project_root=None,
                    project_name=None,
                    inherited_path=parent_run,
                    history_parent_run_id="parent-run",
                    run_id="child-run",
                )

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="metadata-inherited", profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx

            status, _ = _run(app, home)

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(parent_metadata.read_text(encoding="utf-8")), parent_payload)
            self.assertEqual(_metadata_files(home), [parent_metadata])


if __name__ == "__main__":
    unittest.main()
