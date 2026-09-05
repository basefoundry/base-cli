from __future__ import annotations

import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import base_cli._click_compat as click_compat
import base_cli._private_files as private_files
import base_cli._runtime as runtime
from base_cli import RetentionPolicy
from base_cli import history
from base_cli._attach import (
    _click_command_has_pending_children,
    _normalize_attached_option_declaration,
    _normalize_sensitive_parameters,
    _restore_attached_click_command,
    _restore_attached_click_main,
    _selected_click_path,
    _selected_click_paths,
)


class PrivateFileEdgeTests(unittest.TestCase):
    def test_permission_helpers_skip_posix_mode_changes_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "file"
            path.write_text("payload", encoding="utf-8")
            with mock.patch.object(private_files.os, "name", "nt"):
                private_files.restrict_file(path)
                private_files.restrict_directory(path.parent)

    def test_sync_directory_tolerates_filesystems_without_directory_fsync(self) -> None:
        with mock.patch.object(private_files.os, "fsync", side_effect=OSError("unsupported")):
            private_files._sync_directory(123)  # pylint: disable=protected-access

    def test_windows_replace_retries_transient_sharing_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source"
            destination = Path(tmpdir) / "destination"
            source.write_text("payload", encoding="utf-8")
            transient = PermissionError("busy")
            transient.winerror = 32
            with (
                mock.patch.object(private_files.os, "name", "nt"),
                mock.patch.object(
                    private_files.os,
                    "replace",
                    side_effect=[transient, lambda src, dst: Path(dst).write_text(Path(src).read_text())],
                ) as replace,
                mock.patch.object(private_files.time, "sleep") as sleep,
            ):
                private_files._replace_with_retry(source, destination)  # pylint: disable=protected-access
            self.assertEqual(replace.call_count, 2)
            sleep.assert_called_once()

    def test_windows_replace_fails_immediately_for_access_denied(self) -> None:
        source = Path("source")
        destination = Path("destination")
        denied = PermissionError("access denied")
        denied.winerror = 5
        with (
            mock.patch.object(private_files.os, "name", "nt"),
            mock.patch.object(private_files.os, "replace", side_effect=denied),
            mock.patch.object(private_files.time, "sleep") as sleep,
        ):
            with self.assertRaises(PermissionError) as raised:
                private_files._replace_with_retry(source, destination)  # pylint: disable=protected-access
        self.assertIs(raised.exception, denied)
        sleep.assert_not_called()

    def test_windows_replace_retries_compatibility_access_denied_for_owned_temp(self) -> None:
        source = Path(".destination.owned.tmp")
        destination = Path("destination")
        transient = PermissionError("destination in use")
        transient.winerror = 5
        with (
            mock.patch.object(private_files.os, "name", "nt"),
            mock.patch.object(private_files.os, "replace", side_effect=[transient, None]) as replace,
            mock.patch.object(private_files.time, "sleep") as sleep,
        ):
            private_files._replace_with_retry(source, destination)  # pylint: disable=protected-access
        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once()

    def test_windows_replace_respects_elapsed_retry_deadline(self) -> None:
        source = Path("source")
        destination = Path("destination")
        transient = PermissionError("busy")
        transient.winerror = 33
        clock = iter((0.0, 0.1, 1.0))
        with (
            mock.patch.object(private_files.os, "name", "nt"),
            mock.patch.object(private_files.os, "replace", side_effect=transient),
            mock.patch.object(private_files.time, "monotonic", side_effect=lambda: next(clock)),
            mock.patch.object(private_files.time, "sleep") as sleep,
        ):
            with self.assertRaises(PermissionError) as raised:
                private_files._replace_with_retry(source, destination)  # pylint: disable=protected-access
        self.assertIs(raised.exception, transient)
        self.assertEqual(sleep.call_count, 1)

    def test_parent_directory_open_is_disabled_on_windows(self) -> None:
        path = Path("/tmp")
        with mock.patch.object(private_files.os, "name", "nt"):
            self.assertIsNone(private_files._open_parent_directory(path))  # pylint: disable=protected-access


class ClickCompatibilityEdgeTests(unittest.TestCase):
    def test_exit_exception_type_supports_core_fallback(self) -> None:
        fallback = types.SimpleNamespace(
            exceptions=types.SimpleNamespace(),
            core=types.SimpleNamespace(Exit=RuntimeError),
        )
        self.assertIs(click_compat.exit_exception_type(fallback), RuntimeError)

    def test_dialect_falls_back_to_public_click_without_typer(self) -> None:
        with mock.patch.dict("sys.modules", {"typer": None}):
            command = object()
            self.assertIs(click_compat.dialect_for_command(command), __import__("click"))
            self.assertFalse(click_compat.is_command(command))

    def test_vendored_dialect_requires_a_click_module(self) -> None:
        self.assertIsNone(click_compat._vendored_typer_dialect(types.SimpleNamespace()))  # pylint: disable=protected-access
        self.assertIs(click_compat.dialect_for_typer(types.SimpleNamespace()), __import__("click"))

    def test_marking_an_immutable_command_is_best_effort(self) -> None:
        class Immutable:
            __slots__ = ()

        command = Immutable()
        self.assertIs(click_compat.mark_command_dialect(command, object()), command)

    def test_vendor_version_option_requires_a_version(self) -> None:
        decorator = click_compat._vendor_version_option_factory(lambda **_: None, lambda *_args, **_kwargs: None)  # pylint: disable=protected-access
        with self.assertRaisesRegex(RuntimeError, "version is required"):
            decorator()


class RuntimeEdgeTests(unittest.TestCase):
    def test_retention_scan_and_apply_are_portable_without_recursive_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            root.mkdir()
            for index in range(3):
                bundle = root / f"run-{index}"
                bundle.mkdir()
                (bundle / "run.json").write_text(
                    '{"run_id": "run-%d", "status": "ok", '
                    '"started_at": "2020-01-01T00:00:00Z", "preserve": false}' % index,
                    encoding="utf-8",
                )
            with mock.patch.object(runtime, "_bundle_size", side_effect=AssertionError("unexpected size walk")):
                bundles = runtime._discover_run_bundles(  # pylint: disable=protected-access
                    root,
                    protected=set(),
                    max_age_seconds=None,
                    now=1_600_000_000,
                    measure_sizes=False,
                    size_budget=0,
                )
            self.assertTrue(all(not bundle["size_known"] for bundle in bundles))
            with mock.patch.object(runtime, "_remove_run_bundle") as remove:
                runtime._apply_bundle_retention(  # pylint: disable=protected-access
                    root,
                    bundles,
                    policy=RetentionPolicy(max_bundles=1),
                    protected=set(),
                    logger=mock.Mock(),
                    now=1_600_000_000,
                    reserved_active_bundles=0,
                )
            self.assertEqual(remove.call_count, 2)

    def test_owned_runtime_directory_collision_is_not_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runs" / "run"
            path.mkdir(parents=True)
            with self.assertRaises(runtime.RuntimeDirectoryError):
                runtime.create_owned_runtime_directory(path, Path(tmpdir))

    def test_owned_runtime_directory_uses_portable_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runs" / "portable"
            with mock.patch.object(runtime, "_supports_secure_owned_directory_creation", return_value=False):
                identity, descriptor = runtime.create_owned_runtime_directory(path, Path(tmpdir))
            self.assertIsInstance(identity, tuple)
            self.assertIsNone(descriptor)
            self.assertTrue(path.is_dir())

    def test_malformed_metadata_and_timestamps_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = Path(tmpdir) / "bundle"
            bundle.mkdir()
            (bundle / "run.json").write_text("[]", encoding="utf-8")
            self.assertIsNone(runtime._read_bundle_metadata(bundle))  # pylint: disable=protected-access
        self.assertIsNone(runtime._timestamp_to_epoch("not-a-timestamp"))  # pylint: disable=protected-access
        self.assertIsNone(runtime._timestamp_to_epoch(123))  # pylint: disable=protected-access

    def test_retention_without_policy_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = mock.Mock()
            runtime.prune_run_bundles(Path(tmpdir), logger=logger)
            logger.warning.assert_not_called()

    def test_runtime_directory_failures_are_actionable(self) -> None:
        path = Path("/tmp/base-cli-edge/runtime")
        with mock.patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            with self.assertRaisesRegex(runtime.RuntimeDirectoryError, "Check permissions"):
                runtime.create_runtime_directory(path, Path("/tmp/base-cli-edge"))

    def test_safe_resolved_path_falls_back_when_resolution_fails(self) -> None:
        path = Path("relative/path")
        with mock.patch.object(Path, "resolve", side_effect=OSError("unavailable")):
            self.assertEqual(runtime._safe_resolved_path(path), path.absolute())  # pylint: disable=protected-access


class AttachmentHelperEdgeTests(unittest.TestCase):
    def test_attachment_normalizers_accept_strings_and_reject_bad_values(self) -> None:
        self.assertEqual(_normalize_sensitive_parameters("token"), frozenset({"token"}))
        with self.assertRaises(TypeError):
            _normalize_sensitive_parameters([""])
        self.assertEqual(_normalize_attached_option_declaration("--NAME", str.lower), "--name")
        self.assertEqual(_normalize_attached_option_declaration("NAME", str.lower), "NAME")

    def test_selected_paths_follow_resolved_click_children(self) -> None:
        root_command = object()
        child_command = object()
        root = types.SimpleNamespace(command=root_command, parent=None, info_name="root")
        child = types.SimpleNamespace(command=child_command, parent=root, info_name="child")
        resolutions = {id(root): [("child", child_command, child)]}
        self.assertEqual(_selected_click_path(root, child, resolutions), (("child", child_command),))
        self.assertEqual(_selected_click_paths(root, resolutions, {id(root): root}), ((("child", child_command),),))

    def test_pending_children_and_restore_helpers_cover_fallbacks(self) -> None:
        command = types.SimpleNamespace(resolve_command=lambda *_args: None)
        context = types.SimpleNamespace(_protected_args=("child",), args=())
        self.assertTrue(_click_command_has_pending_children(context, command))
        self.assertFalse(_click_command_has_pending_children(types.SimpleNamespace(args=()), object()))

        def original_invoke(_ctx: object) -> None:
            return None

        def original_resolve(_ctx: object, _args: object) -> None:
            return None

        command.invoke = lambda _ctx: "wrapped"
        command.resolve_command = lambda _ctx, _args: "wrapped"
        command.__base_cli_original_invoke__ = original_invoke
        command.__base_cli_original_resolve__ = original_resolve
        _restore_attached_click_command(command)
        self.assertIs(command.invoke, original_invoke)
        self.assertIs(command.resolve_command, original_resolve)

        command.main = lambda: "wrapped"
        command.__base_cli_original_main__ = original_invoke
        _restore_attached_click_main(command)
        self.assertIs(command.main, original_invoke)


class HistoryEdgeTests(unittest.TestCase):
    def test_primary_record_and_parser_cover_optional_and_invalid_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "history.jsonl"
            history.write_primary_record(
                path,
                "demo --token=secret",
                ["demo", "--token=secret"],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                1,
                "run-1",
                project="demo",
                project_root=str(root),
                manifest=str(root / "manifest.yaml"),
                log_path=root / "primary.log",
                bundle_path=root / "bundle",
            )
            line = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertIsNotNone(history.parse_finished_history_record_line(line))
        self.assertIsNone(history.parse_finished_history_record_line("not json"))
        self.assertIsNone(history.parse_finished_history_record_line("{}"))
        self.assertEqual(history.optional_string("value"), "value")
        self.assertIsNone(history.optional_string(1))
        self.assertEqual(history.optional_int(2), 2)
        self.assertIsNone(history.optional_int("2"))


if __name__ == "__main__":
    unittest.main()
