from __future__ import annotations

import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import base_cli._private_files as private_files
from base_cli import RetentionPolicy
from base_cli._private_files import write_private_json
from base_cli._runtime import prune_run_bundles


def _bundle(
    root: Path,
    name: str,
    *,
    status: str = "ok",
    started_at: str = "2020-01-01T00:00:00Z",
    size: int = 1,
    preserve: bool = False,
) -> Path:
    path = root / name
    (path / "logs").mkdir(parents=True)
    (path / "logs" / "primary.log").write_bytes(b"x" * size)
    write_private_json(
        path / "run.json",
        {
            "run_id": name,
            "status": status,
            "started_at": started_at,
            "preserve": preserve,
        },
    )
    return path


class RunBundleRetentionTests(unittest.TestCase):
    def test_count_removes_complete_bundles_as_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            root.mkdir()
            old = _bundle(root, "old", started_at="2020-01-01T00:00:00Z")
            newer = _bundle(root, "newer", started_at="2020-01-02T00:00:00Z")
            (old / "tmp").mkdir()
            (old / "tmp" / "diagnostic.txt").write_text("keep with bundle", encoding="utf-8")

            prune_run_bundles(
                root,
                root / "active",
                policy=RetentionPolicy(max_bundles=2),
                logger=logging.getLogger(__name__),
            )

            self.assertFalse(old.exists())
            self.assertTrue(newer.exists())
            self.assertTrue((root / ".base-cli-run-index.json").is_file())

    def test_preserved_and_running_bundles_survive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            root.mkdir()
            preserved = _bundle(root, "preserved", preserve=True)
            running = _bundle(root, "running", status="running")
            removable = _bundle(root, "removable", started_at="2020-01-01T00:00:00Z")

            prune_run_bundles(
                root,
                root / "active",
                policy=RetentionPolicy(max_bundles=1),
                logger=logging.getLogger(__name__),
            )

            self.assertTrue(preserved.exists())
            self.assertTrue(running.exists())
            self.assertFalse(removable.exists())

    def test_stale_running_bundle_is_recoverable_with_age_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            root.mkdir()
            stale = _bundle(root, "stale", status="running", started_at="2020-01-01T00:00:00Z")

            prune_run_bundles(
                root,
                root / "active",
                policy=RetentionPolicy(max_age_seconds=60),
                logger=logging.getLogger(__name__),
                now=1_600_000_000,
            )

            self.assertFalse(stale.exists())

    def test_aborted_bundle_is_indexed_as_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            root.mkdir()
            _bundle(root, "aborted", status="aborted")

            prune_run_bundles(
                root,
                policy=RetentionPolicy(max_bundles=1),
                logger=logging.getLogger(__name__),
            )

            payload = json.loads((root / ".base-cli-run-index.json").read_text(encoding="utf-8"))

        self.assertEqual([bundle["status"] for bundle in payload["bundles"]], ["aborted"])

    def test_symlink_bundle_is_not_followed_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            external = Path(tmpdir) / "external"
            root.mkdir()
            external.mkdir()
            victim = external / "victim.txt"
            victim.write_text("do not delete", encoding="utf-8")
            (root / "linked").symlink_to(external, target_is_directory=True)

            prune_run_bundles(
                root,
                root / "active",
                policy=RetentionPolicy(max_bundles=1),
                logger=logging.getLogger(__name__),
            )

            self.assertTrue(victim.exists())
            self.assertTrue((root / "linked").is_symlink())

    def test_failed_atomic_json_write_preserves_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.json"
            write_private_json(path, {"status": "ok", "run_id": "stable"})
            with mock.patch("base_cli._private_files.json.dump", side_effect=TypeError("boom")):
                with self.assertRaises(TypeError):
                    write_private_json(path, {"status": "error"})
            self.assertEqual(path.read_text(encoding="utf-8").strip(), '{"run_id": "stable", "status": "ok"}')

    def test_atomic_json_write_refuses_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            victim = root / "victim.json"
            victim.write_text("unchanged", encoding="utf-8")
            destination = root / "run.json"
            destination.symlink_to(victim)
            with self.assertRaises(OSError):
                write_private_json(destination, {"status": "error"})
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

    def test_atomic_json_write_refuses_symlink_destination_without_directory_handles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            victim = root / "victim.json"
            victim.write_text("unchanged", encoding="utf-8")
            destination = root / "run.json"
            destination.symlink_to(victim)
            with mock.patch.object(private_files, "_open_parent_directory", return_value=None):
                with self.assertRaisesRegex(OSError, "refusing to replace symlink"):
                    write_private_json(destination, {"status": "error"})
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

    def test_concurrent_metadata_writers_leave_one_valid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.json"

            def write(index: int) -> None:
                write_private_json(path, {"writer": index, "status": "ok"})

            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(write, range(40)))

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertIn(payload["writer"], range(40))

    def test_concurrent_pruners_keep_a_valid_bounded_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            root.mkdir()
            for index in range(8):
                _bundle(root, f"run-{index}", started_at=f"2020-01-{index + 1:02d}T00:00:00Z")

            def prune() -> None:
                prune_run_bundles(
                    root,
                    policy=RetentionPolicy(max_bundles=2),
                    logger=logging.getLogger(__name__),
                )

            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(lambda _index: prune(), range(4)))

            self.assertLessEqual(len([path for path in root.iterdir() if path.is_dir()]), 2)
            payload = json.loads((root / ".base-cli-run-index.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertLessEqual(len(payload["bundles"]), 2)


if __name__ == "__main__":
    unittest.main()
