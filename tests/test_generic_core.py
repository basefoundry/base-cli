from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import base_cli
from base_cli import history
from base_cli._runtime import runtime_layout
from base_cli.context import Context


class GenericCoreTests(unittest.TestCase):
    def test_generic_runtime_layout_uses_application_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            layout = runtime_layout(root, "demo_tool", "run-1")

        self.assertEqual(layout.owner_root, root / "demo_tool")
        self.assertIn("demo_tool", layout.run_root.name)
        self.assertEqual(layout.cache_dir, root / "demo_tool" / "cache" / "components" / "demo_tool")

    def test_history_writer_requires_consumer_selected_path(self) -> None:
        record = {
            "schema_version": 1,
            "event": "finished",
            "run_id": "run-1",
            "command": "demo",
            "status": "ok",
            "exit_code": 0,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history" / "runs.jsonl"
            history.write_history_record(path, record)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            mode = path.stat().st_mode & 0o777

        self.assertEqual(loaded, record)
        self.assertEqual(mode, 0o600)

    def test_finished_record_has_no_product_version_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            context = Context(
                cli_name="demo_tool",
                run_id="run-1",
                state_dir=root / "state",
                log_dir=root / "logs",
                cache_dir=root / "cache",
                temp_dir=root / "tmp",
                log_file=root / "logs" / "run.log",
                config={},
                environment="dev",
                debug=False,
                keep_temp=False,
                log=logging.getLogger("generic-core-test"),
            )
            started = history.utc_now() - timedelta(seconds=1)
            record = history.build_finished_record(context, ["demo_tool"], set(), started, 0)

        self.assertEqual(record["command"], "demo-tool")
        self.assertNotIn("base_version", record)

    def test_base_specific_path_helpers_are_not_in_generic_module(self) -> None:
        import base_cli.paths as paths

        self.assertFalse(hasattr(paths, "base_cache_root"))
        self.assertFalse(hasattr(paths, "discover_manifest"))
        self.assertFalse(hasattr(paths, "normalize_runtime_owner"))
        self.assertFalse(hasattr(base_cli.CliProfile, "legacy_base"))
