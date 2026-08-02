from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from unittest import mock

import base_cli
import base_cli.app as app_module
import base_cli._runtime as runtime_module
from base_cli._runtime import RuntimeDirectoryError, runtime_layout
from base_cli.redaction import REDACTED
from base_cli.testing import invoke


class RuntimeOwnershipBoundaryTests(unittest.TestCase):
    def test_created_leaf_swap_is_detected_and_retained_handle_is_closed(self) -> None:
        if not runtime_module._supports_secure_owned_directory_creation():
            self.skipTest("secure directory-relative creation is unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cache_root = root / "cache"
            temp_dir = cache_root / "run" / "tmp" / "cli" / "run-123"
            replacement = root / "foreign"
            replacement.mkdir()
            marker = replacement / "preserve.txt"
            marker.write_text("preserve", encoding="utf-8")
            parked_owned = root / "parked-owned"
            opened_leaf_descriptors: list[int] = []
            original_fstat = runtime_module.os.fstat
            original_require = runtime_module._require_current_owned_entry

            def capture_fstat(descriptor: int) -> os.stat_result:
                opened_leaf_descriptors.append(descriptor)
                return original_fstat(descriptor)

            swapped = False

            def replace_before_binding(
                parent_fd: int,
                name: str,
                created_stat: os.stat_result,
                path: Path,
            ) -> None:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    temp_dir.rename(parked_owned)
                    replacement.rename(temp_dir)
                original_require(parent_fd, name, created_stat, path)

            with mock.patch.object(runtime_module.os, "fstat", side_effect=capture_fstat), mock.patch.object(
                runtime_module,
                "_require_current_owned_entry",
                side_effect=replace_before_binding,
            ):
                with self.assertRaisesRegex(RuntimeDirectoryError, "changed during creation"):
                    runtime_module.create_owned_runtime_directory(temp_dir, cache_root)

            self.assertEqual((temp_dir / marker.name).read_text(encoding="utf-8"), "preserve")
            self.assertTrue(parked_owned.is_dir())
            self.assertEqual(len(opened_leaf_descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(opened_leaf_descriptors[0])


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class AppRedactionBoundaryTests(unittest.TestCase):
    def test_redaction_plan_is_published_before_concurrent_invocation(self) -> None:
        compile_started = Event()
        release_compile = Event()
        invocation_started = Event()
        captured: list[list[str]] = []
        received: list[str] = []
        failures: list[BaseException] = []
        statuses: list[int] = []
        secret = "fjord-ember-9274"

        def history_writer(
            _ctx: base_cli.Context,
            argv: list[str],
            _sensitive: set[str],
            _started_at: datetime,
            _exit_code: int,
        ) -> None:
            captured.append(list(argv))

        with tempfile.TemporaryDirectory() as tmpdir:
            profile = replace(
                base_cli.CliProfile.generic(cache_root=Path(tmpdir)),
                history_writer=history_writer,
            )
            app = base_cli.App(name="publication-race", profile=profile)

            @app.command()
            @base_cli.option("--credential", sensitive=True, required=True)
            def main(ctx: base_cli.Context, credential: str) -> None:
                del ctx
                received.append(credential)

            original_compile = app_module.compile_redaction_plan

            def blocking_compile(command: object):
                compile_started.set()
                if not release_compile.wait(5):
                    raise AssertionError("redaction-plan compilation was not released")
                return original_compile(command)

            def build_command() -> None:
                try:
                    _ = app.click_command
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    failures.append(exc)

            def invoke_command() -> None:
                invocation_started.set()
                try:
                    statuses.append(
                        app_module.run_app(
                            app,
                            ["--credential", secret],
                            reraise_unexpected=True,
                        )
                    )
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    failures.append(exc)

            builder = Thread(target=build_command)
            invoker = Thread(target=invoke_command)
            with mock.patch.object(
                app_module,
                "compile_redaction_plan",
                side_effect=blocking_compile,
            ):
                builder.start()
                try:
                    self.assertTrue(compile_started.wait(5))
                    self.assertIsNone(app._click_command)
                    self.assertIsNone(app._redaction_plan)
                    invoker.start()
                    self.assertTrue(invocation_started.wait(5))
                finally:
                    release_compile.set()
                    builder.join(5)
                    if invoker.ident is not None:
                        invoker.join(5)

            self.assertFalse(builder.is_alive())
            self.assertFalse(invoker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(statuses, [0])
            self.assertEqual(received, [secret])
            self.assertEqual(
                captured,
                [["publication-race", "--credential", REDACTED]],
            )
            self.assertNotIn(secret, repr(captured))

    def test_actual_click_schema_redacts_every_value_before_history_callback(self) -> None:
        captured: list[tuple[list[str], set[str], Path]] = []

        def history_writer(
            ctx: base_cli.Context,
            argv: list[str],
            sensitive: set[str],
            _started_at: datetime,
            _exit_code: int,
        ) -> None:
            assert ctx.log_file is not None
            captured.append((list(argv), set(sensitive), ctx.log_file))

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="redaction-boundary", profile=profile)
        received: list[tuple[str, str, bool]] = []

        @app.command()
        @base_cli.option("-t", "--token", "--auth-token", "credential", sensitive=True, required=True)
        @base_cli.option("-v", "--verbose", is_flag=True)
        @base_cli.argument("payload", sensitive=True)
        def main(ctx: base_cli.Context, credential: str, verbose: bool, payload: str) -> None:
            del ctx
            received.append((credential, payload, verbose))

        cases = (
            (["--token", "long-secret", "payload-one"], ("long-secret", "payload-one", False)),
            (["--auth-token=equals-secret", "payload-two"], ("equals-secret", "payload-two", False)),
            (["-vtattached-secret", "payload-three"], ("attached-secret", "payload-three", True)),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index, (args, expected) in enumerate(cases):
                with self.subTest(args=args):
                    home = root / str(index)
                    home.mkdir()
                    result = invoke(app, args, home=home)
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(received[-1], expected)

            self.assertEqual(len(captured), len(cases))
            all_secrets = {value for _, expected in cases for value in expected[:2]}
            for safe_argv, sensitive, log_file in captured:
                rendered = repr(safe_argv)
                log_text = log_file.read_text(encoding="utf-8")
                self.assertIn(REDACTED, rendered)
                self.assertIn(REDACTED, log_text)
                for secret in all_secrets:
                    self.assertNotIn(secret, rendered)
                    self.assertNotIn(secret, log_text)
                self.assertTrue(
                    {"credential", "-t", "--token", "--auth-token", "payload"}.issubset(sensitive)
                )

    def test_secret_name_heuristics_cover_options_and_arguments(self) -> None:
        captured: list[list[str]] = []

        def history_writer(
            _ctx: base_cli.Context,
            argv: list[str],
            _sensitive: set[str],
            _started_at: datetime,
            _exit_code: int,
        ) -> None:
            captured.append(list(argv))

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="automatic-redaction", profile=profile)

        @app.command()
        @base_cli.option("--api-key", required=True)
        @base_cli.argument("password")
        def main(ctx: base_cli.Context, api_key: str, password: str) -> None:
            del ctx, api_key, password

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, ["--api-key", "option-secret", "argument-secret"], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            captured,
            [["automatic-redaction", "--api-key", REDACTED, REDACTED]],
        )

    def test_click_token_normalization_cannot_bypass_redaction(self) -> None:
        captured: list[list[str]] = []
        normalized_inputs: list[str] = []

        def normalize(value: str) -> str:
            normalized_inputs.append(value)
            if "=" in value:
                raise AssertionError("normalizer received attached value bytes")
            return value.lower()

        def history_writer(
            _ctx: base_cli.Context,
            argv: list[str],
            _sensitive: set[str],
            _started_at: datetime,
            _exit_code: int,
        ) -> None:
            captured.append(list(argv))

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="normalized-redaction", profile=profile)

        @app.command(context_settings={"token_normalize_func": normalize})
        @base_cli.option("--token", sensitive=True, required=True)
        def main(ctx: base_cli.Context, token: str) -> None:
            del ctx, token

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, ["--TOKEN=normalized-secret"], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(captured, [["normalized-redaction", f"--TOKEN={REDACTED}"]])
        self.assertTrue(normalized_inputs)
        self.assertFalse(any("normalized-secret" in value for value in normalized_inputs))

    def test_short_prefixes_and_mixed_unknown_clusters_are_redacted(self) -> None:
        captured: list[list[str]] = []

        def history_writer(
            _ctx: base_cli.Context,
            argv: list[str],
            _sensitive: set[str],
            _started_at: datetime,
            _exit_code: int,
        ) -> None:
            captured.append(list(argv))

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)
        app = base_cli.App(name="short-redaction", profile=profile)
        received: list[str] = []

        @app.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
        @base_cli.option("-t", "+t", "/t", "credential", sensitive=True, required=True)
        @base_cli.option("-v", is_flag=True)
        def main(ctx: base_cli.Context, credential: str, v: bool) -> None:
            del ctx, v
            received.append(credential)

        cases = (
            (["-xtcluster-one"], "cluster-one"),
            (["-xvtcluster-two"], "cluster-two"),
            (["-xt", "cluster-three"], "cluster-three"),
            (["-xvt=cluster-four"], "=cluster-four"),
            (["+tplus-secret"], "plus-secret"),
            (["+vt=plus-equals"], "=plus-equals"),
            (["/tslash-secret"], "slash-secret"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index, (args, expected) in enumerate(cases):
                home = root / str(index)
                home.mkdir()
                result = invoke(app, args, home=home)
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(received[-1], expected)

        self.assertEqual(len(captured), len(cases))
        for safe_argv in captured:
            self.assertIn(REDACTED, repr(safe_argv))
            for _, secret in cases:
                self.assertNotIn(secret, repr(safe_argv))


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class AppCleanupBoundaryTests(unittest.TestCase):
    def test_concurrent_temp_leaf_creation_is_never_claimed_or_deleted(self) -> None:
        app = base_cli.App(name="cleanup-claim-race")

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            self.fail("command should not run after an ownership race")

        original_create = app_module.create_owned_runtime_directory
        raced_marker: list[Path] = []

        def race_create(path: Path, cache_root: Path) -> None:
            path.mkdir(parents=True)
            marker = path / "foreign.txt"
            marker.write_text("preserve", encoding="utf-8")
            raced_marker.append(marker)
            original_create(path, cache_root)

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(
            app_module,
            "create_owned_runtime_directory",
            side_effect=race_create,
        ):
            result = invoke(app, [], home=Path(tmpdir))

            self.assertEqual(result.exit_code, 1, result.output)
            self.assertEqual(len(raced_marker), 1)
            self.assertEqual(raced_marker[0].read_text(encoding="utf-8"), "preserve")
            self.assertIn("appeared concurrently", result.stderr)

    def test_successful_invocation_refuses_external_profile_temp_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            run_id = "fixed-run"
            external_temp = root / "external" / run_id
            layout = replace(
                runtime_layout(cache_root, "cleanup-boundary", run_id),
                temp_dir=external_temp,
            )

            def resolve_runtime(
                _cli_name: str,
                _project: base_cli.ProjectInfo | None,
            ) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="cleanup-boundary",
                    project_root=None,
                    project_name=None,
                    inherited_path=None,
                    history_parent_run_id=None,
                    run_id=run_id,
                )

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="cleanup-boundary", profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                (ctx.temp_dir / "keep.txt").write_text("preserve", encoding="utf-8")

            result = invoke(app, [], home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual((external_temp / "keep.txt").read_text(encoding="utf-8"), "preserve")
            self.assertIn("outside the run root", result.stderr)

    def test_successful_inherited_invocation_preserves_parent_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache_root = home / "cache"
            parent_run = cache_root / "parent" / "runs" / "parent-run"
            parent_run.mkdir(parents=True)
            parent_marker = parent_run / "run.json"
            parent_marker.write_text('{"status":"running"}', encoding="utf-8")
            run_id = "child-run"
            layout = runtime_layout(
                cache_root,
                "cleanup-child",
                run_id,
                namespace="parent",
                inherited_run_root=parent_run,
            )

            def resolve_runtime(
                _cli_name: str,
                _project: base_cli.ProjectInfo | None,
            ) -> base_cli.RuntimeBinding:
                return base_cli.RuntimeBinding(
                    cache_root=cache_root,
                    layout=layout,
                    application_home=None,
                    runtime_owner="parent",
                    project_root=None,
                    project_name=None,
                    inherited_path=parent_run,
                    history_parent_run_id="parent-run",
                    run_id=run_id,
                )

            profile = replace(base_cli.CliProfile.generic(), resolve_runtime=resolve_runtime)
            app = base_cli.App(name="cleanup-child", profile=profile)
            seen_temp: list[Path] = []

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen_temp.append(ctx.temp_dir)
                (ctx.temp_dir / "temporary.txt").write_text("temporary", encoding="utf-8")

            result = invoke(app, [], home=home)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(parent_marker.read_text(encoding="utf-8"), '{"status":"running"}')
            self.assertTrue(parent_run.is_dir())
            self.assertTrue(seen_temp[0].is_dir())
            self.assertEqual(list(seen_temp[0].iterdir()), [])


if __name__ == "__main__":
    unittest.main()
