from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
from typing import Any

import base_cli


def _option_for(command: Any, declaration: str) -> Any:
    matches = [
        parameter
        for parameter in command.params
        if getattr(parameter, "param_type_name", None) == "option"
        and declaration
        in (
            *tuple(getattr(parameter, "opts", ())),
            *tuple(getattr(parameter, "secondary_opts", ())),
        )
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one option for {declaration!r}, found {len(matches)}"
        )
    return matches[0]


def _option_count(command: Any, declaration: str) -> int:
    return sum(
        declaration
        in (
            *tuple(getattr(parameter, "opts", ())),
            *tuple(getattr(parameter, "secondary_opts", ())),
        )
        for parameter in command.params
        if getattr(parameter, "param_type_name", None) == "option"
    )


def _runner_env(home: Path, **values: str) -> dict[str, str]:
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "BASE_CLI_CACHE_DIR": str(home / ".cache"),
        **values,
    }


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class LifecycleOptionsTests(unittest.TestCase):
    def test_public_option_policy_is_validated_immutable_and_copy_safe(self) -> None:
        envvars = ["TOOL_TRACE", "LEGACY_TRACE"]
        option = base_cli.LifecycleOption("--trace", envvar=envvars)
        policy = base_cli.LifecycleOptions(debug=option)
        envvars.append("MUTATED")

        self.assertEqual(option.envvar, ("TOOL_TRACE", "LEGACY_TRACE"))
        with self.assertRaises(FrozenInstanceError):
            policy.debug = None  # type: ignore[misc]
        with self.assertRaises(ValueError):
            base_cli.LifecycleOption()
        with self.assertRaises(ValueError):
            base_cli.LifecycleOption("trace")
        with self.assertRaises(ValueError):
            base_cli.LifecycleOption("--trace", "--trace")
        with self.assertRaises(TypeError):
            base_cli.LifecycleOption("--trace", name="not-a-destination")

    def test_renamed_debug_and_quiet_control_preparser_diagnostics(self) -> None:
        failure_detail = "renamed pre-parser diagnostic detail"

        def fail_display_command() -> str:
            raise RuntimeError(failure_detail)

        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=fail_display_command,
        )
        lifecycle_options = base_cli.LifecycleOptions(
            debug=base_cli.LifecycleOption("--trace"),
            quiet=base_cli.LifecycleOption("--silent"),
        )

        for name, argv, traceback_expected in (
            ("renamed-debug", ["--trace"], True),
            ("renamed-debug-quiet", ["--trace", "--silent"], False),
        ):
            with self.subTest(case=name):
                app = base_cli.App(
                    name=name,
                    profile=profile,
                    log_to_file=False,
                    lifecycle_options=lifecycle_options,
                )

                @app.command()
                def main(ctx: base_cli.Context) -> None:
                    del ctx

                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    status = base_cli.run_app(app, argv)

                output = stderr.getvalue()
                self.assertEqual(status, 1)
                self.assertIn("Error: Unexpected internal error.", output)
                if traceback_expected:
                    self.assertIn("Traceback", output)
                    self.assertIn(failure_detail, output)
                else:
                    self.assertNotIn("Traceback", output)
                    self.assertNotIn(failure_detail, output)

    def test_negative_debug_alias_does_not_enable_preparser_tracebacks(self) -> None:
        failure_detail = "negative debug alias must not expose this traceback"

        def fail_display_command() -> str:
            raise RuntimeError(failure_detail)

        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=fail_display_command,
        )
        app = base_cli.App(
            name="negative-debug",
            profile=profile,
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--debug/--no-debug"),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status = base_cli.run_app(app, ["--debug", "--no-debug"])

        output = stderr.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("Error: Unexpected internal error.", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn(failure_detail, output)

    def test_reclaimed_default_aliases_do_not_toggle_preparser_diagnostics(self) -> None:
        failure_detail = "reclaimed alias must stay private"

        def fail_display_command() -> str:
            raise RuntimeError(failure_detail)

        profile = replace(
            base_cli.CliProfile.generic(),
            display_command=fail_display_command,
        )
        cases = (
            (
                "renamed-debug-user-default",
                base_cli.LifecycleOptions(
                    debug=base_cli.LifecycleOption("--trace")
                ),
                "--debug",
                ["--debug"],
                False,
            ),
            (
                "disabled-debug-user-default",
                base_cli.LifecycleOptions(debug=None),
                "--debug",
                ["--debug"],
                False,
            ),
            (
                "renamed-quiet-user-default",
                base_cli.LifecycleOptions(
                    debug=base_cli.LifecycleOption("--trace"),
                    quiet=base_cli.LifecycleOption("--silent"),
                ),
                "--quiet",
                ["--trace", "--quiet"],
                True,
            ),
            (
                "disabled-quiet-user-default",
                base_cli.LifecycleOptions(quiet=None),
                "--quiet",
                ["--debug", "--quiet"],
                True,
            ),
        )

        for name, lifecycle_options, user_alias, argv, traceback_expected in cases:
            with self.subTest(case=name):
                app = base_cli.App(
                    name=name,
                    profile=profile,
                    log_to_file=False,
                    lifecycle_options=lifecycle_options,
                )

                @app.command()
                @base_cli.option(user_alias, is_flag=True)
                def main(ctx: base_cli.Context, **_kwargs: bool) -> None:
                    del ctx

                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    status = base_cli.run_app(app, argv)

                output = stderr.getvalue()
                self.assertEqual(status, 1)
                self.assertIn("Error: Unexpected internal error.", output)
                if traceback_expected:
                    self.assertIn("Traceback", output)
                    self.assertIn(failure_detail, output)
                else:
                    self.assertNotIn("Traceback", output)
                    self.assertNotIn(failure_detail, output)

    def test_explicit_default_options_preserve_implicit_app_help(self) -> None:
        from click.testing import CliRunner

        implicit = base_cli.App(
            name="default-contract",
            version="1.2.3",
            log_to_file=False,
        )
        explicit = base_cli.App(
            name="default-contract",
            version="1.2.3",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(),
        )

        @implicit.command()
        def implicit_main(ctx: base_cli.Context) -> None:
            del ctx

        @explicit.command()
        def explicit_main(ctx: base_cli.Context) -> None:
            del ctx

        runner = CliRunner()
        implicit_help = runner.invoke(implicit.click_command, ["--help"])
        explicit_help = runner.invoke(explicit.click_command, ["--help"])

        self.assertEqual(implicit_help.exit_code, 0, implicit_help.output)
        self.assertEqual(explicit_help.exit_code, 0, explicit_help.output)
        self.assertEqual(explicit_help.output, implicit_help.output)
        self.assertEqual(
            [parameter.name for parameter in explicit.click_command.params],
            [
                "version",
                "quiet",
                "debug",
                "environment",
                "config",
                "keep_temp",
                "log_file",
            ],
        )
        for declaration in (
            "--debug",
            "--quiet",
            "-q",
            "--environment",
            "--config",
            "--keep-temp",
            "--log-file",
            "--version",
        ):
            self.assertEqual(_option_count(explicit.click_command, declaration), 1)
        self.assertEqual(_option_count(explicit.click_command, "--dry-run"), 0)

    def test_group_and_leaf_help_have_stable_default_placement(self) -> None:
        from click.testing import CliRunner

        app = base_cli.App(
            name="placement",
            version="2.0.0",
            log_to_file=False,
        )

        @app.subcommand()
        def status(ctx: base_cli.Context) -> None:
            del ctx

        root = app.click_command
        leaf = root.commands["status"]
        runner = CliRunner()
        root_help = runner.invoke(root, ["--help"])
        leaf_help = runner.invoke(root, ["status", "--help"])

        self.assertEqual(root_help.exit_code, 0, root_help.output)
        self.assertEqual(leaf_help.exit_code, 0, leaf_help.output)
        for declaration in (
            "--debug",
            "--quiet",
            "--environment",
            "--config",
            "--keep-temp",
            "--log-file",
        ):
            self.assertEqual(_option_count(root, declaration), 1)
            self.assertEqual(_option_count(leaf, declaration), 1)
            self.assertIn(declaration, root_help.output)
            self.assertIn(declaration, leaf_help.output)
        self.assertEqual(_option_count(root, "--version"), 1)
        self.assertEqual(_option_count(leaf, "--version"), 0)
        self.assertIn("--version", root_help.output)
        self.assertNotIn("--version", leaf_help.output)

    def test_each_default_option_can_be_disabled_independently(self) -> None:
        declarations = {
            "debug": "--debug",
            "quiet": "--quiet",
            "environment": "--environment",
            "config": "--config",
            "keep_temp": "--keep-temp",
            "log_file": "--log-file",
            "version": "--version",
        }

        for field_name, declaration in declarations.items():
            with self.subTest(option=field_name):
                app = base_cli.App(
                    name=f"without-{field_name.replace('_', '-')}",
                    version="3.4.5",
                    log_to_file=False,
                    lifecycle_options=base_cli.LifecycleOptions(
                        **{field_name: None}
                    ),
                )

                @app.command()
                def main(ctx: base_cli.Context) -> None:
                    del ctx

                command = app.click_command
                self.assertEqual(_option_count(command, declaration), 0)
                for other_name, other_declaration in declarations.items():
                    if other_name != field_name:
                        self.assertEqual(
                            _option_count(command, other_declaration),
                            1,
                            f"disabling {field_name} also removed {other_name}",
                        )

    def test_disabled_lifecycle_alias_is_available_to_user_code(self) -> None:
        from click.testing import CliRunner

        seen: list[tuple[bool, bool]] = []
        app = base_cli.App(
            name="user-debug",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(debug=None),
        )

        @app.command()
        @base_cli.option("--debug", is_flag=True)
        def main(ctx: base_cli.Context, debug: bool) -> None:
            seen.append((debug, ctx.debug))

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                app.click_command,
                ["--debug"],
                env=_runner_env(Path(tmpdir)),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [(True, False)])

    def test_options_can_be_renamed_and_configured_independently(self) -> None:
        from click.testing import CliRunner

        seen: list[base_cli.LifecycleValues] = []
        lifecycle_options = base_cli.LifecycleOptions(
            debug=base_cli.LifecycleOption(
                "--trace",
                "-t",
                name="diagnostic",
                help="Enable diagnostic logging.",
                envvar="COMPOSABLE_TRACE",
                show_envvar=True,
                show_default=True,
                default=False,
            ),
            quiet=None,
            environment=base_cli.LifecycleOption(
                "--stage",
                help="Select the deployment stage.",
                metavar="TIER",
                default="development",
                show_default=True,
            ),
            keep_temp=base_cli.LifecycleOption(
                "--preserve-work",
                hidden=True,
            ),
            version=base_cli.LifecycleOption("--build-version"),
        )
        app = base_cli.App(
            name="configured-options",
            version="7.8.9",
            log_to_file=False,
            lifecycle_options=lifecycle_options,
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            seen.append(base_cli.get_lifecycle_values())

        command = app.click_command
        trace = _option_for(command, "--trace")
        stage = _option_for(command, "--stage")
        preserve = _option_for(command, "--preserve-work")
        self.assertEqual(trace.name, "diagnostic")
        self.assertEqual(trace.opts, ["--trace", "-t"])
        self.assertEqual(trace.help, "Enable diagnostic logging.")
        self.assertEqual(trace.envvar, "COMPOSABLE_TRACE")
        self.assertTrue(trace.show_envvar)
        self.assertTrue(trace.show_default)
        self.assertEqual(stage.name, "stage")
        self.assertEqual(stage.metavar, "TIER")
        self.assertEqual(stage.default, "development")
        self.assertTrue(preserve.hidden)

        runner = CliRunner()
        help_result = runner.invoke(command, ["--help"])
        version_result = runner.invoke(command, ["--build-version"])
        with tempfile.TemporaryDirectory() as tmpdir:
            invoke_result = runner.invoke(
                command,
                [],
                env=_runner_env(Path(tmpdir), COMPOSABLE_TRACE="1"),
            )

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertIn("--trace", help_result.output)
        self.assertIn("-t", help_result.output)
        self.assertIn("Enable diagnostic logging.", help_result.output)
        self.assertIn("--stage TIER", help_result.output)
        self.assertIn("development", help_result.output)
        self.assertNotIn("--quiet", help_result.output)
        self.assertNotIn("--preserve-work", help_result.output)
        self.assertNotIn("--version", help_result.output)
        self.assertIn("--build-version", help_result.output)
        self.assertEqual(version_result.exit_code, 0, version_result.output)
        self.assertIn("configured-options, version 7.8.9", version_result.output)
        self.assertEqual(invoke_result.exit_code, 0, invoke_result.output)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].debug)
        self.assertEqual(seen[0].environment, "development")

    def test_renamed_option_derives_a_public_click_name(self) -> None:
        app = base_cli.App(
            name="derived-name",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--diagnostic-mode")
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        option = _option_for(app.click_command, "--diagnostic-mode")
        self.assertEqual(option.name, "diagnostic_mode")
        self.assertFalse(option.expose_value)

    def test_lifecycle_alias_collisions_fail_before_command_materialization(self) -> None:
        app = base_cli.App(
            name="duplicate-lifecycle-alias",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--diagnostic"),
                quiet=base_cli.LifecycleOption("--diagnostic"),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(
            RuntimeError,
            r"debug.*quiet|quiet.*debug",
        ):
            _ = app.click_command

    def test_lifecycle_public_name_collisions_fail_before_materialization(self) -> None:
        app = base_cli.App(
            name="duplicate-lifecycle-name",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--diagnostic", name="shared"),
                quiet=base_cli.LifecycleOption("--silent", name="shared"),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(
            RuntimeError,
            r"debug.*quiet|quiet.*debug|shared",
        ):
            _ = app.click_command

    def test_configured_debug_quiet_conflict_names_visible_declarations(self) -> None:
        from click.testing import CliRunner

        app = base_cli.App(
            name="configured-output-conflict",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--trace"),
                quiet=base_cli.LifecycleOption("--silent"),
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        result = CliRunner().invoke(
            app.click_command,
            ["--trace", "--silent"],
        )

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--trace and --silent cannot be used together", result.output)

    def test_native_user_alias_collision_is_actionable(self) -> None:
        app = base_cli.App(name="native-alias-collision")

        @app.command()
        @base_cli.option("--debug", is_flag=True)
        def main(ctx: base_cli.Context, debug: bool) -> None:
            del ctx, debug

        with self.assertRaisesRegex(
            RuntimeError,
            r"debug.*(--debug|disable|rename)|(--debug|disable|rename).*debug",
        ):
            _ = app.click_command

    def test_native_user_destination_collision_is_actionable(self) -> None:
        app = base_cli.App(name="native-destination-collision")

        @app.command()
        @base_cli.option("--vendor-debug", "debug", is_flag=True)
        def main(ctx: base_cli.Context, debug: bool) -> None:
            del ctx, debug

        with self.assertRaisesRegex(
            RuntimeError,
            r"debug.*(destination|name|disable|rename)|(destination|name|disable|rename).*debug",
        ):
            _ = app.click_command

    def test_lifecycle_alias_cannot_replace_implicit_help(self) -> None:
        app = base_cli.App(
            name="implicit-help-collision",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--help")
            ),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        with self.assertRaisesRegex(
            RuntimeError,
            r"help.*(debug|lifecycle)|(debug|lifecycle).*help",
        ):
            _ = app.click_command

    def test_attached_lifecycle_alias_cannot_replace_configured_help(self) -> None:
        import click

        @click.command(
            name="configured-help-collision",
            context_settings={"help_option_names": ["-h", "--assist"]},
        )
        def command() -> None:
            pass

        original_parameters = tuple(command.params)
        app = base_cli.App(
            name="configured-help-collision",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--assist")
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"assist.*(debug|help)|(debug|help).*assist",
        ):
            app.attach(command)
        self.assertEqual(tuple(command.params), original_parameters)

    def test_attached_help_option_names_none_uses_click_default(self) -> None:
        import click
        from click.testing import CliRunner

        @click.command(
            name="default-help-names",
            context_settings={"help_option_names": None},
        )
        def command() -> None:
            pass

        app = base_cli.App(
            name="default-help-names",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--trace")
            ),
        )
        app.attach(command)

        result = CliRunner().invoke(command, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--help", result.output)
        self.assertIn("--trace", result.output)

    def test_static_token_normalizer_participates_in_collision_checks(self) -> None:
        app = base_cli.App(
            name="normalized-alias-collision",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--TRACE")
            ),
        )

        @app.command(context_settings={"token_normalize_func": str.casefold})
        @base_cli.option("--trace", is_flag=True)
        def main(ctx: base_cli.Context, trace: bool) -> None:
            del ctx, trace

        with self.assertRaisesRegex(
            RuntimeError,
            r"trace.*(debug|lifecycle)|(debug|lifecycle).*trace",
        ):
            _ = app.click_command

    def test_expanded_and_normalized_lifecycle_aliases_must_be_unique(self) -> None:
        cases = (
            (
                "expanded",
                base_cli.LifecycleOption(
                    "--debug/--no-debug",
                    "--no-debug",
                ),
            ),
            (
                "normalized",
                base_cli.LifecycleOption("--TRACE", "--trace"),
            ),
        )

        for name, option in cases:
            with self.subTest(case=name):
                app = base_cli.App(
                    name=f"duplicate-{name}",
                    lifecycle_options=base_cli.LifecycleOptions(debug=option),
                )

                @app.command(context_settings={"token_normalize_func": str.casefold})
                def main(ctx: base_cli.Context) -> None:
                    del ctx

                with self.assertRaisesRegex(
                    RuntimeError,
                    r"debug.*(repeat|unique)|(?:repeat|unique).*debug",
                ):
                    _ = app.click_command

    def test_invalid_derived_destination_fails_consistently(self) -> None:
        import click

        lifecycle_options = base_cli.LifecycleOptions(
            debug=base_cli.LifecycleOption("--")
        )
        native = base_cli.App(
            name="invalid-native-destination",
            lifecycle_options=lifecycle_options,
        )

        @native.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx

        attached_command = click.Command(
            name="invalid-attached-destination",
            callback=lambda: None,
        )
        attached = base_cli.App(
            name="invalid-attached-destination",
            lifecycle_options=lifecycle_options,
        )

        for pathway, operation in (
            ("native", lambda: native.click_command),
            ("attached", lambda: attached.attach(attached_command)),
        ):
            with self.subTest(pathway=pathway), self.assertRaisesRegex(
                RuntimeError,
                r"(derive|determine|destination|name).*debug|debug.*(derive|determine|destination|name)",
            ):
                operation()

    def test_value_lifecycle_fields_cannot_become_dual_flags(self) -> None:
        import click

        for key in ("environment", "config", "log_file"):
            with self.subTest(field=key):
                declaration = key.replace("_", "-")
                lifecycle_options = base_cli.LifecycleOptions(
                    **{
                        key: base_cli.LifecycleOption(
                            f"--{declaration}/--no-{declaration}"
                        )
                    }
                )
                native = base_cli.App(
                    name=f"native-{declaration}-shape",
                    lifecycle_options=lifecycle_options,
                )

                @native.command()
                def main(ctx: base_cli.Context) -> None:
                    del ctx

                attached_command = click.Command(
                    name=f"attached-{declaration}-shape",
                    callback=lambda: None,
                )
                attached_parameters = tuple(attached_command.params)
                attached = base_cli.App(
                    name=attached_command.name,
                    lifecycle_options=lifecycle_options,
                )

                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"{key}.*(?:shape|scalar)|(?:shape|scalar).*{key}",
                ):
                    _ = native.click_command
                with self.assertRaisesRegex(
                    RuntimeError,
                    rf"{key}.*(?:shape|scalar)|(?:shape|scalar).*{key}",
                ):
                    attached.attach(attached_command)
                self.assertEqual(tuple(attached_command.params), attached_parameters)

    def test_typed_meta_state_preserves_typed_dict_and_none_obj_identity(self) -> None:
        import click
        from click.testing import CliRunner

        @dataclass
        class VendorState:
            label: str

        objects: tuple[tuple[str, object | None], ...] = (
            ("typed", VendorState("kept")),
            ("dict", {"vendor": "kept"}),
            ("none", None),
        )

        for label, vendor_object in objects:
            with self.subTest(obj=label), tempfile.TemporaryDirectory() as tmpdir:
                seen: dict[str, Any] = {}
                app = base_cli.App(
                    name=f"meta-{label}",
                    log_to_file=False,
                )

                @app.subcommand()
                def status(ctx: base_cli.Context) -> None:
                    click_context = click.get_current_context()
                    values = base_cli.get_lifecycle_values()
                    seen.update(
                        obj=click_context.obj,
                        values=values,
                        meta_value=click_context.meta[base_cli.LIFECYCLE_META_KEY],
                        context=ctx,
                    )

                result = CliRunner().invoke(
                    app.click_command,
                    ["--debug", "--environment", "stage", "status"],
                    obj=vendor_object,
                    env=_runner_env(Path(tmpdir)),
                )

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIs(seen["obj"], vendor_object)
                self.assertIs(seen["values"], seen["meta_value"])
                self.assertIsInstance(seen["values"], base_cli.LifecycleValues)
                self.assertTrue(seen["values"].debug)
                self.assertFalse(seen["values"].quiet)
                self.assertEqual(seen["values"].environment, "stage")
                self.assertIsNone(seen["values"].config)
                self.assertFalse(seen["values"].keep_temp)
                self.assertIsNone(seen["values"].log_file)
                self.assertFalse(seen["values"].dry_run)
                self.assertTrue(base_cli.LIFECYCLE_META_KEY.startswith("base_cli."))

    def test_group_leaf_precedence_uses_click_source_then_leaf_tiebreak(self) -> None:
        from click.testing import CliRunner

        seen: list[str] = []
        app = base_cli.App(name="source-precedence", log_to_file=False)

        @app.subcommand()
        def status(ctx: base_cli.Context) -> None:
            seen.append(ctx.environment)

        cases = (
            (
                "root command line beats leaf environment",
                ["--environment", "root-cli", "status"],
                {"auto_envvar_prefix": "TOOL"},
                {"TOOL_STATUS_ENVIRONMENT": "leaf-env"},
                "root-cli",
            ),
            (
                "root environment beats leaf default map",
                ["status"],
                {
                    "auto_envvar_prefix": "TOOL",
                    "default_map": {"status": {"environment": "leaf-map"}},
                },
                {"TOOL_ENVIRONMENT": "root-env"},
                "root-env",
            ),
            (
                "leaf environment beats root default map",
                ["status"],
                {
                    "auto_envvar_prefix": "TOOL",
                    "default_map": {"environment": "root-map"},
                },
                {"TOOL_STATUS_ENVIRONMENT": "leaf-env"},
                "leaf-env",
            ),
            (
                "leaf default map wins equal-source tie",
                ["status"],
                {
                    "default_map": {
                        "environment": "root-map",
                        "status": {"environment": "leaf-map"},
                    }
                },
                {},
                "leaf-map",
            ),
            (
                "leaf command line wins equal-source tie",
                [
                    "--environment",
                    "root-cli",
                    "status",
                    "--environment",
                    "leaf-cli",
                ],
                {},
                {},
                "leaf-cli",
            ),
        )

        runner = CliRunner()
        for name, args, extra, case_env, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmpdir:
                seen.clear()
                result = runner.invoke(
                    app.click_command,
                    args,
                    env=_runner_env(Path(tmpdir), **case_env),
                    **extra,
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(seen, [expected])

    def test_attached_injected_option_honors_public_default_map_name(self) -> None:
        import click
        from click.testing import CliRunner

        seen: list[base_cli.LifecycleValues] = []

        @click.command(name="attached-default-map")
        def command() -> None:
            seen.append(base_cli.get_lifecycle_values())

        app = base_cli.App(name="attached-default-map", log_to_file=False)
        app.attach(command)
        debug = _option_for(command, "--debug")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                command,
                [],
                default_map={"debug": True},
                env=_runner_env(Path(tmpdir)),
            )

        self.assertEqual(debug.name, "debug")
        self.assertFalse(debug.expose_value)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].debug)

    def test_attached_static_auto_envvar_prefix_reaches_lifecycle(self) -> None:
        import click
        from click.testing import CliRunner

        seen: list[bool] = []

        @click.command(
            name="attached-static-env",
            context_settings={"auto_envvar_prefix": "STATIC"},
        )
        def command() -> None:
            seen.append(base_cli.get_lifecycle_values().debug)

        app = base_cli.App(name="attached-static-env", log_to_file=False)
        app.attach(command)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                command,
                [],
                env=_runner_env(Path(tmpdir), STATIC_DEBUG="1"),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [True])

    def test_attached_runtime_auto_envvar_prefix_reaches_lifecycle(self) -> None:
        import click
        from click.testing import CliRunner

        seen: list[bool] = []

        @click.command(name="attached-runtime-env")
        def command() -> None:
            seen.append(base_cli.get_lifecycle_values().debug)

        app = base_cli.App(name="attached-runtime-env", log_to_file=False)
        app.attach(command)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                command,
                [],
                auto_envvar_prefix="RUNTIME",
                env=_runner_env(Path(tmpdir), RUNTIME_DEBUG="1"),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [True])

    def test_attached_renamed_public_name_drives_default_map_and_auto_env(self) -> None:
        import click
        from click.testing import CliRunner

        seen: list[bool] = []

        @click.command(name="attached-renamed-source")
        def command() -> None:
            seen.append(base_cli.get_lifecycle_values().debug)

        app = base_cli.App(
            name="attached-renamed-source",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption(
                    "--trace",
                    name="diagnostic",
                )
            ),
        )
        app.attach(command)
        trace = _option_for(command, "--trace")
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            map_result = runner.invoke(
                command,
                [],
                default_map={"diagnostic": True},
                env=_runner_env(Path(tmpdir)),
            )
            env_result = runner.invoke(
                command,
                [],
                auto_envvar_prefix="TOOL",
                env=_runner_env(Path(tmpdir), TOOL_DIAGNOSTIC="1"),
            )

        self.assertEqual(trace.name, "diagnostic")
        self.assertEqual(map_result.exit_code, 0, map_result.output)
        self.assertEqual(env_result.exit_code, 0, env_result.output)
        self.assertEqual(seen, [True, True])

    def test_attached_compatible_option_is_adopted_without_callback_mutation(self) -> None:
        import click
        from click.testing import CliRunner

        callback_values: list[bool] = []
        command_values: list[tuple[bool, bool]] = []

        def vendor_callback(
            _context: click.Context,
            _parameter: click.Parameter,
            value: bool,
        ) -> bool:
            callback_values.append(value)
            return value

        @click.command(name="attached-adoption")
        @click.option(
            "--trace",
            "vendor_debug",
            is_flag=True,
            callback=vendor_callback,
        )
        def command(vendor_debug: bool) -> None:
            command_values.append(
                (vendor_debug, base_cli.get_lifecycle_values().debug)
            )

        vendor_option = _option_for(command, "--trace")
        original_callback = vendor_option.callback
        app = base_cli.App(
            name="attached-adoption",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--trace")
            ),
        )
        app.attach(command)

        self.assertIs(_option_for(command, "--trace"), vendor_option)
        self.assertIs(vendor_option.callback, original_callback)
        self.assertEqual(vendor_option.name, "vendor_debug")
        self.assertEqual(_option_count(command, "--trace"), 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                command,
                [],
                default_map={"vendor_debug": True},
                env=_runner_env(Path(tmpdir)),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(callback_values, [True])
        self.assertEqual(command_values, [(True, True)])

    def test_attached_adoption_rejects_a_conflicting_explicit_destination(self) -> None:
        import click

        @click.command(name="attached-explicit-destination")
        @click.option("--trace", "vendor_debug", is_flag=True)
        def command(vendor_debug: bool) -> None:
            del vendor_debug

        original_parameters = tuple(command.params)
        app = base_cli.App(
            name="attached-explicit-destination",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption(
                    "--trace",
                    name="diagnostic",
                )
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"vendor_debug.*diagnostic|diagnostic.*vendor_debug",
        ):
            app.attach(command)

        self.assertEqual(tuple(command.params), original_parameters)

    def test_attached_adoption_rejects_missing_configured_aliases(self) -> None:
        import click

        @click.command(name="attached-missing-alias")
        @click.option("--trace", is_flag=True)
        def command(trace: bool) -> None:
            del trace

        original_parameters = tuple(command.params)
        app = base_cli.App(
            name="attached-missing-alias",
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--trace", "-t")
            ),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"-t.*(expose|alias)|(?:expose|alias).*-t",
        ):
            app.attach(command)

        self.assertEqual(tuple(command.params), original_parameters)

    def test_attached_adoption_requires_matching_alias_polarity(self) -> None:
        import click

        cases = (
            (
                "configured-negative",
                ("--debug", "--no-debug"),
                base_cli.LifecycleOption("--debug/--no-debug"),
            ),
            (
                "vendor-negative",
                ("--no-debug/--debug",),
                base_cli.LifecycleOption("--no-debug", "--debug"),
            ),
        )

        for name, vendor_declarations, option in cases:
            with self.subTest(case=name):

                @click.command(name=f"attached-polarity-{name}")
                @click.option(*vendor_declarations, is_flag=True)
                def command(**_kwargs: bool) -> None:
                    pass

                original_parameters = tuple(command.params)
                app = base_cli.App(
                    name=f"attached-polarity-{name}",
                    lifecycle_options=base_cli.LifecycleOptions(debug=option),
                )

                with self.assertRaisesRegex(
                    RuntimeError,
                    r"polarity.*(?:debug|no-debug)|(?:debug|no-debug).*polarity",
                ):
                    app.attach(command)

                self.assertEqual(tuple(command.params), original_parameters)

    def test_attached_version_adoption_enforces_name_and_aliases(self) -> None:
        import click

        cases = (
            (
                "destination",
                base_cli.LifecycleOption(
                    "--build-version",
                    name="release",
                ),
                r"vendor_version.*release|release.*vendor_version",
            ),
            (
                "alias",
                base_cli.LifecycleOption("--build-version", "-V"),
                r"-V.*(expose|alias)|(?:expose|alias).*-V",
            ),
        )

        for name, version_option, message in cases:
            with self.subTest(case=name):

                @click.command(name=f"attached-version-{name}")
                @click.version_option(
                    "1.2.3",
                    "--build-version",
                    "vendor_version",
                )
                def command() -> None:
                    pass

                original_parameters = tuple(command.params)
                app = base_cli.App(
                    name=f"attached-version-{name}",
                    version="1.2.3",
                    lifecycle_options=base_cli.LifecycleOptions(
                        version=version_option
                    ),
                )

                with self.assertRaisesRegex(RuntimeError, message):
                    app.attach(command)

                self.assertEqual(tuple(command.params), original_parameters)

    def test_attached_configured_options_appear_only_on_root_help(self) -> None:
        import click
        from click.testing import CliRunner

        @click.group(name="attached-placement")
        def root() -> None:
            pass

        @root.command(name="status")
        def status() -> None:
            pass

        app = base_cli.App(
            name="attached-placement",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--trace"),
                quiet=None,
            ),
        )
        app.attach(root)
        runner = CliRunner()
        root_help = runner.invoke(root, ["--help"])
        leaf_help = runner.invoke(root, ["status", "--help"])

        self.assertEqual(root_help.exit_code, 0, root_help.output)
        self.assertEqual(leaf_help.exit_code, 0, leaf_help.output)
        self.assertIn("--trace", root_help.output)
        self.assertNotIn("--quiet", root_help.output)
        self.assertNotIn("--trace", leaf_help.output)
        self.assertNotIn("--quiet", leaf_help.output)
        self.assertEqual(_option_count(root, "--trace"), 1)
        self.assertEqual(_option_count(status, "--trace"), 0)

    def test_reserved_lifecycle_meta_key_is_never_overwritten(self) -> None:
        import click
        from click.testing import CliRunner

        def occupy_meta(
            click_context: click.Context,
            _parameter: click.Parameter,
            value: bool,
        ) -> bool:
            click_context.meta[base_cli.LIFECYCLE_META_KEY] = "vendor-owned"
            return value

        @click.command(name="reserved-meta")
        @click.option("--vendor", is_flag=True, callback=occupy_meta)
        def command(vendor: bool) -> None:
            del vendor

        app = base_cli.App(name="reserved-meta", log_to_file=False)
        app.attach(command)

        result = CliRunner().invoke(command, ["--vendor"])

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("base_cli.lifecycle", result.output)
        self.assertIn("reserved", result.output)

    def test_attached_vendor_dry_run_does_not_opt_in_by_default(self) -> None:
        import click
        from click.testing import CliRunner

        seen: list[tuple[bool, bool, bool]] = []

        @click.command(name="vendor-dry-run")
        @click.option("--dry-run", is_flag=True)
        def command(dry_run: bool) -> None:
            context = base_cli.get_current_context()
            seen.append(
                (
                    dry_run,
                    context.dry_run,
                    base_cli.get_lifecycle_values().dry_run,
                )
            )

        app = base_cli.App(name="vendor-dry-run", log_to_file=False)
        app.attach(command)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                command,
                ["--dry-run"],
                env=_runner_env(Path(tmpdir)),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [(True, False, False)])

    def test_attached_dry_run_can_be_enabled_and_renamed(self) -> None:
        import click
        from click.testing import CliRunner

        seen: list[tuple[bool, bool, Path | None, bool]] = []

        @click.command(name="attached-preview")
        def command() -> None:
            context = base_cli.get_current_context()
            values = base_cli.get_lifecycle_values()
            seen.append(
                (
                    context.dry_run,
                    values.dry_run,
                    context.log_file,
                    context.temp_dir.exists(),
                )
            )

        app = base_cli.App(
            name="attached-preview",
            lifecycle_options=base_cli.LifecycleOptions(
                dry_run=base_cli.LifecycleOption("--preview")
            ),
        )
        app.attach(command)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = CliRunner().invoke(
                command,
                ["--preview"],
                env=_runner_env(Path(tmpdir)),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [(True, True, None, False)])

    def test_native_conventional_dry_run_conflicts_with_global_policy(self) -> None:
        app = base_cli.App(
            name="duplicate-dry-run-source",
            lifecycle_options=base_cli.LifecycleOptions(
                dry_run=base_cli.LifecycleOption("--simulate")
            ),
        )

        @app.command()
        @base_cli.option("--preview", "dry_run", is_flag=True)
        def main(ctx: base_cli.Context, dry_run: bool) -> None:
            del ctx, dry_run

        with self.assertRaisesRegex(RuntimeError, "only one dry-run source"):
            _ = app.click_command


if __name__ == "__main__":
    unittest.main()
