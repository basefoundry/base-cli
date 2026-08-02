from __future__ import annotations

import importlib.util
import json
import logging
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import base_cli
import base_cli.app as app_module
from base_cli._runtime import runtime_layout


def _run(app: base_cli.App, home: Path) -> int:
    with mock.patch.dict(
        os.environ,
        {
            "HOME": str(home),
            "BASE_CLI_CACHE_DIR": str(home / "cache"),
        },
    ):
        return base_cli.run_app(app, [])


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class AppStartupTransactionTests(unittest.TestCase):
    def test_retention_failure_retains_partial_log_and_closes_logger(self) -> None:
        app = base_cli.App(name="startup-retention", max_log_files=1)
        called: list[None] = []

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            called.append(None)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            preserved = home / "cache" / "startup-retention" / "cache" / "existing.txt"
            preserved.parent.mkdir(parents=True)
            preserved.write_text("keep", encoding="utf-8")

            with mock.patch.object(app_module, "prune_log_files", side_effect=RuntimeError("retention unavailable")):
                status = _run(app, home)

            self.assertEqual(status, 1)
            self.assertEqual(called, [])
            self.assertEqual(list((home / "cache").glob("**/run.json")), [])
            partial_logs = list((home / "cache").glob("**/primary.log"))
            self.assertEqual(len(partial_logs), 1)
            self.assertTrue(partial_logs[0].is_file())
            self.assertEqual(logging.getLogger("base_cli.startup-retention").handlers, [])
            self.assertEqual(preserved.read_text(encoding="utf-8"), "keep")
            with self.assertRaisesRegex(RuntimeError, "context is not active"):
                base_cli.get_current_context()

    def test_partial_logger_failure_closes_handlers_and_retains_partial_log(self) -> None:
        app = base_cli.App(name="startup-logger")
        original_configure_logger = app_module.configure_logger

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            self.fail("command should not run")

        def fail_after_logger_setup(*args: object, **kwargs: object) -> logging.Logger:
            original_configure_logger(*args, **kwargs)
            raise OSError("logger unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(app_module, "configure_logger", side_effect=fail_after_logger_setup):
                status = _run(app, home)

            self.assertEqual(status, 1)
            self.assertEqual(list((home / "cache").glob("**/run.json")), [])
            partial_logs = list((home / "cache").glob("**/primary.log"))
            self.assertEqual(len(partial_logs), 1)
            self.assertTrue(partial_logs[0].is_file())
            self.assertEqual(logging.getLogger("base_cli.startup-logger").handlers, [])
            with self.assertRaisesRegex(RuntimeError, "context is not active"):
                base_cli.get_current_context()

    def test_startup_rollback_never_unlinks_log_through_a_swapped_symlink_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            run_id = "fixed-run"
            layout = runtime_layout(cache_root, "startup-log-swap", run_id)
            external_log_dir = root / "external-logs"
            external_log_dir.mkdir()
            victim = external_log_dir / "primary.log"
            victim.write_text("preserve", encoding="utf-8")
            parked_log_dir = layout.run_root / "logs-parked"

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="startup-log-swap",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id=run_id,
                )

            def fail_after_swapping_log_ancestor(*_args: object) -> None:
                layout.log_dir.rename(parked_log_dir)
                layout.log_dir.symlink_to(external_log_dir, target_is_directory=True)
                raise RuntimeError("retention unavailable")

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="startup-log-swap", max_log_files=1, profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                self.fail("command should not run")

            try:
                with mock.patch.object(app_module, "prune_log_files", side_effect=fail_after_swapping_log_ancestor):
                    status = _run(app, home)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            self.assertEqual(status, 1)
            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve")
            self.assertTrue(layout.log_dir.is_symlink())
            self.assertTrue((parked_log_dir / "primary.log").is_file())
            self.assertEqual(logging.getLogger("base_cli.startup-log-swap").handlers, [])

    def test_post_logger_failure_erases_owned_temp_through_retained_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            run_id = "fixed-run"
            layout = runtime_layout(cache_root, "startup-retained-handle", run_id)

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="startup-retained-handle",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id=run_id,
                )

            def fail_with_temp_payload(*_args: object) -> None:
                (layout.temp_dir / "partial-startup.txt").write_text("temporary", encoding="utf-8")
                raise RuntimeError("retention unavailable")

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="startup-retained-handle", max_log_files=1, profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                self.fail("command should not run")

            with mock.patch.object(app_module, "prune_log_files", side_effect=fail_with_temp_payload):
                status = _run(app, home)

            self.assertEqual(status, 1)
            self.assertTrue(layout.temp_dir.is_dir())
            self.assertEqual(list(layout.temp_dir.iterdir()), [])
            self.assertEqual(logging.getLogger("base_cli.startup-retained-handle").handlers, [])

    def test_inherited_startup_failure_never_finalizes_or_deletes_parent_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            parent_run = cache_root / "parent" / "runs" / "parent-run"
            parent_run.mkdir(parents=True)
            parent_metadata = parent_run / "run.json"
            parent_payload = {"run_id": "parent-run", "status": "running"}
            parent_metadata.write_text(json.dumps(parent_payload), encoding="utf-8")

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=runtime_layout(
                        cache_root,
                        "startup-inherited",
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
            app = base_cli.App(name="startup-inherited", max_log_files=1, profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                self.fail("command should not run")

            with mock.patch.object(app_module, "prune_log_files", side_effect=RuntimeError("retention unavailable")):
                status = _run(app, home)

            self.assertEqual(status, 1)
            self.assertEqual(json.loads(parent_metadata.read_text(encoding="utf-8")), parent_payload)
            self.assertTrue(parent_run.is_dir())
            retained_temp = parent_run / "tmp" / "startup-inherited" / "child-run"
            self.assertTrue(retained_temp.is_dir())
            self.assertEqual(list(retained_temp.iterdir()), [])
            self.assertEqual(logging.getLogger("base_cli.startup-inherited").handlers, [])

    def test_startup_rollback_preserves_preexisting_temp_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            layout = runtime_layout(cache_root, "startup-preexisting", "fixed-run")
            layout.temp_dir.mkdir(parents=True)
            marker = layout.temp_dir / "existing.txt"
            marker.write_text("keep", encoding="utf-8")

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="startup-preexisting",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id="fixed-run",
                )

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="startup-preexisting", max_log_files=1, profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                self.fail("command should not run")

            with mock.patch.object(app_module, "prune_log_files", side_effect=RuntimeError("retention unavailable")):
                status = _run(app, home)

            self.assertEqual(status, 1)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(logging.getLogger("base_cli.startup-preexisting").handlers, [])

    def test_startup_rollback_never_recursively_deletes_temp_outside_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            external_temp = root / "profile-selected-external-temp"
            layout = replace(
                runtime_layout(cache_root, "startup-contained", "fixed-run"),
                temp_dir=external_temp,
            )

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="startup-contained",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id="fixed-run",
                )

            marker = external_temp / "created-during-startup.txt"

            def fail_retention(*_args: object) -> None:
                marker.write_text("preserve", encoding="utf-8")
                raise RuntimeError("retention unavailable")

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="startup-contained", max_log_files=1, profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                self.fail("command should not run")

            with mock.patch.object(app_module, "prune_log_files", side_effect=fail_retention):
                status = _run(app, home)

            self.assertEqual(status, 1)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(logging.getLogger("base_cli.startup-contained").handlers, [])

    def test_startup_rollback_never_prunes_through_a_swapped_symlink_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            run_id = "fixed-run"
            layout = runtime_layout(cache_root, "startup-symlink-swap", run_id)
            external = root / "external"
            foreign_cli_dir = external / "startup-symlink-swap"
            foreign_cli_dir.mkdir(parents=True)
            parked_temp_parent = layout.run_root / "tmp-parked"

            def resolve_runtime(_cli_name: str, _project: base_cli.ProjectInfo | None) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="startup-symlink-swap",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id=run_id,
                )

            def fail_after_swapping_temp_ancestor(*_args: object) -> None:
                temp_parent = layout.run_root / "tmp"
                temp_parent.rename(parked_temp_parent)
                temp_parent.symlink_to(external, target_is_directory=True)
                raise RuntimeError("retention unavailable")

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="startup-symlink-swap", max_log_files=1, profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                self.fail("command should not run")

            try:
                with mock.patch.object(app_module, "prune_log_files", side_effect=fail_after_swapping_temp_ancestor):
                    status = _run(app, home)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            self.assertEqual(status, 1)
            self.assertTrue(foreign_cli_dir.is_dir())
            self.assertTrue((layout.run_root / "tmp").is_symlink())
            self.assertTrue((parked_temp_parent / "startup-symlink-swap" / run_id).is_dir())
            self.assertEqual(logging.getLogger("base_cli.startup-symlink-swap").handlers, [])

    def test_rollback_cleanup_runtime_error_cannot_mask_startup_failure(self) -> None:
        app = base_cli.App(name="startup-rollback-runtime", max_log_files=1)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            self.fail("command should not run")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.object(
                app_module,
                "prune_log_files",
                side_effect=RuntimeError("primary startup failure"),
            ), mock.patch.object(
                app_module.Context,
                "_cleanup_owned_temp_dir",
                side_effect=RuntimeError("secondary rollback failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "primary startup failure"):
                    with mock.patch.dict(
                        os.environ,
                        {"HOME": str(home), "BASE_CLI_CACHE_DIR": str(home / "cache")},
                    ):
                        base_cli.run_app(app, [], reraise_unexpected=True)

            self.assertEqual(logging.getLogger("base_cli.startup-rollback-runtime").handlers, [])


if __name__ == "__main__":
    unittest.main()
