from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import base_cli
from base_cli._runtime import RuntimeDirectoryError
from base_cli.testing import invoke


def _option_count(command: Any, declaration: str) -> int:
    return sum(
        declaration in tuple(getattr(parameter, "opts", ()))
        for parameter in command.params
        if getattr(parameter, "param_type_name", None) == "option"
    )


def _all_output(result: Any) -> str:
    output = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    if stderr and stderr not in output:
        return f"{output}{stderr}"
    return output


class _CountingApp(base_cli.App):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context_create_count = 0
        self.context_cleanup_count = 0
        self.created_contexts: list[base_cli.Context] = []

    def _create_context(
        self,
        standard: dict[str, Any],
        dry_run: bool = False,
    ) -> base_cli.Context:
        self.context_create_count += 1
        context = super()._create_context(standard, dry_run=dry_run)
        self.created_contexts.append(context)
        original_cleanup = context.cleanup

        def count_cleanup() -> None:
            self.context_cleanup_count += 1
            original_cleanup()

        context.cleanup = count_cleanup  # type: ignore[method-assign]
        return context


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class ClickTreeAttachmentTests(unittest.TestCase):
    def test_prebuilt_single_command_preserves_click_contract_and_lifecycle(self) -> None:
        import click

        seen: dict[str, Any] = {}
        cleanup_calls: list[None] = []
        context_settings = {
            "help_option_names": ["-h", "--help"],
            "token_normalize_func": str.casefold,
        }

        @click.command(
            name="vendor-sync",
            help="Synchronize a vendor workspace.",
            epilog="Provided by the vendor package.",
            context_settings=context_settings,
        )
        @click.version_option("9.8.7", prog_name="vendor-sync")
        @click.option(
            "-m",
            "--mode",
            type=click.Choice(["fast", "safe"], case_sensitive=False),
            required=True,
            help="Select the vendor mode.",
        )
        @click.argument("target")
        @click.pass_context
        def vendor_sync(click_context: Any, mode: str, target: str) -> None:
            context = base_cli.get_current_context()
            context.on_cleanup(lambda: cleanup_calls.append(None))
            seen.update(
                click_context=click_context,
                context=context,
                mode=mode,
                target=target,
            )

        original_parameters = tuple(vendor_sync.params)
        original_parameter_state = [
            (
                parameter,
                parameter.name,
                tuple(getattr(parameter, "opts", ())),
                getattr(parameter, "help", None),
                getattr(parameter, "required", False),
            )
            for parameter in original_parameters
        ]
        original_context_settings = dict(vendor_sync.context_settings or {})
        app = _CountingApp(
            name="vendor-sync",
            version="1.2.3",
            log_to_file=False,
        )

        attached = app.attach(vendor_sync)

        self.assertIs(attached, vendor_sync)
        self.assertIs(app.click_command, vendor_sync)
        self.assertEqual(vendor_sync.name, "vendor-sync")
        self.assertEqual(vendor_sync.help, "Synchronize a vendor workspace.")
        self.assertEqual(vendor_sync.epilog, "Provided by the vendor package.")
        self.assertEqual(vendor_sync.context_settings, original_context_settings)
        for parameter, name, declarations, help_text, required in original_parameter_state:
            self.assertTrue(any(candidate is parameter for candidate in vendor_sync.params))
            self.assertEqual(parameter.name, name)
            self.assertEqual(tuple(getattr(parameter, "opts", ())), declarations)
            self.assertEqual(getattr(parameter, "help", None), help_text)
            self.assertEqual(getattr(parameter, "required", False), required)
        self.assertEqual(_option_count(vendor_sync, "--version"), 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            result = invoke(
                app,
                ["--MODE", "FAST", "workspace"],
                home=home,
            )
            help_result = invoke(app, ["-h"], home=home)
            version_result = invoke(app, ["--version"], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen["mode"], "fast")
        self.assertEqual(seen["target"], "workspace")
        self.assertIs(seen["click_context"].command, vendor_sync)
        self.assertEqual(seen["context"].cli_name, "vendor-sync")
        self.assertEqual(cleanup_calls, [None])
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertIn("Synchronize a vendor workspace.", help_result.output)
        self.assertIn("Provided by the vendor package.", help_result.output)
        self.assertIn("Select the vendor mode.", help_result.output)
        self.assertEqual(version_result.exit_code, 0, version_result.output)
        self.assertIn("vendor-sync, version 9.8.7", version_result.output)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_nested_groups_preserve_dispatch_and_arbitrary_result_callbacks(self) -> None:
        import click

        events: list[tuple[str, Any]] = []
        active_run_ids: list[tuple[str, str]] = []

        @click.group(name="workspace", help="Manage workspaces.")
        def root() -> None:
            active_run_ids.append(("root", base_cli.get_current_context().run_id))
            events.append(("root", None))

        @root.group(name="admin", help="Administrative commands.")
        def admin() -> None:
            active_run_ids.append(("admin", base_cli.get_current_context().run_id))
            events.append(("admin", None))

        @admin.command(name="deploy", help="Deploy a target.")
        @click.option("--target", required=True)
        def deploy(target: str) -> dict[str, str]:
            context = base_cli.get_current_context()
            active_run_ids.append(("deploy", context.run_id))
            events.append(("deploy", (target, context.run_id)))
            return {"target": target}

        @admin.result_callback()
        def finish_admin(result: dict[str, str]) -> tuple[str, dict[str, str]]:
            active_run_ids.append(("admin-result", base_cli.get_current_context().run_id))
            events.append(("admin-result", result))
            return ("admin", result)

        @root.result_callback()
        def finish_root(result: tuple[str, dict[str, str]]) -> int:
            active_run_ids.append(("root-result", base_cli.get_current_context().run_id))
            events.append(("root-result", result))
            return 0

        root_callback = root.callback
        admin_callback = admin.callback
        deploy_parameters = tuple(deploy.params)
        app = _CountingApp(name="workspace", log_to_file=False)

        self.assertIs(app.attach(root), root)
        self.assertIs(app.click_command, root)
        self.assertIs(root.commands["admin"], admin)
        self.assertIs(admin.commands["deploy"], deploy)
        self.assertIs(root.callback, root_callback)
        self.assertIs(admin.callback, admin_callback)
        self.assertTrue(
            all(any(candidate is parameter for candidate in deploy.params) for parameter in deploy_parameters)
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                ["--environment", "stage", "admin", "deploy", "--target", "prod"],
                home=Path(tmpdir),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            [name for name, _value in events],
            ["root", "admin", "deploy", "admin-result", "root-result"],
        )
        self.assertEqual(events[2][1][0], "prod")
        self.assertEqual(events[3], ("admin-result", {"target": "prod"}))
        self.assertEqual(events[4], ("root-result", ("admin", {"target": "prod"})))
        self.assertEqual(
            [name for name, _run_id in active_run_ids],
            ["root", "admin", "deploy", "admin-result", "root-result"],
        )
        self.assertEqual(len({run_id for _name, run_id in active_run_ids}), 1)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_aliases_sharing_one_command_are_instrumented_once(self) -> None:
        import click

        invocations: list[tuple[str, str]] = []
        cleanup_calls: list[str] = []

        @click.command(name="synchronize")
        @click.pass_context
        def synchronize(click_context: Any) -> None:
            context = base_cli.get_current_context()
            alias = str(click_context.info_name)
            invocations.append((alias, context.run_id))
            context.on_cleanup(lambda: cleanup_calls.append(alias))

        root = click.Group(name="aliases")
        root.add_command(synchronize, name="sync")
        root.add_command(synchronize, name="ship")
        app = _CountingApp(name="aliases", log_to_file=False)

        app.attach(root)
        callback_after_attachment = synchronize.callback

        self.assertIs(root.commands["sync"], root.commands["ship"])
        self.assertIs(app.click_command, root)
        self.assertIs(synchronize.callback, callback_after_attachment)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            sync_result = invoke(app, ["sync"], home=home)
            ship_result = invoke(app, ["ship"], home=home)

        self.assertEqual(sync_result.exit_code, 0, sync_result.output)
        self.assertEqual(ship_result.exit_code, 0, ship_result.output)
        self.assertEqual([alias for alias, _run_id in invocations], ["sync", "ship"])
        self.assertNotEqual(invocations[0][1], invocations[1][1])
        self.assertEqual(cleanup_calls, ["sync", "ship"])
        self.assertEqual(app.context_create_count, 2)
        self.assertEqual(app.context_cleanup_count, 2)
        self.assertIs(synchronize.callback, callback_after_attachment)

    def test_standard_options_are_added_only_once_at_the_root(self) -> None:
        import click

        seen: dict[str, Any] = {}

        @click.group(name="standard-options")
        @click.option(
            "--debug",
            is_flag=True,
            help="Enable the vendor's debug behavior.",
        )
        def root(debug: bool) -> None:
            seen["vendor_debug"] = debug
            context = base_cli.get_current_context()
            seen["root_base_debug"] = context.debug
            seen["root_environment"] = context.environment

        @root.group(name="nested")
        def nested() -> None:
            pass

        @nested.command(name="status")
        def status() -> None:
            seen["base_debug"] = base_cli.get_current_context().debug

        debug_parameter = next(parameter for parameter in root.params if "--debug" in getattr(parameter, "opts", ()))
        app = _CountingApp(
            name="standard-options",
            version="3.2.1",
            log_to_file=False,
        )

        app.attach(root)
        self.assertIs(app.click_command, root)
        self.assertIs(app.click_command, root)

        for declaration in (
            "--debug",
            "--quiet",
            "--environment",
            "--config",
            "--keep-temp",
            "--log-file",
            "--version",
        ):
            self.assertEqual(
                _option_count(root, declaration),
                1,
                f"expected one root option for {declaration}",
            )
        self.assertTrue(any(parameter is debug_parameter for parameter in root.params))
        self.assertEqual(debug_parameter.help, "Enable the vendor's debug behavior.")
        for command in (nested, status):
            for declaration in (
                "--debug",
                "--quiet",
                "--environment",
                "--config",
                "--keep-temp",
                "--log-file",
                "--version",
            ):
                self.assertEqual(_option_count(command, declaration), 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            result = invoke(
                app,
                ["--debug", "--environment", "stage", "nested", "status"],
                home=home,
            )
            help_result = invoke(app, ["--help"], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(seen["vendor_debug"])
        self.assertTrue(seen["root_base_debug"])
        self.assertEqual(seen["root_environment"], "stage")
        self.assertTrue(seen["base_debug"])
        self.assertEqual(help_result.exit_code, 0, help_result.output)
        for declaration in (
            "--debug",
            "--quiet",
            "--environment",
            "--config",
            "--keep-temp",
            "--log-file",
        ):
            self.assertEqual(help_result.output.count(declaration), 1)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_attached_standard_options_do_not_collide_with_vendor_meta(self) -> None:
        import click

        seen: list[tuple[str | None, bool, Any]] = []

        def set_vendor_meta(
            click_context: Any,
            _parameter: Any,
            value: str,
        ) -> str:
            click_context.meta["base_cli_standard_options"] = "vendor-owned"
            return value

        @click.command(name="meta-safe")
        @click.option("--vendor", callback=set_vendor_meta)
        @click.pass_context
        def command(click_context: Any, vendor: str | None) -> None:
            context = base_cli.get_current_context()
            seen.append(
                (
                    vendor,
                    context.debug,
                    click_context.meta["base_cli_standard_options"],
                )
            )

        app = _CountingApp(name="meta-safe", log_to_file=False)
        app.attach(command)

        with tempfile.TemporaryDirectory() as tmpdir:
            vendor_first = invoke(
                app,
                ["--vendor", "kept", "--debug"],
                home=Path(tmpdir),
            )
            base_first = invoke(
                app,
                ["--debug", "--vendor", "also-kept"],
                home=Path(tmpdir),
            )

        self.assertEqual(vendor_first.exit_code, 0, vendor_first.output)
        self.assertEqual(base_first.exit_code, 0, base_first.output)
        self.assertEqual(
            seen,
            [
                ("kept", True, "vendor-owned"),
                ("also-kept", True, "vendor-owned"),
            ],
        )
        self.assertEqual(app.context_create_count, 2)
        self.assertEqual(app.context_cleanup_count, 2)

    def test_injected_version_uses_click_invocation_alias(self) -> None:
        import click
        from click.testing import CliRunner

        command = click.Command(name="canonical-name", callback=lambda: None)
        app = _CountingApp(
            name="canonical-name",
            version="4.5.6",
            log_to_file=False,
        )
        app.attach(command)

        result = CliRunner().invoke(
            command,
            ["--version"],
            prog_name="alias-bin",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("alias-bin, version 4.5.6", result.output)
        self.assertNotIn("canonical-name, version", result.output)
        self.assertEqual(app.context_create_count, 0)
        self.assertEqual(app.context_cleanup_count, 0)

    def test_semantically_inverted_standard_flags_are_rejected(self) -> None:
        import click

        reversed_declaration = click.Command(
            name="reversed-debug",
            callback=lambda **_kwargs: None,
            params=[click.Option(["--no-debug/--debug"], default=True)],
        )
        false_flag_value = click.Command(
            name="false-debug",
            callback=lambda **_kwargs: None,
            params=[
                click.Option(
                    ["--debug"],
                    is_flag=True,
                    flag_value=False,
                    default=True,
                )
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "--debug.*incompatible"):
            base_cli.App(name="reversed-debug").attach(reversed_declaration)
        with self.assertRaisesRegex(RuntimeError, "--debug.*incompatible"):
            base_cli.App(name="false-debug").attach(false_flag_value)

    def test_one_vendor_parameter_cannot_supply_two_lifecycle_options(self) -> None:
        import click

        command = click.Command(
            name="ambiguous-options",
            callback=lambda **_kwargs: None,
            params=[
                click.Option(
                    ["--environment", "--config"],
                    type=str,
                )
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "combines lifecycle aliases"):
            base_cli.App(name="ambiguous-options").attach(command)

    def test_attach_rejects_invalid_or_already_registered_inputs(self) -> None:
        import click

        app = base_cli.App(name="invalid-attachment")
        with self.assertRaisesRegex(TypeError, "click.Command"):
            app.attach(object())

        registered_app = base_cli.App(name="registered")

        @registered_app.command()
        def registered(context: base_cli.Context) -> None:
            del context

        external = click.Command(name="external", callback=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "registered commands|cannot attach"):
            registered_app.attach(external)

    def test_attach_is_idempotent_only_for_the_same_app_and_factories(self) -> None:
        import click

        command = click.Command(name="ownership", callback=lambda: None)
        app = base_cli.App(name="ownership", log_to_file=False)

        def context_factory(context: base_cli.Context) -> object:
            return context

        def service_factory(context: base_cli.Context) -> object:
            return context

        self.assertIs(
            app.attach(
                command,
                context_factory=context_factory,
                service_factory=service_factory,
            ),
            command,
        )
        self.assertIs(
            app.attach(
                command,
                context_factory=context_factory,
                service_factory=service_factory,
            ),
            command,
        )

        with self.assertRaisesRegex(RuntimeError, "already attached"):
            app.attach(command, context_factory=lambda context: context)
        with self.assertRaisesRegex(RuntimeError, "already attached"):
            base_cli.App(name="other-owner").attach(command)

    def test_attach_publication_failure_rolls_back_and_can_retry(self) -> None:
        import click

        class FailOnceApp(_CountingApp):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.fail_attachment_publication = False
                super().__init__(*args, **kwargs)
                self.fail_attachment_publication = True

            def __setattr__(self, name: str, value: Any) -> None:
                if (
                    name == "_click_command"
                    and value is not None
                    and getattr(self, "fail_attachment_publication", False)
                ):
                    object.__setattr__(self, "fail_attachment_publication", False)
                    raise RuntimeError("simulated publication failure")
                super().__setattr__(name, value)

        command = click.Command(
            name="retry-attachment",
            callback=lambda: None,
            params=[click.Option(["--vendor"])],
        )
        original_parameters = tuple(command.params)
        original_invoke = command.invoke
        original_main = command.main
        app = FailOnceApp(name="retry-attachment", log_to_file=False)

        with self.assertRaisesRegex(RuntimeError, "publication failure"):
            app.attach(command)

        self.assertEqual(tuple(command.params), original_parameters)
        self.assertEqual(command.invoke, original_invoke)
        self.assertEqual(command.main, original_main)
        self.assertFalse(hasattr(command, "__base_cli_attachment__"))
        self.assertFalse(hasattr(command, "__base_cli_lifecycle_instrumented__"))
        self.assertFalse(hasattr(command, "__base_cli_main_instrumented__"))
        self.assertIsNone(app._attached_command)  # pylint: disable=protected-access
        self.assertIsNone(app._click_command)  # pylint: disable=protected-access

        self.assertIs(app.attach(command), command)
        self.assertIs(app.click_command, command)

    def test_factory_failure_still_cleans_and_resets_the_active_context(self) -> None:
        import click

        class FactoryFailure(RuntimeError):
            pass

        cleanup_calls: list[base_cli.Context] = []
        callback_calls: list[None] = []
        failure = FactoryFailure("factory unavailable")

        @click.command(name="factory-failure")
        def command() -> None:
            callback_calls.append(None)

        def context_factory(context: base_cli.Context) -> object:
            self.assertIs(base_cli.get_current_context(), context)
            context.on_cleanup(lambda: cleanup_calls.append(context))
            raise failure

        app = _CountingApp(name="factory-failure", log_to_file=False)
        app.attach(command, context_factory=context_factory)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIs(result.exception, failure)
        self.assertEqual(callback_calls, [])
        self.assertEqual(len(cleanup_calls), 1)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_factory_configuration_and_runtime_failures_translate_to_click_errors(self) -> None:
        import click

        cases = (
            (base_cli.ConfigurationError("invalid application configuration"), click.UsageError),
            (RuntimeDirectoryError("runtime directory unavailable"), click.ClickException),
        )
        for failure, expected_type in cases:
            with self.subTest(failure=type(failure).__name__):

                @click.command(name=f"factory-{type(failure).__name__.lower()}")
                def command() -> None:
                    self.fail("factory failure should prevent callback execution")

                app = _CountingApp(name=command.name or "factory", log_to_file=False)
                app.attach(command, context_factory=lambda _context, failure=failure: (_ for _ in ()).throw(failure))
                with tempfile.TemporaryDirectory() as tmpdir:
                    result = invoke(app, [], home=Path(tmpdir))

                self.assertEqual(result.exit_code, 2 if expected_type is click.UsageError else 1, result.output)
                self.assertIn(str(failure), result.output)
                self.assertEqual(app.context_cleanup_count, 1)

    def test_partial_attachment_initialization_finalizes_before_reraising(self) -> None:
        import click

        class PartialInitializationFailure(BaseException):
            pass

        @click.command(name="partial-attachment")
        def command() -> None:
            self.fail("partial initialization should prevent callback execution")

        app = _CountingApp(name="partial-attachment", log_to_file=False)
        app.attach(command)
        failure = PartialInitializationFailure("context activation interrupted")
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "base_cli._attach.set_current_context",
                side_effect=failure,
            ):
                with self.assertRaises(PartialInitializationFailure) as raised:
                    invoke(app, [], home=Path(tmpdir), reraise_unexpected=True)

        self.assertIs(raised.exception, failure)
        self.assertEqual(app.context_cleanup_count, 1)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_factory_registered_click_resources_close_inside_lifecycle(self) -> None:
        import click

        failure = RuntimeError("factory close failed")
        history_exit_codes: list[int] = []
        events: list[str] = []
        test_case = self

        class OrderedApp(_CountingApp):
            def _create_context(
                self,
                standard: dict[str, Any],
                dry_run: bool = False,
            ) -> base_cli.Context:
                events.append("lifecycle-enter")
                context = super()._create_context(standard, dry_run=dry_run)
                original_cleanup = context.cleanup

                def ordered_cleanup() -> None:
                    events.append("lifecycle-exit")
                    original_cleanup()

                context.cleanup = ordered_cleanup  # type: ignore[method-assign]
                return context

        class FactoryResource:
            def __enter__(self) -> FactoryResource:
                test_case.assertIsNotNone(base_cli.get_current_context())
                events.append("factory-resource-enter")
                return self

            def __exit__(
                self,
                exc_type: Any,
                exc_value: Any,
                traceback: Any,
            ) -> None:
                del exc_type, traceback
                test_case.assertIsNotNone(base_cli.get_current_context())
                test_case.assertIs(exc_value, failure)
                events.append("factory-resource-exit")

        def history_writer(
            _context: base_cli.Context,
            _argv: list[str],
            _sensitive: set[str],
            _started_at: Any,
            exit_code: int,
        ) -> None:
            history_exit_codes.append(exit_code)

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)

        def context_factory(context: base_cli.Context) -> object:
            self.assertIs(base_cli.get_current_context(), context)
            click.get_current_context().with_resource(FactoryResource())
            events.append("context-factory")
            return object()

        def service_factory(context: base_cli.Context) -> object:
            self.assertIsNotNone(context.application_context)

            def fail_close() -> None:
                self.assertIs(base_cli.get_current_context(), context)
                events.append("factory-close-hook")
                raise failure

            click.get_current_context().call_on_close(fail_close)
            events.append("service-factory")
            return object()

        @click.command(name="factory-close")
        def command() -> None:
            events.append("callback")

        app = OrderedApp(name="factory-close", profile=profile)
        app.attach(
            command,
            context_factory=context_factory,
            service_factory=service_factory,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )
            metadata_path = app.created_contexts[0].run_root / "run.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, 1)
        self.assertIs(result.exception, failure)
        self.assertEqual(history_exit_codes, [1])
        self.assertEqual(metadata["status"], "error")
        self.assertEqual(metadata["outcome"], "unexpected_error")
        self.assertEqual(metadata["exit_code"], 1)
        self.assertEqual(
            events,
            [
                "lifecycle-enter",
                "factory-resource-enter",
                "context-factory",
                "service-factory",
                "callback",
                "factory-close-hook",
                "factory-resource-exit",
                "lifecycle-exit",
            ],
        )
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_factory_failure_before_lazy_resolution_opaquely_redacts_argv(self) -> None:
        import click

        failure = RuntimeError("factory stopped lazy resolution")
        get_calls: list[str] = []
        callback_calls: list[None] = []
        log_files: list[Path] = []

        @click.command(name="deferred-child")
        @click.option("--access-code")
        def deferred_child(access_code: str | None) -> None:
            del access_code
            callback_calls.append(None)

        class LazyGroup(click.Group):
            def list_commands(self, ctx: Any) -> list[str]:
                del ctx
                raise AssertionError("factory failure must not enumerate commands")

            def get_command(self, ctx: Any, name: str) -> Any:
                del ctx
                get_calls.append(name)
                return deferred_child if name == "deferred-child" else None

        def context_factory(context: base_cli.Context) -> object:
            if context.log_file is not None:
                log_files.append(context.log_file)
            raise failure

        root = LazyGroup(name="lazy-factory-failure")
        app = _CountingApp(name="lazy-factory-failure")
        app.attach(root, context_factory=context_factory)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [
                    "deferred-child",
                    "--access-code",
                    "factory-pre-resolution-value",
                    "factory-positional-value",
                ],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )
            self.assertEqual(len(log_files), 1)
            log_text = log_files[0].read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 1)
        self.assertIs(result.exception, failure)
        self.assertEqual(get_calls, [])
        self.assertEqual(callback_calls, [])
        self.assertGreaterEqual(log_text.count("[REDACTED]"), 4)
        self.assertNotIn("deferred-child", log_text)
        self.assertNotIn("factory-pre-resolution-value", log_text)
        self.assertNotIn("factory-positional-value", log_text)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_exact_app_and_click_root_names_must_match_before_mutation(self) -> None:
        import click

        command = click.Command(
            name="click-root",
            callback=lambda: None,
            params=[click.Option(["--vendor-mode"])],
        )
        original_parameters = tuple(command.params)
        mismatched = base_cli.App(name="app-root", log_to_file=False)

        with self.assertRaisesRegex(RuntimeError, "app-root.*click-root"):
            mismatched.attach(command)

        self.assertEqual(tuple(command.params), original_parameters)
        matching = base_cli.App(name="click-root", log_to_file=False)
        self.assertIs(matching.attach(command), command)

    def test_distinct_nested_attachment_is_rejected_before_parent_callback(self) -> None:
        import click

        parent_calls: list[None] = []
        child_calls: list[None] = []

        @click.command(name="child")
        def child() -> None:
            child_calls.append(None)

        child_app = _CountingApp(name="child", log_to_file=False)
        child_app.attach(child)

        @click.group(name="parent")
        def parent() -> None:
            parent_calls.append(None)

        parent.add_command(child)
        parent_app = _CountingApp(name="parent", log_to_file=False)
        parent_app.attach(parent)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                parent_app,
                ["child"],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertRegex(str(result.exception), "different base_cli.App|nested")
        self.assertEqual(parent_calls, [])
        self.assertEqual(child_calls, [])
        self.assertEqual(parent_app.context_create_count, 1)
        self.assertEqual(parent_app.context_cleanup_count, 1)
        self.assertEqual(child_app.context_create_count, 0)
        self.assertEqual(child_app.context_cleanup_count, 0)

    def test_native_app_command_cannot_be_attached_or_nested(self) -> None:
        import click

        parent_calls: list[None] = []
        child_calls: list[None] = []
        child_app = _CountingApp(name="native-child", log_to_file=False)

        @child_app.command()
        def native_child(context: base_cli.Context) -> None:
            del context
            child_calls.append(None)

        native_command = child_app.click_command
        original_parameters = tuple(native_command.params)
        attaching_app = base_cli.App(name="native-child", log_to_file=False)

        with self.assertRaisesRegex(RuntimeError, "native base_cli.App"):
            attaching_app.attach(native_command)

        self.assertEqual(tuple(native_command.params), original_parameters)
        self.assertIs(child_app.click_command, native_command)

        @click.group(name="native-parent")
        def parent() -> None:
            parent_calls.append(None)

        parent.add_command(native_command)
        parent_app = _CountingApp(name="native-parent", log_to_file=False)
        parent_app.attach(parent)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                parent_app,
                ["native-child"],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertRegex(str(result.exception), "native base_cli.App|second lifecycle")
        self.assertEqual(parent_calls, [])
        self.assertEqual(child_calls, [])
        self.assertEqual(parent_app.context_create_count, 1)
        self.assertEqual(parent_app.context_cleanup_count, 1)
        self.assertEqual(child_app.context_create_count, 0)
        self.assertEqual(child_app.context_cleanup_count, 0)

    def test_lazy_group_resolves_only_selected_path_and_redacts_dynamic_leaf(self) -> None:
        import click

        calls: dict[str, Any] = {
            "list": 0,
            "get": [],
            "commands_property": 0,
            "imports": [],
            "callbacks": [],
            "log_files": [],
        }
        cached_commands: dict[str, Any] = {}

        def load_command(name: str) -> Any:
            calls["imports"].append(name)

            @click.command(name=name, help=f"Run the {name} integration.")
            @click.option("--access-code", required=True)
            def selected(access_code: str) -> None:
                context = base_cli.get_current_context()
                calls["callbacks"].append((name, access_code, context.run_id))
                calls["log_files"].append(context.log_file)

            return selected

        class LazyGroup(click.Group):
            @property
            def commands(self) -> Any:
                calls["commands_property"] += 1
                raise AssertionError("lazy commands mapping was accessed eagerly")

            @commands.setter
            def commands(self, value: Any) -> None:
                self._lazy_constructor_commands = value

            def list_commands(self, ctx: Any) -> list[str]:
                del ctx
                calls["list"] += 1
                return ["selected", "unused"]

            def get_command(self, ctx: Any, name: str) -> Any:
                del ctx
                calls["get"].append(name)
                if name not in {"selected", "unused"}:
                    return None
                if name not in cached_commands:
                    cached_commands[name] = load_command(name)
                return cached_commands[name]

        lazy_root = LazyGroup(name="lazy-suite", help="Load commands on demand.")
        app = _CountingApp(name="lazy-suite")

        self.assertIs(
            app.attach(lazy_root, sensitive_parameters={"access_code"}),
            lazy_root,
        )
        self.assertIs(app.click_command, lazy_root)
        self.assertEqual(calls["list"], 0)
        self.assertEqual(calls["get"], [])
        self.assertEqual(calls["commands_property"], 0)
        self.assertEqual(calls["imports"], [])

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            first = invoke(
                app,
                ["selected", "--access-code", "first-lazy-value"],
                home=home,
            )
            first_callback = cached_commands["selected"].callback
            second = invoke(
                app,
                ["selected", "--access-code", "second-lazy-value"],
                home=home,
            )

            log_texts = [path.read_text(encoding="utf-8") for path in calls["log_files"] if path is not None]

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(calls["list"], 0)
        self.assertEqual(calls["get"], ["selected", "selected"])
        self.assertEqual(calls["commands_property"], 0)
        self.assertEqual(calls["imports"], ["selected"])
        self.assertEqual(
            [(name, value) for name, value, _run_id in calls["callbacks"]],
            [
                ("selected", "first-lazy-value"),
                ("selected", "second-lazy-value"),
            ],
        )
        self.assertNotEqual(calls["callbacks"][0][2], calls["callbacks"][1][2])
        self.assertIs(cached_commands["selected"].callback, first_callback)
        self.assertEqual(app.context_create_count, 2)
        self.assertEqual(app.context_cleanup_count, 2)
        self.assertEqual(len(log_texts), 2)
        for log_text in log_texts:
            self.assertIn("[REDACTED]", log_text)
            self.assertNotIn("first-lazy-value", log_text)
            self.assertNotIn("second-lazy-value", log_text)

    def test_context_and_service_factories_extend_base_context_without_replacing_click_obj(self) -> None:
        import click

        vendor_object = {"vendor": "preserved"}
        application_context = object()
        services = object()
        events: list[tuple[str, Any]] = []

        @click.group(name="factories")
        @click.pass_context
        def root(click_context: Any) -> None:
            context = base_cli.get_current_context()
            click_context.obj = vendor_object

            def close_click_context() -> None:
                self.assertIs(base_cli.get_current_context(), context)
                events.append(("click-close", app.context_cleanup_count))

            click_context.call_on_close(close_click_context)
            events.append(
                (
                    "root",
                    (
                        click_context.obj,
                        context.application_context,
                        context.services,
                    ),
                )
            )

        @root.command(name="run")
        @click.pass_obj
        def run(vendor_state: Any) -> None:
            context = base_cli.get_current_context()
            events.append(
                (
                    "callback",
                    (
                        vendor_state,
                        context.application_context,
                        context.services,
                    ),
                )
            )

        @root.result_callback()
        def finish(result: Any) -> Any:
            context = base_cli.get_current_context()
            events.append(
                (
                    "result",
                    (context.application_context, context.services),
                )
            )
            return result

        def context_factory(context: base_cli.Context) -> object:
            self.assertIs(base_cli.get_current_context(), context)
            events.append(("context-factory", context))

            def cleanup_base_context() -> None:
                self.assertIs(base_cli.get_current_context(), context)
                events.append(("base-cleanup", app.context_cleanup_count))

            context.on_cleanup(cleanup_base_context)
            return application_context

        def service_factory(context: base_cli.Context) -> object:
            self.assertIs(base_cli.get_current_context(), context)
            self.assertIs(context.application_context, application_context)
            events.append(("service-factory", context))
            return services

        app = _CountingApp(name="factories", log_to_file=False)
        app.attach(
            root,
            context_factory=context_factory,
            service_factory=service_factory,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            help_result = invoke(app, ["--help"], home=home)
            result = invoke(app, ["run"], home=home)

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            [name for name, _value in events],
            [
                "context-factory",
                "service-factory",
                "root",
                "callback",
                "result",
                "click-close",
                "base-cleanup",
            ],
        )
        root_vendor_state, root_application_context, root_services = events[2][1]
        self.assertIs(root_vendor_state, vendor_object)
        self.assertIs(root_application_context, application_context)
        self.assertIs(root_services, services)
        vendor_state, actual_application_context, actual_services = events[3][1]
        self.assertIs(vendor_state, vendor_object)
        self.assertIs(actual_application_context, application_context)
        self.assertIs(actual_services, services)
        result_application_context, result_services = events[4][1]
        self.assertIs(result_application_context, application_context)
        self.assertIs(result_services, services)
        self.assertEqual(events[5], ("click-close", 0))
        self.assertEqual(events[6], ("base-cleanup", 1))
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_chained_group_keeps_one_lifecycle_and_click_list_results(self) -> None:
        import click

        events: list[tuple[str, Any]] = []
        run_ids: list[str] = []
        log_files: list[Path] = []

        @click.group(name="pipeline", chain=True)
        def pipeline() -> None:
            context = base_cli.get_current_context()
            run_ids.append(context.run_id)
            if context.log_file is not None:
                log_files.append(context.log_file)
            events.append(("pipeline", None))

        @pipeline.command(name="extract")
        @click.option("--source-code", required=True)
        def extract(source_code: str) -> str:
            context = base_cli.get_current_context()
            run_ids.append(context.run_id)
            events.append(("extract", source_code))
            return "rows"

        @pipeline.command(name="load")
        @click.option("--destination-code", required=True)
        @click.argument("payload")
        def load(destination_code: str, payload: str) -> dict[str, int]:
            context = base_cli.get_current_context()
            run_ids.append(context.run_id)
            events.append(("load", (destination_code, payload)))
            return {"loaded": 3}

        @pipeline.result_callback()
        def finish(results: list[Any]) -> list[Any]:
            context = base_cli.get_current_context()
            run_ids.append(context.run_id)
            events.append(("result", results))
            return results

        app = _CountingApp(name="pipeline")
        app.attach(
            pipeline,
            sensitive_parameters={"source_code", "destination_code", "payload"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [
                    "extract",
                    "--source-code",
                    "first-chain-value",
                    "load",
                    "--destination-code",
                    "second-chain-option",
                    "second-chain-argument",
                ],
                home=Path(tmpdir),
            )
            self.assertEqual(len(log_files), 1)
            log_text = log_files[0].read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            events,
            [
                ("pipeline", None),
                ("extract", "first-chain-value"),
                ("load", ("second-chain-option", "second-chain-argument")),
                ("result", ["rows", {"loaded": 3}]),
            ],
        )
        self.assertGreaterEqual(log_text.count("[REDACTED]"), 3)
        self.assertNotIn("first-chain-value", log_text)
        self.assertNotIn("second-chain-option", log_text)
        self.assertNotIn("second-chain-argument", log_text)
        self.assertEqual(len(set(run_ids)), 1)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_later_chain_parse_failure_redacts_earlier_and_failing_values(self) -> None:
        import click

        callback_calls: list[str] = []

        @click.group(name="failing-chain", chain=True)
        def pipeline() -> None:
            callback_calls.append("pipeline")

        @pipeline.command(name="first")
        @click.option("--source-code", required=True)
        def first(source_code: str) -> None:
            del source_code
            callback_calls.append("first")

        @pipeline.command(name="second")
        @click.option("--destination-code", required=True)
        @click.option("--confirm", is_flag=True, required=True)
        @click.argument("payload")
        def second(destination_code: str, confirm: bool, payload: str) -> None:
            del destination_code, confirm, payload
            callback_calls.append("second")

        app = _CountingApp(name="failing-chain")
        app.attach(
            pipeline,
            sensitive_parameters={"source_code", "destination_code", "payload"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [
                    "first",
                    "--source-code",
                    "earlier-chain-value",
                    "second",
                    "--destination-code",
                    "failing-chain-option",
                    "failing-chain-argument",
                ],
                home=Path(tmpdir),
            )
            self.assertEqual(len(app.created_contexts), 1)
            log_file = app.created_contexts[0].log_file
            self.assertIsNotNone(log_file)
            log_text = log_file.read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("Missing option '--confirm'", _all_output(result))
        # Click invokes a chain group's root callback before constructing every
        # child context. Neither selected member may run after the later parse
        # failure.
        self.assertEqual(callback_calls, ["pipeline"])
        self.assertGreaterEqual(log_text.count("[REDACTED]"), 3)
        self.assertNotIn("earlier-chain-value", log_text)
        self.assertNotIn("failing-chain-option", log_text)
        self.assertNotIn("failing-chain-argument", log_text)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_preparse_click_resources_close_inside_lifecycle_and_record_failure(
        self,
    ) -> None:
        import click

        history_exit_codes: list[int] = []
        failure = RuntimeError("vendor close failed")
        events: list[str] = []
        test_case = self

        class OrderedApp(_CountingApp):
            def _create_context(
                self,
                standard: dict[str, Any],
                dry_run: bool = False,
            ) -> base_cli.Context:
                events.append("lifecycle-enter")
                context = super()._create_context(standard, dry_run=dry_run)
                original_cleanup = context.cleanup

                def ordered_cleanup() -> None:
                    events.append("lifecycle-exit")
                    original_cleanup()

                context.cleanup = ordered_cleanup  # type: ignore[method-assign]
                return context

        class VendorResource:
            def __enter__(self) -> VendorResource:
                events.append("vendor-enter")
                return self

            def __exit__(
                self,
                exc_type: Any,
                exc_value: Any,
                traceback: Any,
            ) -> None:
                del exc_type, traceback
                self_context = base_cli.get_current_context()
                test_case.assertIsNotNone(self_context)
                test_case.assertIs(exc_value, failure)
                events.append("vendor-exit")

        class FactoryResource:
            def __enter__(self) -> FactoryResource:
                self_context = base_cli.get_current_context()
                test_case.assertIsNotNone(self_context)
                events.append("factory-resource-enter")
                return self

            def __exit__(
                self,
                exc_type: Any,
                exc_value: Any,
                traceback: Any,
            ) -> None:
                del exc_type, traceback
                self_context = base_cli.get_current_context()
                test_case.assertIsNotNone(self_context)
                test_case.assertIsNone(exc_value)
                events.append("factory-resource-exit")

        def history_writer(
            _context: base_cli.Context,
            _argv: list[str],
            _sensitive: set[str],
            _started_at: Any,
            exit_code: int,
        ) -> None:
            history_exit_codes.append(exit_code)

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)

        def context_factory(context: base_cli.Context) -> object:
            self.assertIs(base_cli.get_current_context(), context)
            click.get_current_context().with_resource(FactoryResource())
            events.append("context-factory")
            return object()

        def register_close_failure(
            click_context: Any,
            _parameter: Any,
            value: str,
        ) -> str:
            click_context.with_resource(VendorResource())

            def fail_close() -> None:
                self.assertIsNotNone(base_cli.get_current_context())
                events.append("close-hook")
                raise failure

            click_context.call_on_close(fail_close)
            return value

        @click.command(name="close-failure")
        @click.option("--vendor", callback=register_close_failure)
        def command(vendor: str | None) -> None:
            del vendor
            events.append("callback")

        app = OrderedApp(name="close-failure", profile=profile)
        app.attach(command, context_factory=context_factory)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                ["--vendor", "value"],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )
            self.assertEqual(len(app.created_contexts), 1)
            metadata_path = app.created_contexts[0].run_root / "run.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(result.exit_code, 1)
        self.assertIs(result.exception, failure)
        self.assertEqual(history_exit_codes, [1])
        self.assertEqual(metadata["status"], "error")
        self.assertEqual(metadata["outcome"], "unexpected_error")
        self.assertEqual(metadata["exit_code"], 1)
        self.assertEqual(
            events,
            [
                "vendor-enter",
                "lifecycle-enter",
                "factory-resource-enter",
                "context-factory",
                "callback",
                "factory-resource-exit",
                "close-hook",
                "vendor-exit",
                "lifecycle-exit",
            ],
        )
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_close_hook_click_exit_controls_run_app_status_and_metadata(self) -> None:
        import click

        history_exit_codes: list[int] = []

        def history_writer(
            _context: base_cli.Context,
            _argv: list[str],
            _sensitive: set[str],
            _started_at: Any,
            exit_code: int,
        ) -> None:
            history_exit_codes.append(exit_code)

        profile = replace(base_cli.CliProfile.generic(), history_writer=history_writer)

        @click.command(name="close-exit")
        @click.pass_context
        def command(click_context: Any) -> None:
            click_context.call_on_close(lambda: click_context.exit(7))

        app = _CountingApp(name="close-exit", profile=profile)
        app.attach(command)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))
            self.assertEqual(len(app.created_contexts), 1)
            metadata_path = app.created_contexts[0].run_root / "run.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(
            result.exit_code,
            7,
            f"{result.output}\nhistory={history_exit_codes!r} metadata={metadata!r}",
        )
        self.assertEqual(history_exit_codes, [7])
        self.assertEqual(metadata["status"], "error")
        self.assertEqual(metadata["outcome"], "nonzero_return")
        self.assertEqual(metadata["exit_code"], 7)
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_direct_click_main_parity_preserves_results_and_redacts_argv(self) -> None:
        import click
        from click.testing import CliRunner

        log_files: list[Path] = []
        callback_values: list[str] = []

        @click.command(name="direct-main")
        @click.option("--access-code", required=True)
        def command(access_code: str) -> dict[str, str]:
            context = base_cli.get_current_context()
            callback_values.append(access_code)
            if context.log_file is not None:
                log_files.append(context.log_file)
            return {"value": access_code}

        app = _CountingApp(name="direct-main")
        app.attach(command, sensitive_parameters={"access_code"})

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            environment = {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "BASE_CLI_CACHE_DIR": str(home / ".cache"),
            }
            runner_result = CliRunner().invoke(
                command,
                ["--access-code", "runner-secret"],
                env=environment,
            )
            with mock.patch.dict(os.environ, environment):
                direct_result = command.main(
                    args=["--access-code", "main-secret"],
                    prog_name="direct-main",
                    standalone_mode=False,
                )
                iterator_result = command.main(
                    args=iter(["--access-code", "iterator-secret"]),
                    prog_name="direct-main",
                    standalone_mode=False,
                )
            log_texts = [path.read_text(encoding="utf-8") for path in log_files]

        self.assertEqual(runner_result.exit_code, 0, runner_result.output)
        self.assertEqual(direct_result, {"value": "main-secret"})
        self.assertEqual(iterator_result, {"value": "iterator-secret"})
        self.assertEqual(
            callback_values,
            ["runner-secret", "main-secret", "iterator-secret"],
        )
        self.assertEqual(app.context_create_count, 3)
        self.assertEqual(app.context_cleanup_count, 3)
        self.assertEqual(len(log_texts), 3)
        for log_text in log_texts:
            self.assertIn("[REDACTED]", log_text)
            self.assertNotIn("runner-secret", log_text)
            self.assertNotIn("main-secret", log_text)
            self.assertNotIn("iterator-secret", log_text)
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_nested_attached_main_scopes_argv_and_preserves_native_status(self) -> None:
        import click

        inner_calls: list[None] = []
        outer_secret = "OUTER-SUPER-SECRET"

        @click.command(name="inner-attached")
        def inner_command() -> dict[str, bool]:
            inner_calls.append(None)
            return {"inner": True}

        inner_app = _CountingApp(name="inner-attached")
        inner_app.attach(inner_command)

        outer_app = _CountingApp(name="outer-native")

        @outer_app.command()
        @base_cli.option("--credential", sensitive=True, required=True)
        def outer_native(context: base_cli.Context, credential: str) -> int:
            self.assertEqual(context.cli_name, "outer-native")
            self.assertEqual(credential, outer_secret)
            result = inner_command.main(
                args=[],
                prog_name="inner-alias",
                standalone_mode=False,
            )
            self.assertEqual(result, {"inner": True})
            return 9

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": tmpdir,
                    "USERPROFILE": tmpdir,
                    "XDG_CACHE_HOME": str(Path(tmpdir) / ".cache"),
                    "BASE_CLI_CACHE_DIR": str(Path(tmpdir) / ".cache"),
                },
            ):
                status = base_cli.run_app(
                    outer_app,
                    ["--credential", outer_secret],
                )
            log_paths = [
                outer_app.created_contexts[0].log_file,
                inner_app.created_contexts[0].log_file,
            ]
            self.assertTrue(all(path is not None for path in log_paths))
            log_texts = [path.read_text(encoding="utf-8") for path in log_paths if path is not None]

        self.assertEqual(status, 9)
        self.assertEqual(inner_calls, [None])
        self.assertEqual(len(log_texts), 2)
        self.assertIn("[REDACTED]", log_texts[0])
        self.assertNotIn("--credential", log_texts[1])
        for log_text in log_texts:
            self.assertNotIn(outer_secret, log_text)
        self.assertEqual(outer_app.context_create_count, 1)
        self.assertEqual(outer_app.context_cleanup_count, 1)
        self.assertEqual(inner_app.context_create_count, 1)
        self.assertEqual(inner_app.context_cleanup_count, 1)

    def test_run_app_materialization_failure_restores_invocation_state(self) -> None:
        import importlib

        app_module = importlib.import_module("base_cli.app")
        ambient_argv = ["ambient-cli", "ambient-secret"]
        ambient_bypass = object()
        argv_token = app_module._INVOCATION_ARGV.set(ambient_argv)
        bypass_token = app_module._INVOCATION_MAIN_BYPASS.set(ambient_bypass)
        unconfigured_app = base_cli.App(
            name="materialization-failure",
            log_to_file=False,
        )

        try:
            with self.assertRaisesRegex(RuntimeError, "No command has been registered"):
                base_cli.run_app(
                    unconfigured_app,
                    [],
                    reraise_unexpected=True,
                )
            self.assertIs(app_module._INVOCATION_ARGV.get(), ambient_argv)
            self.assertIs(app_module._INVOCATION_MAIN_BYPASS.get(), ambient_bypass)
        finally:
            app_module._INVOCATION_MAIN_BYPASS.reset(bypass_token)
            app_module._INVOCATION_ARGV.reset(argv_token)

    def test_module_attach_with_existing_app_returns_the_same_command(self) -> None:
        import click

        seen: list[str] = []

        @click.command(name="module-existing")
        def command() -> None:
            seen.append(base_cli.get_current_context().cli_name)

        app = _CountingApp(name="module-existing", log_to_file=False)

        attached = base_cli.attach(command, app=app)

        self.assertIs(attached, command)
        self.assertIs(app.click_command, command)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(app, [], home=Path(tmpdir))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, ["module-existing"])
        self.assertEqual(app.context_create_count, 1)
        self.assertEqual(app.context_cleanup_count, 1)

    def test_module_attach_can_create_an_app_for_the_run_app_boundary(self) -> None:
        import click

        seen: dict[str, Any] = {}
        application_context = object()
        services = object()
        factory_calls: list[str] = []

        def context_factory(context: base_cli.Context) -> object:
            self.assertIs(base_cli.get_current_context(), context)
            factory_calls.append("context")
            return application_context

        def service_factory(context: base_cli.Context) -> object:
            self.assertIs(context.application_context, application_context)
            factory_calls.append("services")
            return services

        @click.command(name="module-created")
        def command() -> dict[str, str]:
            context = base_cli.get_current_context()
            seen["cli_name"] = context.cli_name
            seen["context"] = context
            seen["application_context"] = context.application_context
            seen["services"] = context.services
            context.on_cleanup(lambda: seen.update(cleaned=True))
            return {"vendor": "result"}

        attached = base_cli.attach(
            command,
            name="module-created",
            log_to_file=False,
            context_factory=context_factory,
            service_factory=service_factory,
            sensitive_parameters={"vendor_result"},
        )

        self.assertIs(attached, command)
        self.assertIs(base_cli.attach(command), command)
        implicit_app = base_cli.get_command_app(command)
        self.assertIs(base_cli.attach(command, app=implicit_app), command)
        self.assertIs(
            base_cli.attach(
                command,
                context_factory=context_factory,
                service_factory=service_factory,
                sensitive_parameters={"vendor_result"},
            ),
            command,
        )
        with self.assertRaisesRegex(RuntimeError, "already attached"):
            base_cli.attach(
                command,
                context_factory=context_factory,
                service_factory=service_factory,
                sensitive_parameters={"different"},
            )
        with self.assertRaisesRegex(RuntimeError, "already attached"):
            base_cli.attach(
                command,
                sensitive_parameters={"different-without-factories"},
            )
        self.assertIs(
            base_cli.attach(
                command,
                context_factory=context_factory,
                service_factory=service_factory,
            ),
            command,
        )
        with self.assertRaisesRegex(RuntimeError, "already attached"):
            base_cli.attach(
                command,
                context_factory=lambda context: context,
                service_factory=service_factory,
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                    "XDG_CACHE_HOME": str(home / ".cache"),
                    "BASE_CLI_CACHE_DIR": str(home / ".cache"),
                },
            ):
                status = base_cli.run_app(attached, [])

        self.assertEqual(status, 0)
        self.assertEqual(seen["cli_name"], "module-created")
        self.assertIs(seen["application_context"], application_context)
        self.assertIs(seen["services"], services)
        self.assertEqual(factory_calls, ["context", "services"])
        self.assertTrue(seen["cleaned"])
        with self.assertRaisesRegex(RuntimeError, "context is not active"):
            base_cli.get_current_context()

    def test_module_attach_rejects_unnamed_click_commands_directly(self) -> None:
        import click

        command = click.Command(name=None, callback=lambda: None)

        with self.assertRaisesRegex(TypeError, "named Click command"):
            base_cli.attach(command, name="explicit-name")


if __name__ == "__main__":
    unittest.main()
