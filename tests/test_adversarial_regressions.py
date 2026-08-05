from __future__ import annotations

import io
import json
import multiprocessing as multiprocessing_module
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import base_cli
from hypothesis import given, settings, strategies as st

from base_cli._private_files import write_private_json
from base_cli._runtime import prune_run_bundles
from base_cli.command_protocol import (
    BOOLEAN,
    CommandProtocolError,
    CommandSchemaRegistry,
    NULLABLE_STRING,
    STRING,
    dumps_records,
    loads_records,
)
from base_cli.history import append_history_line
from base_cli.redaction import REDACTED, redact_argv
from base_cli.testing import invoke


SEEDS = (17, 29, 41, 53)
_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\0"),
    max_size=80,
)


@st.composite
def _record_strategy(draw: st.DrawFn) -> dict[str, str | bool | None]:
    return {
        "name": draw(_SAFE_TEXT),
        "enabled": draw(st.booleans()),
        "note": draw(st.one_of(st.none(), _SAFE_TEXT)),
        "command": draw(_SAFE_TEXT),
    }


def _append_history_worker(path_text: str, seed: int, count: int) -> None:
    path = Path(path_text)
    for ordinal in range(count):
        payload = json.dumps({"seed": seed, "ordinal": ordinal}, sort_keys=True)
        append_history_line(path, payload + "\n")


def _write_metadata_worker(path_text: str, seed: int, count: int) -> None:
    path = Path(path_text)
    for ordinal in range(count):
        write_private_json(path, {"seed": seed, "ordinal": ordinal, "status": "ok"})


def _write_log_worker(path_text: str, seed: int, count: int) -> None:
    from base_cli.logging import configure_logger

    stream = io.StringIO()
    logger = configure_logger(
        f"adversarial-{seed}",
        Path(path_text),
        debug=True,
        quiet=True,
        stream=stream,
        run_id=f"seed-{seed}",
    )
    for ordinal in range(count):
        logger.info("seed=%s ordinal=%s", seed, ordinal)
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


def _prune_worker(runs_root_text: str) -> None:
    prune_run_bundles(
        Path(runs_root_text),
        policy=base_cli.RetentionPolicy(max_bundles=2),
    )


def _run_processes(target: object, args_list: list[tuple[object, ...]]) -> None:
    context = multiprocessing_module.get_context("spawn")
    processes = [context.Process(target=target, args=args) for args in args_list]  # type: ignore[arg-type]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise AssertionError("adversarial worker exceeded its 30-second budget")
            if process.exitcode != 0:
                raise AssertionError(f"adversarial worker exited with {process.exitcode}")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


class ProtocolAndRedactionPropertyTests(unittest.TestCase):
    @settings(max_examples=80, deadline=None, derandomize=True)
    @given(st.lists(_record_strategy(), max_size=5))
    def test_protocol_round_trip_is_total_for_generated_records(
        self,
        records: list[dict[str, str | bool | None]],
    ) -> None:
        registry = CommandSchemaRegistry()
        registry.register(
            "fuzz-record",
            {"name": STRING, "enabled": BOOLEAN, "note": NULLABLE_STRING, "command": STRING},
        )

        payload = dumps_records("fuzz-record", records, registry=registry)

        self.assertEqual(
            loads_records(payload, expected_record_type="fuzz-record", registry=registry),
            ("fuzz-record", tuple(records)),
        )

    @settings(max_examples=120, deadline=None, derandomize=True)
    @given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=256))
    def test_protocol_fuzzer_rejects_or_decodes_without_leaking_unexpected_errors(self, payload: str) -> None:
        registry = CommandSchemaRegistry()
        registry.register("fuzz-record", {"name": STRING})

        try:
            loads_records(payload, registry=registry)
        except CommandProtocolError:
            return

    @settings(max_examples=100, deadline=None, derandomize=True)
    @given(
        secret=_SAFE_TEXT.filter(lambda value: value not in {"", REDACTED}),
        visible=_SAFE_TEXT,
    )
    def test_redaction_property_preserves_argv_shape_and_hides_generated_secrets(
        self,
        secret: str,
        visible: str,
    ) -> None:
        argv = ["tool", "--token", secret, "--label", visible]

        redacted = redact_argv(argv, {"token"})

        self.assertEqual(len(redacted), len(argv))
        self.assertEqual(redacted[2], REDACTED)
        self.assertNotEqual(redacted[2], secret)


class MultiprocessingRegressionTests(unittest.TestCase):
    def test_history_appends_remain_complete_across_spawned_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.jsonl"
            _run_processes(
                _append_history_worker,
                [(str(path), seed, 16) for seed in SEEDS],
            )

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(records), len(SEEDS) * 16)
        self.assertEqual(
            {(record["seed"], record["ordinal"]) for record in records},
            {(seed, ordinal) for seed in SEEDS for ordinal in range(16)},
        )

    def test_atomic_metadata_writers_leave_one_valid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.json"
            _run_processes(
                _write_metadata_worker,
                [(str(path), seed, 20) for seed in SEEDS],
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            temporary_files = tuple(path.parent.glob(f".{path.name}.*.tmp"))

        self.assertEqual(payload["status"], "ok")
        self.assertIn(payload["seed"], SEEDS)
        self.assertIn(payload["ordinal"], range(20))
        self.assertEqual(temporary_files, ())

    def test_log_file_writers_do_not_leave_partial_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "parallel.log"
            _run_processes(
                _write_log_worker,
                [(str(path), seed, 16) for seed in SEEDS],
            )

            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), len(SEEDS) * 16)
        self.assertTrue(all(" seed=" in line and " ordinal=" in line for line in lines))

    def test_extension_discovery_metadata_cache_is_thread_safe_and_single_snapshot(self) -> None:
        entry_point = SimpleNamespace(
            group=base_cli.COMMAND_ENTRY_POINT_GROUP,
            name="adversarial",
            value="sample:register",
            extras=(),
            dist=SimpleNamespace(name="sample", version="1.0"),
        )
        calls = 0
        calls_lock = Lock()

        def provider() -> tuple[SimpleNamespace]:
            nonlocal calls
            with calls_lock:
                calls += 1
            return (entry_point,)

        discovery = base_cli.ExtensionDiscovery(entry_points=provider)
        with ThreadPoolExecutor(max_workers=8) as executor:
            snapshots = list(executor.map(lambda _index: discovery.list_commands(), range(32)))

        self.assertEqual(calls, 1)
        self.assertTrue(all(snapshot == snapshots[0] for snapshot in snapshots))
        self.assertEqual(snapshots[0][0].key, "base_cli.commands:adversarial")

    @unittest.skipUnless(os.name != "nt", "POSIX retention locking is covered on Windows through single-process tests")
    def test_run_bundle_retention_remains_bounded_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_root = Path(tmpdir) / "runs"
            runs_root.mkdir()
            for index in range(8):
                bundle = runs_root / f"run-{index}"
                (bundle / "logs").mkdir(parents=True)
                write_private_json(
                    bundle / "run.json",
                    {
                        "run_id": bundle.name,
                        "status": "ok",
                        "started_at": f"2020-01-{index + 1:02d}T00:00:00Z",
                        "preserve": False,
                    },
                )

            _run_processes(_prune_worker, [(str(runs_root),) for _seed in SEEDS])

            bundles = [path for path in runs_root.iterdir() if path.is_dir() and not path.name.startswith(".")]
            index = json.loads((runs_root / ".base-cli-run-index.json").read_text(encoding="utf-8"))

        self.assertLessEqual(len(bundles), 2)
        self.assertEqual(index["version"], 1)
        self.assertLessEqual(len(index["bundles"]), 2)


class SignalRegressionTests(unittest.TestCase):
    def test_keyboard_interrupt_finishes_lifecycle_without_leaked_context(self) -> None:
        app = base_cli.App(name="adversarial-interrupt")
        seen: dict[str, object] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["temp_dir"] = ctx.temp_dir
            seen["logger"] = ctx.log
            raise KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            result = invoke(app, [], home=home)
            metadata_paths = tuple((home / ".cache").rglob("run.json"))
            payload = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
            temp_dir = Path(seen["temp_dir"])
            temp_contents = tuple(temp_dir.iterdir()) if temp_dir.is_dir() else ()
            logger_handlers = list(seen["logger"].handlers)  # type: ignore[union-attr]

        self.assertEqual(result.exit_code, base_cli.ExitCode.INTERRUPTED)
        self.assertIn("Interrupted.", result.stderr)
        self.assertEqual(len(metadata_paths), 1)
        self.assertEqual(payload["outcome"], "interrupted")
        self.assertEqual(payload["status"], "error")
        self.assertEqual(temp_contents, ())
        self.assertEqual(logger_handlers, [])
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_click_abort_is_recorded_as_a_terminal_aborted_outcome(self) -> None:
        import click

        app = base_cli.App(name="adversarial-abort")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            raise click.Abort()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            result = invoke(app, [], home=home)
            metadata_paths = tuple((home / ".cache").rglob("run.json"))
            payload = json.loads(metadata_paths[0].read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, base_cli.ExitCode.FAILURE)
        self.assertIn("Aborted!", result.stderr)
        self.assertEqual(payload["outcome"], "aborted")
        self.assertEqual(payload["status"], "error")

    @unittest.skipUnless(os.name != "nt", "SIGINT subprocess semantics are platform-specific")
    def test_real_sigint_returns_130_and_persists_interrupted_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache = home / ".cache"
            ready = root / "ready"
            script = textwrap.dedent(
                """
                import os
                import time
                from pathlib import Path

                import base_cli

                app = base_cli.App(name="signal-child")
                ready = Path(os.environ["BASE_CLI_READY"])

                @app.command()
                def main(ctx: base_cli.Context) -> None:
                    del ctx
                    ready.write_text("ready", encoding="utf-8")
                    while True:
                        time.sleep(1)

                raise SystemExit(base_cli.run_app(app, []))
                """
            )
            environment = dict(os.environ)
            package_root = Path(__file__).resolve().parents[1] / "lib" / "python"
            environment["PYTHONPATH"] = f"{package_root}{os.pathsep}{environment.get('PYTHONPATH', '')}"
            environment.update(
                {
                    "HOME": str(home),
                    "BASE_CLI_CACHE_DIR": str(cache),
                    "BASE_CLI_READY": str(ready),
                }
            )
            process = subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not ready.exists():
                    stdout, stderr = process.communicate(timeout=2)
                    self.fail(f"signal child did not become ready: {stdout}\n{stderr}")
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                self.fail(f"signal child did not exit: {stdout}\n{stderr}")

            metadata_paths = tuple(cache.rglob("run.json"))
            payload = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
            temp_dir = metadata_paths[0].parent / "tmp" / "signal-child" / payload["run_id"]
            temp_contents = tuple(temp_dir.iterdir())

        self.assertEqual(process.returncode, base_cli.ExitCode.INTERRUPTED, stderr)
        self.assertIn("Interrupted.", stderr)
        self.assertEqual(len(metadata_paths), 1)
        self.assertEqual(payload["outcome"], "interrupted")
        self.assertEqual(payload["status"], "error")
        self.assertEqual(temp_contents, ())


if __name__ == "__main__":
    unittest.main()
