from __future__ import annotations

import io
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base_cli
import base_cli._cleanup as cleanup_module
from base_cli._cleanup import (
    UnsafeCleanupPathError,
    _is_root_like,
    _validated_cleanup_paths,
    remove_owned_temp_directory,
)


class CleanupSecurityTests(unittest.TestCase):
    def _context(
        self,
        root: Path,
        temp_dir: Path,
        run_root: Path | None,
        *,
        run_id: str = "run-123",
        owned: bool = True,
    ) -> tuple[base_cli.Context, io.StringIO]:
        stream = io.StringIO()
        logger = logging.Logger(f"cleanup-security-{id(stream)}", level=logging.DEBUG)
        logger.addHandler(logging.StreamHandler(stream))
        context = base_cli.Context(
            cli_name="cleanup-security",
            run_id=run_id,
            state_dir=root / "state",
            log_dir=root / "logs",
            cache_dir=root / "cache",
            temp_dir=temp_dir,
            log_file=None,
            config={},
            environment="test",
            debug=False,
            keep_temp=False,
            log=logger,
            run_root=run_root,
        )
        if owned:
            context._owns_temp_dir = True
            try:
                temp_stat = os.stat(temp_dir, follow_symlinks=False)
                context._owned_temp_identity = (temp_stat.st_dev, temp_stat.st_ino)
                context._owned_temp_descriptor = os.open(
                    temp_dir,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                )
            except OSError:
                pass
        return context, stream

    def test_owned_temp_contents_are_removed_while_empty_leaf_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            (temp_dir / "payload.txt").write_text("temporary", encoding="utf-8")
            (run_root / "logs").mkdir()

            context, _ = self._context(root, temp_dir, run_root)
            owned_descriptor = context._owned_temp_descriptor
            context.cleanup()

            self.assertTrue(temp_dir.is_dir())
            self.assertEqual(list(temp_dir.iterdir()), [])
            self.assertTrue((run_root / "tmp" / "cleanup-security").is_dir())
            self.assertTrue((run_root / "tmp").is_dir())
            self.assertTrue(run_root.exists())
            self.assertTrue((run_root / "logs").exists())
            self.assertEqual(context.log.handlers, [])
            self.assertIsNotNone(owned_descriptor)
            with self.assertRaises(OSError):
                os.fstat(owned_descriptor)

    def test_nonempty_ancestor_and_its_content_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            sibling = run_root / "tmp" / "keep.txt"
            sibling.write_text("persistent", encoding="utf-8")

            context, _ = self._context(root, temp_dir, run_root)
            context.cleanup()

            self.assertTrue(temp_dir.is_dir())
            self.assertEqual(list(temp_dir.iterdir()), [])
            self.assertTrue(sibling.is_file())
            self.assertTrue((run_root / "tmp").is_dir())
            self.assertTrue(run_root.is_dir())

    def test_preexisting_unowned_temp_tree_is_never_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            marker = temp_dir / "preexisting.txt"
            marker.write_text("keep", encoding="utf-8")

            context, stream = self._context(root, temp_dir, run_root, owned=False)
            context.cleanup()

            self.assertTrue(marker.is_file())
            self.assertEqual(stream.getvalue(), "")
            self.assertEqual(context.log.handlers, [])

    def test_keep_temp_preserves_contents_and_closes_retained_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            marker = temp_dir / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            context, _ = self._context(root, temp_dir, run_root)
            context.keep_temp = True
            owned_descriptor = context._owned_temp_descriptor

            context.cleanup()

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(context._owns_temp_dir)
            self.assertIsNone(context._owned_temp_descriptor)
            self.assertIsNotNone(owned_descriptor)
            with self.assertRaises(OSError):
                os.fstat(owned_descriptor)

    def test_traversal_target_is_refused_and_handlers_still_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            run_root.mkdir()
            actual_target = root / "outside" / "run-123"
            actual_target.mkdir(parents=True)
            marker = actual_target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            traversal_target = run_root / ".." / "outside" / "run-123"

            context, stream = self._context(root, traversal_target, run_root)
            context.cleanup()

            self.assertTrue(marker.is_file())
            self.assertIn("path traversal is not allowed", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_outside_target_and_missing_run_id_marker_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            run_root.mkdir()
            cases = (
                (root / "outside" / "run-123", "run-123", "outside"),
                (run_root / "tmp" / "wrong-marker", "run-123", "ownership marker"),
            )
            for target, run_id, expected_warning in cases:
                with self.subTest(target=target):
                    target.mkdir(parents=True)
                    marker = target / "keep.txt"
                    marker.write_text("keep", encoding="utf-8")
                    context, stream = self._context(root, target, run_root, run_id=run_id)

                    context.cleanup()

                    self.assertTrue(marker.is_file())
                    self.assertIn(expected_warning, stream.getvalue())
                    self.assertEqual(context.log.handlers, [])

    def test_symlink_target_is_refused_without_touching_its_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            external = root / "external"
            external.mkdir()
            marker = external / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.parent.mkdir(parents=True)
            try:
                temp_dir.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            context, stream = self._context(root, temp_dir, run_root)
            context.cleanup()

            self.assertTrue(temp_dir.is_symlink())
            self.assertTrue(marker.is_file())
            self.assertIn("symlink cleanup targets are not allowed", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_intermediate_symlink_is_refused_even_when_it_resolves_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            real_parent = run_root / "real-temp"
            actual_target = real_parent / "cleanup-security" / "run-123"
            actual_target.mkdir(parents=True)
            marker = actual_target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            linked_parent = run_root / "tmp"
            try:
                linked_parent.symlink_to(real_parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")
            temp_dir = linked_parent / "cleanup-security" / "run-123"

            context, stream = self._context(root, temp_dir, run_root)
            context.cleanup()

            self.assertTrue(marker.is_file())
            self.assertTrue(linked_parent.is_symlink())
            self.assertIn("symlink cleanup targets are not allowed", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_intermediate_symlink_swap_after_validation_cannot_redirect_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_parent = run_root / "tmp"
            temp_dir = temp_parent / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            owned_marker = temp_dir / "owned.txt"
            owned_marker.write_text("owned", encoding="utf-8")
            outside_parent = root / "outside"
            outside_target = outside_parent / "cleanup-security" / "run-123"
            outside_target.mkdir(parents=True)
            victim = outside_target / "victim.txt"
            victim.write_text("preserve", encoding="utf-8")
            parked_parent = run_root / "tmp-parked"
            owned_stat = temp_dir.stat()
            owned_descriptor = os.open(temp_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

            def swap_intermediate() -> None:
                temp_parent.rename(parked_parent)
                temp_parent.symlink_to(outside_parent, target_is_directory=True)

            try:
                try:
                    with self.assertRaisesRegex(UnsafeCleanupPathError, "symlink cleanup targets"):
                        remove_owned_temp_directory(
                            temp_dir,
                            run_root,
                            "run-123",
                            expected_identity=(owned_stat.st_dev, owned_stat.st_ino),
                            owned_descriptor=owned_descriptor,
                            before_remove=swap_intermediate,
                        )
                finally:
                    os.close(owned_descriptor)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(
                (parked_parent / "cleanup-security" / "run-123" / "owned.txt").read_text(encoding="utf-8"),
                "owned",
            )

    def test_foreign_leaf_replacement_before_removal_is_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            (temp_dir / "owned.txt").write_text("owned", encoding="utf-8")
            owned_stat = temp_dir.stat()
            owned_descriptor = os.open(temp_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parked_owned = run_root / "parked-owned"
            foreign = root / "foreign"
            foreign.mkdir()

            def replace_leaf() -> None:
                temp_dir.rename(parked_owned)
                foreign.rename(temp_dir)

            try:
                with self.assertRaisesRegex(UnsafeCleanupPathError, "changed while it was open"):
                    remove_owned_temp_directory(
                        temp_dir,
                        run_root,
                        "run-123",
                        expected_identity=(owned_stat.st_dev, owned_stat.st_ino),
                        owned_descriptor=owned_descriptor,
                        before_remove=replace_leaf,
                    )
            finally:
                os.close(owned_descriptor)

            self.assertTrue(temp_dir.is_dir())
            self.assertEqual(list(temp_dir.iterdir()), [])
            self.assertEqual((parked_owned / "owned.txt").read_text(encoding="utf-8"), "owned")

    def test_missing_linux_mount_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            marker = temp_dir / "preserve.txt"
            marker.write_text("preserve", encoding="utf-8")
            context, stream = self._context(root, temp_dir, run_root)

            with (
                mock.patch.object(cleanup_module, "_requires_mount_identity", return_value=True),
                mock.patch.object(
                    cleanup_module,
                    "_mount_identity",
                    return_value=None,
                ),
            ):
                context.cleanup()

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertIn("mount identity is unavailable", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_mount_identity_failure_closes_unpublished_child_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            marker = temp_dir / "preserve.txt"
            marker.write_text("preserve", encoding="utf-8")
            context, stream = self._context(root, temp_dir, run_root)
            checked_descriptors: list[int] = []

            def fail_mount_check(
                descriptor: int,
                _value: os.stat_result,
                _root_device: int,
                _root_mount: str | None,
            ) -> bool:
                checked_descriptors.append(descriptor)
                raise UnsafeCleanupPathError("mount identity inspection failed")

            with mock.patch.object(cleanup_module, "_crosses_mount", side_effect=fail_mount_check):
                context.cleanup()

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertIn("mount identity inspection failed", stream.getvalue())
            self.assertEqual(len(checked_descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(checked_descriptors[0])

    def test_nested_directory_skeleton_is_retained_without_any_rmdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            nested = temp_dir / "nested" / "deep"
            nested.mkdir(parents=True)
            payload = nested / "payload.txt"
            payload.write_text("temporary", encoding="utf-8")
            foreign = root / "foreign"
            foreign.mkdir()
            parked_nested = root / "parked-nested"
            context, stream = self._context(root, temp_dir, run_root)
            original_rmdir = cleanup_module.os.rmdir

            def destructive_rmdir(name: str, *, dir_fd: int) -> None:
                del name, dir_fd
                (temp_dir / "nested").rename(parked_nested)
                foreign.rename(temp_dir / "nested")
                original_rmdir(temp_dir / "nested")

            with mock.patch.object(cleanup_module.os, "rmdir", side_effect=destructive_rmdir) as rmdir_mock:
                context.cleanup()

            rmdir_mock.assert_not_called()
            self.assertFalse(payload.exists())
            self.assertTrue(nested.is_dir())
            self.assertEqual(list(nested.iterdir()), [])
            self.assertTrue(foreign.is_dir())
            self.assertFalse(parked_nested.exists())
            self.assertEqual(stream.getvalue(), "")

    def test_plain_directory_replacement_never_inherits_cleanup_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            owned_marker = temp_dir / "owned.txt"
            owned_marker.write_text("owned", encoding="utf-8")
            context, stream = self._context(root, temp_dir, run_root)

            parked_owned = run_root / "parked-owned"
            temp_dir.rename(parked_owned)
            replacement = root / "replacement"
            replacement.mkdir()
            victim = replacement / "do-not-delete.txt"
            victim.write_text("preserve", encoding="utf-8")
            replacement.rename(temp_dir)

            context.cleanup()

            self.assertEqual((temp_dir / "do-not-delete.txt").read_text(encoding="utf-8"), "preserve")
            self.assertEqual((parked_owned / "owned.txt").read_text(encoding="utf-8"), "owned")
            self.assertIn("no longer matches the invocation-owned directory", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_target_equal_to_run_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run-123"
            run_root.mkdir()
            marker = run_root / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            context, stream = self._context(root, run_root, run_root)
            context.cleanup()

            self.assertTrue(marker.is_file())
            self.assertIn("equals the run root", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_filesystem_root_and_cross_platform_root_like_paths_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "run-123"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            filesystem_root = Path(target.anchor)

            context, stream = self._context(root, target, filesystem_root)
            context.cleanup()

            self.assertTrue(marker.is_file())
            self.assertIn("filesystem-root cleanup targets", stream.getvalue())
            self.assertTrue(_is_root_like(Path("/")))
            self.assertTrue(_is_root_like(Path("C:\\")))
            self.assertTrue(_is_root_like(Path("\\\\server\\share\\")))
            self.assertEqual(context.log.handlers, [])

    def test_mount_target_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            marker = temp_dir / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            original_is_mount = cleanup_module.os.path.ismount

            def is_mount(path: Path) -> bool:
                return path == temp_dir or original_is_mount(path)

            context, stream = self._context(root, temp_dir, run_root)
            with mock.patch.object(cleanup_module.os.path, "ismount", side_effect=is_mount):
                context.cleanup()

            self.assertTrue(marker.is_file())
            self.assertIn("mounted temp directories", stream.getvalue())
            self.assertEqual(context.log.handlers, [])

    def test_validation_rejects_traversal_outside_root_and_invalid_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "run"
            cases = (
                (root / "tmp" / ".." / "run-123", root, "run-123", "path traversal"),
                (Path(tmpdir) / "other" / "run-123", root, "run-123", "outside the run root"),
                (root / "tmp" / "run-123", root, "../run-123", "ownership marker"),
            )
            for target, run_root, run_id, message in cases:
                with self.subTest(target=target, run_id=run_id):
                    with self.assertRaisesRegex(UnsafeCleanupPathError, message):
                        _validated_cleanup_paths(target, run_root, run_id, (1, 2))

    def test_validation_rejects_resolved_path_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "run"
            target = root / "tmp" / "cleanup-security" / "run-123"
            target.mkdir(parents=True)
            with mock.patch.object(
                cleanup_module,
                "_strict_relative_path",
                side_effect=(Path("tmp/cleanup-security/run-123"), Path("elsewhere/run-123")),
            ):
                with self.assertRaisesRegex(UnsafeCleanupPathError, "does not match"):
                    _validated_cleanup_paths(target, root, "run-123", (target.stat().st_dev, target.stat().st_ino))

    def test_platform_without_safe_directory_handles_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "run"
            temp_dir = run_root / "tmp" / "cleanup-security" / "run-123"
            temp_dir.mkdir(parents=True)
            marker = temp_dir / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            context, stream = self._context(root, temp_dir, run_root)

            with mock.patch.object(cleanup_module, "_supports_fd_relative_cleanup", return_value=False):
                context.cleanup()

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertIn("directory-handle operations", stream.getvalue())
            self.assertEqual(context.log.handlers, [])


if __name__ == "__main__":
    unittest.main()
