from __future__ import annotations

import importlib.util
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import base_cli
import base_cli.app as app_module
import base_cli.context as context_module
from base_cli.testing import invoke


class _BrokenHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        del record

    def flush(self) -> None:
        raise OSError("flush unavailable")

    def close(self) -> None:
        raise OSError("close unavailable")


class _CommandFailure(RuntimeError):
    pass


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class AppLifecycleTests(unittest.TestCase):
    def test_history_failure_does_not_change_success_or_skip_teardown(self) -> None:
        def fail_history(*_args: object) -> None:
            raise RuntimeError("history unavailable")

        profile = replace(base_cli.CliProfile.generic(), history_writer=fail_history)
        app = base_cli.App(name="lifecycle-success", profile=profile)
        seen: dict[str, object] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["context"] = ctx
            seen["temp_dir"] = ctx.temp_dir
            seen["logger"] = ctx.log
            seen["cleanup_context"] = None

            def fail_cleanup() -> None:
                raise RuntimeError("cleanup unavailable")

            def record_cleanup_context() -> None:
                seen["cleanup_context"] = base_cli.get_current_context()

            ctx.on_cleanup(fail_cleanup)
            ctx.on_cleanup(record_cleanup_context)
            ctx.log.handlers.insert(0, _BrokenHandler())

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIsNone(result.exception)
            self.assertIs(seen["cleanup_context"], seen["context"])
            self.assertTrue(Path(seen["temp_dir"]).is_dir())
            self.assertEqual(list(Path(seen["temp_dir"]).iterdir()), [])
            self.assertEqual(seen["logger"].handlers, [])

        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()
        self.assertIn("History finalization failed: history unavailable", result.stderr)
        self.assertIn("Cleanup hook failed: cleanup unavailable", result.stderr)
        self.assertIn("Log handler flush failed: flush unavailable", result.stderr)
        self.assertIn("Log handler close failed: close unavailable", result.stderr)

    def test_cleanup_failure_does_not_change_success_or_leak_context(self) -> None:
        app = base_cli.App(name="cleanup-failure")
        seen: dict[str, object] = {}
        cleanup_calls: list[None] = []

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["context"] = ctx
            seen["original_cleanup"] = ctx.cleanup

            def fail_cleanup() -> None:
                cleanup_calls.append(None)
                raise RuntimeError("cleanup unavailable")

            ctx.cleanup = fail_cleanup  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIsNone(result.exception)
            self.assertEqual(cleanup_calls, [None])
            self.assertIn("Lifecycle cleanup failed: cleanup unavailable", result.stderr)
            with self.assertRaisesRegex(RuntimeError, "context is not active"):
                base_cli.get_current_context()

            original_cleanup = seen["original_cleanup"]
            self.assertTrue(callable(original_cleanup))
            original_cleanup()

    def test_history_failure_does_not_mask_command_failure(self) -> None:
        def fail_history(*_args: object) -> None:
            raise OSError("history unavailable")

        profile = replace(base_cli.CliProfile.generic(), history_writer=fail_history)
        app = base_cli.App(name="lifecycle-failure", profile=profile)
        primary_failure = _CommandFailure("command failed")
        seen: dict[str, object] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["temp_dir"] = ctx.temp_dir
            seen["logger"] = ctx.log
            ctx.on_cleanup(lambda: seen.update(cleanup_called=True))
            raise primary_failure

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))

            self.assertEqual(result.exit_code, 1)
            self.assertIsInstance(result.exception, SystemExit)
            self.assertIsNot(result.exception, primary_failure)
            self.assertTrue(seen["cleanup_called"])
            self.assertTrue(Path(seen["temp_dir"]).is_dir())
            self.assertEqual(list(Path(seen["temp_dir"]).iterdir()), [])
            self.assertEqual(seen["logger"].handlers, [])

        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()
        self.assertIn("History finalization failed: history unavailable", result.stderr)
        self.assertIn("Error: Unexpected internal error.", result.stderr)
        self.assertNotIn("command failed", result.stderr)

    def test_invoke_can_capture_original_unexpected_exception_for_debugging(self) -> None:
        app = base_cli.App(name="lifecycle-reraise", log_to_file=False)
        primary_failure = _CommandFailure("command failed")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise primary_failure

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIs(result.exception, primary_failure)

    def test_non_os_temp_cleanup_failure_still_closes_handlers(self) -> None:
        app = base_cli.App(name="cleanup-runtime-failure")
        seen: dict[str, object] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["logger"] = ctx.log

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(
                context_module,
                "remove_owned_temp_directory",
                side_effect=RuntimeError("cleanup implementation failed"),
            ):
                result = invoke(app, [], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Temp directory cleanup failed", result.stderr)
        self.assertEqual(seen["logger"].handlers, [])
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_active_context_reset_interruption_uses_direct_recovery(self) -> None:
        app = base_cli.App(name="context-reset-interrupt")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(
                app_module,
                "reset_current_context",
                side_effect=KeyboardInterrupt(),
            ),
        ):
            result = invoke(app, [], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_handler_removal_interruption_uses_direct_detach_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            logger = logging.Logger("isolated-handler-removal")
            logger.addHandler(logging.NullHandler())
            logger.addHandler(logging.NullHandler())
            logger.removeHandler = mock.Mock(side_effect=KeyboardInterrupt())
            context = base_cli.Context(
                cli_name="handler-removal-interrupt",
                run_id="run-1",
                state_dir=root / "state",
                log_dir=root / "logs",
                cache_dir=root / "cache",
                temp_dir=root / "tmp",
                log_file=None,
                config={},
                environment="dev",
                debug=False,
                keep_temp=False,
                log=logger,
            )

            context.cleanup()

        self.assertEqual(logger.handlers, [])

    def test_context_var_reset_helper_restores_previous_value_after_interrupt(self) -> None:
        class Token:
            MISSING = object()
            old_value = "parent"

        class InterruptedVariable:
            def __init__(self) -> None:
                self.restored: object | None = None

            def reset(self, _token: object) -> None:
                raise KeyboardInterrupt()

            def set(self, value: object) -> None:
                self.restored = value

        variable = InterruptedVariable()

        app_module._reset_context_var(variable, Token())

        self.assertEqual(variable.restored, "parent")


if __name__ == "__main__":
    unittest.main()
