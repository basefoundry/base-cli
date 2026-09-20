from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest import mock

import base_cli
from base_cli.logging import SecureLogFileHandler


def _outer_callback(app: base_cli.App, nested_statuses: list[int]) -> Callable[..., None]:
    @base_cli.option("--fail-inner", is_flag=True)
    def main(ctx: base_cli.Context[Any, Any, Any], fail_inner: bool) -> None:
        ctx.log.info("outer-before")
        inner_argv = ["--unknown"] if fail_inner else []
        nested_statuses.append(base_cli.run_app(app, inner_argv))
        ctx.log.info("outer-after")

    return main


def _file_handler_close_tracker(
    close_calls: list[SecureLogFileHandler],
    original_close: Callable[[SecureLogFileHandler], None],
) -> Callable[[SecureLogFileHandler], None]:
    def track_close(handler: SecureLogFileHandler) -> None:
        close_calls.append(handler)
        original_close(handler)

    return track_close


class RunAppReentrancyTests(unittest.TestCase):
    def test_same_identity_nested_success_and_failure_attempts_leave_outer_logging_intact(self) -> None:
        for nested_args in ([], ["--unknown"]):
            with self.subTest(nested_args=nested_args), tempfile.TemporaryDirectory() as tmpdir:
                home = Path(tmpdir)
                nested_statuses: list[int] = []
                app = base_cli.App(name=f"nested-{len(nested_args)}-{home.name}")
                app.command()(_outer_callback(app, nested_statuses))

                close_calls: list[SecureLogFileHandler] = []
                original_close = SecureLogFileHandler.close
                track_close = _file_handler_close_tracker(close_calls, original_close)

                outer_args = ["--fail-inner"] if nested_args else []
                with mock.patch.object(SecureLogFileHandler, "close", new=track_close):
                    result = base_cli.testing.invoke(app, outer_args, home=home)
                    metadata_files = list((home / ".cache").glob("**/run.json"))

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(nested_statuses, [base_cli.ExitCode.FAILURE])
                self.assertIn("Nested run_app()", result.stderr)
                self.assertEqual(len(metadata_files), 1)
                log_text = (metadata_files[0].parent / "logs" / "primary.log").read_text(encoding="utf-8")
                self.assertIn("outer-before", log_text)
                self.assertIn("outer-after", log_text)
                self.assertEqual(log_text.count("outer-before"), 1)
                self.assertEqual(log_text.count("outer-after"), 1)
        self.assertEqual(len(close_calls), 1)

    def test_nested_invocation_in_json_mode_preserves_the_output_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            nested_statuses: list[int] = []
            app = base_cli.App(name=f"nested-json-{home.name}")
            app.lifecycle_options = base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json"),
            )
            app.command()(_outer_callback(app, nested_statuses))

            result = base_cli.testing.invoke(app, ["--json", "--fail-inner"], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "base-cli.output")
        nested = json.loads(payload["details"]["stdout"])
        self.assertEqual(nested["schema"], "base-cli.error")
        self.assertEqual(nested["code"], "invocation_rejected")
        self.assertNotIn("Nested run_app()", result.stderr)

    def test_concurrent_in_process_invocation_fails_fast_without_entering_second_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            started = threading.Event()
            release = threading.Event()
            callback_calls: list[str] = []
            stderr = io.StringIO()
            app = base_cli.App(
                name=f"concurrent-{Path(tmpdir).name}",
                log_to_file=False,
                profile=base_cli.CliProfile.generic(cache_root=Path(tmpdir) / "cache"),
            )

            @app.command()
            def main(ctx: base_cli.Context[Any, Any, Any]) -> None:
                del ctx
                callback_calls.append("entered")
                started.set()
                release.wait(timeout=5)

            with mock.patch("sys.stderr", stderr), ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(base_cli.run_app, app, [])
                self.assertTrue(started.wait(timeout=2))
                second_status = base_cli.run_app(app, [])
                release.set()
                first_status = first.result(timeout=2)

        self.assertEqual(first_status, base_cli.ExitCode.SUCCESS)
        self.assertEqual(second_status, base_cli.ExitCode.FAILURE)
        self.assertEqual(callback_calls, ["entered"])
        self.assertIn("Concurrent run_app()", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
