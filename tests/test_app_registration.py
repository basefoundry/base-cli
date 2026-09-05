from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import base_cli
from base_cli.testing import invoke


def _all_output(result: Any) -> str:
    output = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    if stderr and stderr not in output:
        return f"{output}{stderr}"
    return output


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class AppRegistrationTests(unittest.TestCase):
    def test_single_command_uses_app_identity_for_click_help_version_and_usage(self) -> None:
        app = base_cli.App(
            name="professional-tool",
            version="1.2.3",
            help="Operate the professional tool.",
            log_to_file=False,
        )

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        click_command = app.click_command
        self.assertEqual(click_command.name, "professional-tool")
        self.assertEqual(click_command.help, "Operate the professional tool.")

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            help_result = invoke(app, ["--help"], home=home)
            version_result = invoke(app, ["--version"], home=home)
            usage_result = invoke(app, ["--unknown"], home=home)

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertIn("Usage: professional-tool [OPTIONS]", help_result.output)
        self.assertIn("Operate the professional tool.", help_result.output)
        self.assertNotIn("Usage: implementation", help_result.output)

        self.assertEqual(version_result.exit_code, 0, version_result.output)
        self.assertIn("professional-tool, version 1.2.3", version_result.output)

        self.assertEqual(usage_result.exit_code, 2, usage_result.output)
        self.assertIn("Usage: professional-tool [OPTIONS]", _all_output(usage_result))
        self.assertNotIn("Usage: implementation", _all_output(usage_result))

    def test_explicit_dotted_identity_remains_lossless(self) -> None:
        app = base_cli.App(name="ops.prod", log_to_file=False)

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            self.assertEqual(ctx.cli_name, "ops.prod")

        self.assertEqual(app.name, "ops.prod")
        self.assertEqual(app.click_command.name, "ops.prod")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))
        self.assertEqual(result.exit_code, 0, _all_output(result))

    def test_single_command_rejects_a_name_conflicting_with_app_identity(self) -> None:
        app = base_cli.App(name="authoritative-name")

        with self.assertRaisesRegex((RuntimeError, ValueError), "conflicting-name|authoritative-name"):

            @app.command("conflicting-name")
            def implementation(ctx: base_cli.Context) -> None:
                del ctx

    def test_single_command_accepts_an_explicit_name_matching_app_identity(self) -> None:
        app = base_cli.App(name="matching-name")

        @app.command("matching-name")
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        self.assertEqual(app.click_command.name, "matching-name")

    def test_direct_app_invocation_defaults_to_app_identity(self) -> None:
        app = base_cli.App(name="direct-identity")

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        click_command = app.click_command
        with mock.patch.object(click_command, "main", return_value=0) as click_main:
            result = app(["--help"])

        self.assertEqual(result, 0)
        click_main.assert_called_once_with(["--help"], prog_name="direct-identity")

    def test_direct_app_invocation_honors_delegated_display_identity(self) -> None:
        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=lambda: "launcher delegated",
        )
        app = base_cli.App(name="internal-identity", profile=profile)

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        click_command = app.click_command
        with mock.patch.object(click_command, "main", return_value=0) as click_main:
            result = app([])

        self.assertEqual(result, 0)
        click_main.assert_called_once_with([], prog_name="launcher delegated")

    def test_custom_single_command_class_cannot_override_app_identity(self) -> None:
        import click

        class RenamingCommand(click.Command):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.name = "custom-override"

        app = base_cli.App(name="canonical-single")

        @app.command(cls=RenamingCommand)
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "canonical-single|custom-override"):
            _ = app.click_command

    def test_app_name_can_change_before_materialization_and_becomes_canonical(self) -> None:
        app = base_cli.App(name="original-name", log_to_file=False)

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        app.name = "renamed tool"

        self.assertEqual(app.name, "renamed tool")
        self.assertEqual(app.click_command.name, "renamed tool")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, ["--help"], home=Path(tmpdir))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Usage: renamed tool [OPTIONS]", result.output)
        self.assertNotIn("Usage: original-name", result.output)

    def test_app_name_rejects_a_rename_conflicting_with_registered_explicit_name(self) -> None:
        app = base_cli.App(name="original-name")

        @app.command("original-name")
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "original-name|renamed-name"):
            app.name = "renamed-name"

        self.assertEqual(app.name, "original-name")
        self.assertEqual(app.click_command.name, "original-name")

    def test_app_name_cannot_change_during_or_after_materialization(self) -> None:
        mutation_errors: list[BaseException] = []

        class RenamingApp(base_cli.App):
            def _build_click_command(self) -> object:
                try:
                    self.name = "during-build"
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    mutation_errors.append(exc)
                return super()._build_click_command()

        app = RenamingApp(name="stable-name")

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        click_command = app.click_command

        self.assertEqual(len(mutation_errors), 1)
        self.assertIsInstance(mutation_errors[0], RuntimeError)
        self.assertRegex(str(mutation_errors[0]), "materializ|frozen")
        self.assertEqual(app.name, "stable-name")
        self.assertEqual(click_command.name, "stable-name")

        with self.assertRaisesRegex(RuntimeError, "materializ|frozen"):
            app.name = "after-build"
        self.assertEqual(app.name, "stable-name")
        self.assertIs(app.click_command, click_command)

    def test_group_uses_app_identity_for_click_help_and_version(self) -> None:
        app = base_cli.App(
            name="workspace-suite",
            version="2.4.0",
            help="Manage the workspace suite.",
            log_to_file=False,
        )

        @app.subcommand()
        def status(ctx: base_cli.Context) -> None:
            del ctx

        click_group = app.click_command
        self.assertEqual(click_group.name, "workspace-suite")
        self.assertEqual(click_group.help, "Manage the workspace suite.")
        self.assertEqual(set(click_group.commands), {"status"})

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            help_result = invoke(app, ["--help"], home=home)
            version_result = invoke(app, ["--version"], home=home)

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertIn(
            "Usage: workspace-suite [OPTIONS] COMMAND [ARGS]...",
            help_result.output,
        )
        self.assertIn("Manage the workspace suite.", help_result.output)
        self.assertIn("status", help_result.output)
        self.assertEqual(version_result.exit_code, 0, version_result.output)
        self.assertIn("workspace-suite, version 2.4.0", version_result.output)

    def test_inferred_subcommand_suffixes_are_click_version_independent(self) -> None:
        app = base_cli.App(name="stable-names")

        @app.subcommand()
        def sync_command(ctx: base_cli.Context) -> None:
            del ctx

        @app.subcommand()
        def inspect_cmd(ctx: base_cli.Context) -> None:
            del ctx

        @app.subcommand()
        def report_group(ctx: base_cli.Context) -> None:
            del ctx

        @app.subcommand()
        def clean_grp(ctx: base_cli.Context) -> None:
            del ctx

        self.assertEqual(
            set(app.click_command.commands),
            {"sync", "inspect", "report", "clean"},
        )

    def test_rejects_duplicate_explicit_subcommand_names(self) -> None:
        app = base_cli.App(name="duplicate-explicit")

        @app.subcommand("deploy")
        def deploy_primary(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "deploy"):

            @app.subcommand(name="deploy")
            def deploy_secondary(ctx: base_cli.Context) -> None:
                del ctx

    def test_rejects_duplicate_inferred_subcommand_names(self) -> None:
        app = base_cli.App(name="duplicate-inferred")

        def primary(ctx: base_cli.Context) -> None:
            del ctx

        primary.__name__ = "status"
        app.subcommand()(primary)

        def secondary(ctx: base_cli.Context) -> None:
            del ctx

        secondary.__name__ = "status"
        with self.assertRaisesRegex(RuntimeError, "status"):
            app.subcommand()(secondary)

    def test_rejects_explicit_collision_with_inferred_suffix_name(self) -> None:
        app = base_cli.App(name="duplicate-mixed")

        @app.subcommand("sync")
        def explicit_sync(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "sync"):

            @app.subcommand()
            def sync_command(ctx: base_cli.Context) -> None:
                del ctx

    def test_custom_subcommand_class_cannot_override_registered_name(self) -> None:
        import click

        class RenamingCommand(click.Command):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.name = "custom-override"

        app = base_cli.App(name="canonical-group")

        @app.subcommand(cls=RenamingCommand)
        def status(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "status|custom-override"):
            _ = app.click_command

    def test_registration_factories_reject_calls_after_materialization(self) -> None:
        single = base_cli.App(name="frozen-single")

        @single.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        self.assertIsNotNone(single.click_command)
        with self.assertRaisesRegex(RuntimeError, "(materialized|frozen)"):
            single.command()

        group = base_cli.App(name="frozen-group")

        @group.subcommand()
        def status(ctx: base_cli.Context) -> None:
            del ctx

        self.assertIsNotNone(group.click_command)
        with self.assertRaisesRegex(RuntimeError, "(materialized|frozen)"):
            group.subcommand()

    def test_deferred_registration_decorators_reject_late_application(self) -> None:
        single = base_cli.App(name="deferred-single")
        deferred_command = single.command()

        @single.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        self.assertIsNotNone(single.click_command)

        def late_main(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "(materialized|frozen)"):
            deferred_command(late_main)

        group = base_cli.App(name="deferred-group")
        deferred_subcommand = group.subcommand()

        @group.subcommand()
        def status(ctx: base_cli.Context) -> None:
            del ctx

        self.assertIsNotNone(group.click_command)

        def late_status(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(RuntimeError, "(materialized|frozen)"):
            deferred_subcommand(late_status)

    def test_failed_empty_materialization_does_not_freeze_registration(self) -> None:
        app = base_cli.App(name="recover-empty")

        with self.assertRaisesRegex(RuntimeError, "No command has been registered"):
            _ = app.click_command

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        self.assertEqual(app.click_command.name, "recover-empty")

    def test_failed_plan_compilation_restores_registration(self) -> None:
        app = base_cli.App(name="recover-transaction")

        @app.subcommand()
        def status(ctx: base_cli.Context) -> None:
            del ctx

        with mock.patch(
            "base_cli.app.compile_redaction_plan",
            side_effect=RuntimeError("plan compilation failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "plan compilation failed"):
                _ = app.click_command

        @app.subcommand()
        def inspect(ctx: base_cli.Context) -> None:
            del ctx

        self.assertEqual(set(app.click_command.commands), {"status", "inspect"})

    def test_materialization_and_late_registration_are_serialized(self) -> None:
        app = base_cli.App(name="registration-race")

        @app.subcommand()
        def status(ctx: base_cli.Context) -> None:
            del ctx

        real_build = app._build_click_command  # pylint: disable=protected-access
        build_entered = threading.Event()
        release_build = threading.Event()
        materialization_errors: list[BaseException] = []
        registration_errors: list[BaseException] = []
        registration_started = threading.Event()
        registration_finished = threading.Event()

        def blocked_build() -> object:
            build_entered.set()
            if not release_build.wait(timeout=5):
                raise AssertionError("test did not release command materialization")
            return real_build()

        def materialize() -> None:
            try:
                _ = app.click_command
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                materialization_errors.append(exc)

        def register_late() -> None:
            registration_started.set()

            def inspect(ctx: base_cli.Context) -> None:
                del ctx

            try:
                app.subcommand()(inspect)
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                registration_errors.append(exc)
            finally:
                registration_finished.set()

        with mock.patch.object(app, "_build_click_command", side_effect=blocked_build):
            materialize_thread = threading.Thread(target=materialize, daemon=True)
            materialize_thread.start()
            self.assertTrue(build_entered.wait(timeout=2), "materialization did not start")

            registration_thread = threading.Thread(target=register_late, daemon=True)
            registration_thread.start()
            self.assertTrue(registration_started.wait(timeout=2), "registration did not start")
            registration_finished.wait(timeout=0.5)
            release_build.set()
            materialize_thread.join(timeout=2)
            registration_thread.join(timeout=2)

        self.assertFalse(materialize_thread.is_alive(), "materialization deadlocked")
        self.assertFalse(registration_thread.is_alive(), "registration deadlocked")
        self.assertEqual(materialization_errors, [])
        self.assertEqual(len(registration_errors), 1)
        self.assertIsInstance(registration_errors[0], RuntimeError)
        self.assertRegex(str(registration_errors[0]), "materialized|frozen")
        self.assertEqual(set(app.click_command.commands), {"status"})

    def test_reentrant_materialization_fails_instead_of_deadlocking(self) -> None:
        class ReentrantApp(base_cli.App):
            def _build_click_command(self) -> object:
                return self.click_command

        app = ReentrantApp(name="reentrant-materialization")
        errors: list[BaseException] = []

        def materialize() -> None:
            try:
                _ = app.click_command
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                errors.append(exc)

        thread = threading.Thread(target=materialize, daemon=True)
        thread.start()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive(), "reentrant materialization deadlocked")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertRegex(str(errors[0]), "(reentrant|materializ)")

    def test_reentrant_registration_during_materialization_is_rejected(self) -> None:
        import click

        app = base_cli.App(name="reentrant-registration")
        errors: list[BaseException] = []

        def late(ctx: base_cli.Context) -> None:
            del ctx

        class RegisteringCommand(click.Command):
            def __init__(self, *args: object, **kwargs: object) -> None:
                try:
                    app.subcommand()(late)
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    errors.append(exc)
                super().__init__(*args, **kwargs)

        @app.subcommand(cls=RegisteringCommand)
        def status(ctx: base_cli.Context) -> None:
            del ctx

        click_group = app.click_command

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertRegex(str(errors[0]), "materializ|frozen")
        self.assertEqual(set(click_group.commands), {"status"})

    def test_module_command_returns_original_callable_and_exposes_stable_app(self) -> None:
        def script(ctx: base_cli.Context) -> None:
            del ctx

        decorated = base_cli.command()(script)

        self.assertIs(decorated, script)
        command_app = base_cli.get_command_app(script)
        self.assertIsInstance(command_app, base_cli.App)
        self.assertIs(base_cli.get_command_app(decorated), command_app)
        self.assertIs(base_cli.get_command_app(script), command_app)

    def test_module_command_rejects_stacked_duplicate_registration(self) -> None:
        def script(ctx: base_cli.Context) -> None:
            del ctx

        decorated = base_cli.command()(script)
        original_app = base_cli.get_command_app(decorated)

        with self.assertRaisesRegex(RuntimeError, "already|@base_cli.command"):
            base_cli.command()(decorated)

        self.assertIs(base_cli.get_command_app(decorated), original_app)

    def test_module_command_explicit_names_seed_the_owner_app(self) -> None:
        def positional(ctx: base_cli.Context) -> None:
            del ctx

        def keyword(ctx: base_cli.Context) -> None:
            del ctx

        positional_decorated = base_cli.command("positional-tool")(positional)
        keyword_decorated = base_cli.command(name="keyword-tool")(keyword)

        self.assertIs(positional_decorated, positional)
        self.assertIs(keyword_decorated, keyword)
        self.assertEqual(base_cli.get_command_app(positional).name, "positional-tool")
        self.assertEqual(base_cli.get_command_app(keyword).name, "keyword-tool")
        self.assertEqual(
            base_cli.get_command_app(positional).click_command.name,
            "positional-tool",
        )
        self.assertEqual(
            base_cli.get_command_app(keyword).click_command.name,
            "keyword-tool",
        )

    def test_module_command_inference_is_click_version_independent(self) -> None:
        @base_cli.command()
        def sync_command(ctx: base_cli.Context) -> None:
            del ctx

        command_app = base_cli.get_command_app(sync_command)
        self.assertEqual(command_app.name, "sync")
        self.assertEqual(command_app.click_command.name, "sync")

    def test_module_command_runs_through_run_app_with_outer_parameter_decorator(self) -> None:
        seen: dict[str, str] = {}

        @base_cli.option("--name", required=True)
        @base_cli.command()
        def greet(ctx: base_cli.Context, name: str) -> None:
            del ctx
            seen["name"] = name

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            stderr = io.StringIO()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(home),
                        "USERPROFILE": str(home),
                        "LOCALAPPDATA": str(home / "AppData" / "Local"),
                        "XDG_CACHE_HOME": str(home / ".cache"),
                        "BASE_CLI_CACHE_DIR": str(home / ".cache"),
                    },
                ),
                redirect_stderr(stderr),
            ):
                status = base_cli.run_app(greet, ["--name", "Ada"])

        self.assertEqual(status, 0, stderr.getvalue())
        self.assertEqual(seen, {"name": "Ada"})

    def test_module_commands_own_independent_apps(self) -> None:
        @base_cli.command()
        def first(ctx: base_cli.Context) -> None:
            del ctx

        @base_cli.command()
        def second(ctx: base_cli.Context) -> None:
            del ctx

        first_app = base_cli.get_command_app(first)
        second_app = base_cli.get_command_app(second)

        self.assertIsNot(first_app, second_app)
        self.assertIs(base_cli.get_command_app(first), first_app)
        self.assertIs(base_cli.get_command_app(second), second_app)
        self.assertEqual(first_app.click_command.name, "first")
        self.assertEqual(second_app.click_command.name, "second")

    def test_ordinary_callable_is_rejected_by_command_app_resolution_and_run_app(self) -> None:
        def ordinary(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(TypeError, "@base_cli.command"):
            base_cli.get_command_app(ordinary)
        with self.assertRaisesRegex(TypeError, "@base_cli.command"):
            base_cli.run_app(ordinary, [])

    def test_delegated_display_identity_overrides_internal_app_name(self) -> None:
        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=lambda: "launcher delegated",
        )
        app = base_cli.App(
            name="internal-implementation",
            version="3.1.4",
            help="Delegated command help.",
            profile=profile,
            log_to_file=False,
        )

        @app.command()
        def implementation(ctx: base_cli.Context) -> None:
            del ctx

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            help_result = invoke(app, ["--help"], home=home)
            version_result = invoke(app, ["--version"], home=home)
            usage_result = invoke(app, ["--unknown"], home=home)

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertIn("Usage: launcher delegated [OPTIONS]", help_result.output)
        self.assertNotIn("Usage: internal-implementation", help_result.output)
        self.assertEqual(version_result.exit_code, 0, version_result.output)
        self.assertIn("launcher delegated, version 3.1.4", version_result.output)
        self.assertEqual(usage_result.exit_code, 2, usage_result.output)
        self.assertIn("Usage: launcher delegated [OPTIONS]", _all_output(usage_result))
        self.assertNotIn("Usage: internal-implementation", _all_output(usage_result))


if __name__ == "__main__":
    unittest.main()
