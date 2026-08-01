from __future__ import annotations

import importlib.util
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base_cli
from base_cli._runtime import prune_log_files
from base_cli.testing import invoke


def generic_app(**kwargs: object) -> base_cli.App:
    return base_cli.App(profile=base_cli.CliProfile.generic(), **kwargs)


def write_log_file(path: Path, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old log\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


class AppLogRetentionTests(unittest.TestCase):
    def test_ignores_stale_paths_in_retention_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "runs" / "seed" / "logs"
            old_a = log_dir / "20260620T120000_a.log"
            old_b = log_dir / "20260621T120000_b.log"
            old_c = log_dir / "20260622T120000_c.log"
            current = log_dir / "20260623T120000_current.log"
            phantom = log_dir / "deleted" / "phantom.log"
            for path, mtime in ((old_a, 1), (old_b, 2), (old_c, 3)):
                write_log_file(path, mtime)
            (log_dir / ".base-cli-log-index.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "logs": [str(path.resolve()) for path in (old_a, old_b, old_c, phantom)],
                    }
                ),
                encoding="utf-8",
            )

            prune_log_files(log_dir, current, 3, logging.getLogger(__name__))

            self.assertFalse(old_a.exists())
            self.assertTrue(old_b.exists())
            self.assertTrue(old_c.exists())
            current.write_text("current log\n", encoding="utf-8")
            self.assertEqual(len(tuple(log_dir.rglob("*.log"))), 3)
            index = json.loads((log_dir / ".base-cli-log-index.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {Path(path) for path in index["logs"]},
                {old_b.resolve(), old_c.resolve(), current.resolve()},
            )

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_uses_retention_index_after_initial_discovery(self) -> None:
        app = generic_app(name="retention-index", max_log_files=2)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            ctx.log.info("indexed retention")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            first = invoke(app, home=home)
            self.assertEqual(first.exit_code, 0, first.output)

            with mock.patch(
                "base_cli._runtime.Path.glob",
                side_effect=AssertionError("retention should use its index after initial discovery"),
            ):
                second = invoke(app, home=home)

        self.assertEqual(second.exit_code, 0, second.output)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_prunes_oldest_default_logs(self) -> None:
        app = generic_app(name="retention-demo", max_log_files=2)
        seen = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["log_file"] = ctx.log_file
            ctx.log.info("retention run")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            log_dir = home / ".cache" / "retention-demo" / "runs" / "seed" / "logs"
            oldest = log_dir / "20260620T120000_oldest.log"
            newest = log_dir / "20260621T120000_newest.log"
            write_log_file(oldest, 1)
            write_log_file(newest, 2)

            result = invoke(app, home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())
            self.assertIsNotNone(seen["log_file"])
            self.assertTrue(seen["log_file"].exists())
            self.assertEqual(len(tuple((home / ".cache" / "retention-demo" / "runs").rglob("*.log"))), 2)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_prunes_by_filename_when_mtimes_disagree(self) -> None:
        app = generic_app(name="retention-filename", max_log_files=2)
        seen = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["log_file"] = ctx.log_file
            ctx.log.info("filename retention")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            log_dir = home / ".cache" / "retention-filename" / "runs" / "seed" / "logs"
            older_by_name = log_dir / "20260620T120000_old.log"
            newer_by_name = log_dir / "20260621T120000_new.log"
            write_log_file(older_by_name, 2)
            write_log_file(newer_by_name, 1)

            result = invoke(app, home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse(older_by_name.exists())
            self.assertTrue(newer_by_name.exists())
            self.assertIsNotNone(seen["log_file"])
            self.assertTrue(seen["log_file"].exists())
            self.assertEqual(len(tuple((home / ".cache" / "retention-filename" / "runs").rglob("*.log"))), 2)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_preserves_current_log_file(self) -> None:
        app = generic_app(name="retention-current", max_log_files=1)
        seen = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["log_file"] = ctx.log_file
            ctx.log.info("keep current")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            log_dir = home / ".cache" / "retention-current" / "runs" / "seed" / "logs"
            old_a = log_dir / "old-a.log"
            old_b = log_dir / "old-b.log"
            write_log_file(old_a, 1)
            write_log_file(old_b, 2)

            result = invoke(app, home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse(old_a.exists())
            self.assertFalse(old_b.exists())
            self.assertIsNotNone(seen["log_file"])
            self.assertTrue(seen["log_file"].exists())
            self.assertEqual(
                tuple(path.resolve() for path in (home / ".cache" / "retention-current" / "runs").rglob("*.log")),
                (seen["log_file"].resolve(),),
            )

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_keeps_logs_when_count_is_within_limit(self) -> None:
        app = generic_app(name="retention-within-limit", max_log_files=3)
        seen = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["log_file"] = ctx.log_file
            ctx.log.info("within retention limit")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            log_dir = home / ".cache" / "retention-within-limit" / "runs" / "seed" / "logs"
            old_a = log_dir / "20260620T120000_a.log"
            old_b = log_dir / "20260621T120000_b.log"
            write_log_file(old_a, 1)
            write_log_file(old_b, 2)

            result = invoke(app, home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(old_a.exists())
            self.assertTrue(old_b.exists())
            self.assertIsNotNone(seen["log_file"])
            self.assertTrue(seen["log_file"].exists())
            self.assertEqual(len(tuple((home / ".cache" / "retention-within-limit" / "runs").rglob("*.log"))), 3)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_is_disabled_by_default(self) -> None:
        app = generic_app(name="retention-unset")
        seen = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["log_file"] = ctx.log_file
            ctx.log.info("no retention")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            log_dir = home / ".cache" / "retention-unset" / "runs" / "seed" / "logs"
            old_a = log_dir / "old-a.log"
            old_b = log_dir / "old-b.log"
            write_log_file(old_a, 1)
            write_log_file(old_b, 2)

            result = invoke(app, home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(old_a.exists())
            self.assertTrue(old_b.exists())
            self.assertIsNotNone(seen["log_file"])
            self.assertTrue(seen["log_file"].exists())
            self.assertEqual(len(tuple((home / ".cache" / "retention-unset" / "runs").rglob("*.log"))), 3)

    @unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
    def test_skips_no_durable_write_modes(self) -> None:
        dry_run_app = generic_app(name="retention-dry-run", max_log_files=1)
        dry_seen = {}

        @dry_run_app.command()
        @base_cli.option("--dry-run", is_flag=True)
        def dry_run_main(ctx: base_cli.Context, dry_run: bool) -> None:
            dry_seen["dry_run"] = dry_run
            dry_seen["log_file"] = ctx.log_file
            ctx.log.info("dry retention")

        no_file_app = generic_app(
            name="retention-no-file",
            log_to_file=False,
            max_log_files=1,
        )
        no_file_seen = {}

        @no_file_app.command()
        def no_file_main(ctx: base_cli.Context) -> None:
            no_file_seen["log_file"] = ctx.log_file
            ctx.log.info("no file retention")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            dry_log_dir = home / ".cache" / "retention-dry-run" / "runs" / "dry-seed" / "logs"
            no_file_log_dir = home / ".cache" / "retention-no-file" / "runs" / "no-file-seed" / "logs"
            dry_old = dry_log_dir / "old.log"
            no_file_old = no_file_log_dir / "old.log"
            write_log_file(dry_old, 1)
            write_log_file(no_file_old, 1)

            dry_result = invoke(dry_run_app, ["--dry-run"], home=home)
            no_file_result = invoke(no_file_app, home=home)

            self.assertEqual(dry_result.exit_code, 0, dry_result.output)
            self.assertEqual(no_file_result.exit_code, 0, no_file_result.output)
            self.assertTrue(dry_seen["dry_run"])
            self.assertIsNone(dry_seen["log_file"])
            self.assertIsNone(no_file_seen["log_file"])
            self.assertTrue(dry_old.exists())
            self.assertTrue(no_file_old.exists())
            self.assertEqual(tuple(dry_log_dir.glob("*.log")), (dry_old,))
            self.assertEqual(tuple(no_file_log_dir.glob("*.log")), (no_file_old,))
