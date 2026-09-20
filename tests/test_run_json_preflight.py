from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import base_cli


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class RunJsonPreflightTests(unittest.TestCase):
    def _attached_click_command(self, counters: dict[str, Any], *, lazy: bool = False) -> Any:
        import click

        class CountingType(click.ParamType):
            name = "counted"

            def convert(self, value: Any, param: Any, ctx: Any) -> Any:
                del param, ctx
                counters["conversions"] = counters.get("conversions", 0) + 1
                return value

        def default_payload() -> str:
            counters["defaults"] = counters.get("defaults", 0) + 1
            return "default"

        def record_payload(ctx: Any, _parameter: Any, value: str) -> str:
            counters["callbacks"] = counters.get("callbacks", 0) + 1
            ctx.call_on_close(lambda: counters.__setitem__("close_hooks", counters.get("close_hooks", 0) + 1))
            return value

        @click.command("status")
        @click.option(
            "--payload",
            "-p",
            default=default_payload,
            type=CountingType(),
            callback=record_payload,
        )
        @click.option("--pair", nargs=2, type=(str, str))
        @click.argument("tail", required=False)
        def status(payload: str, pair: tuple[str, str] | None, tail: str | None) -> None:
            counters["commands"] = counters.get("commands", 0) + 1
            click.echo(f"PAYLOAD={payload};PAIR={pair};TAIL={tail}")

        class LazyGroup(click.Group):
            def get_command(self, ctx: Any, cmd_name: str) -> Any:
                counters["lazy_resolutions"] = counters.get("lazy_resolutions", 0) + 1
                return super().get_command(ctx, cmd_name)

        group_class = LazyGroup if lazy else click.Group
        group = group_class(name="probe", commands={"status": status})
        app = base_cli.App(
            name="probe",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json/--no-json"),
            ),
        )
        return app.attach(group)

    def _native_app(self) -> base_cli.App:
        app = base_cli.App(
            name="probe",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json/--no-json"),
            ),
        )

        @app.subcommand()
        @base_cli.option("--payload", "-p", default="default")
        @base_cli.option("--pair", nargs=2, type=(str, str))
        @base_cli.argument("tail", required=False)
        def status(
            ctx: base_cli.Context[Any, Any, Any],
            payload: str,
            pair: tuple[str, str] | None,
            tail: str | None,
        ) -> None:
            del ctx
            print(f"PAYLOAD={payload};PAIR={pair};TAIL={tail}")

        return app

    def test_human_invocation_runs_callbacks_defaults_converters_and_close_hooks_once(self) -> None:
        counters: dict[str, Any] = {}
        command = self._attached_click_command(counters)

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(command, ["status"], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("PAYLOAD=default", result.stdout)
        self.assertEqual(counters, {"defaults": 1, "conversions": 1, "callbacks": 1, "commands": 1, "close_hooks": 1})

    def test_json_invocation_runs_callbacks_defaults_converters_and_close_hooks_once(self) -> None:
        counters: dict[str, Any] = {}
        command = self._attached_click_command(counters)

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(command, ["--json", "status"], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["details"]["stdout"].split(";", maxsplit=1)[0], "PAYLOAD=default")
        self.assertEqual(counters, {"defaults": 1, "conversions": 1, "callbacks": 1, "commands": 1, "close_hooks": 1})

    def test_early_parse_errors_do_not_run_consumer_callbacks_during_mode_detection(self) -> None:
        for args, expect_json in (
            (["status", "--unknown"], False),
            (["--json", "status", "--unknown"], True),
        ):
            with self.subTest(args=args):
                counters: dict[str, Any] = {}
                command = self._attached_click_command(counters)
                with tempfile.TemporaryDirectory() as home:
                    result = base_cli.testing.invoke(command, args, home=Path(home))

                self.assertEqual(result.exit_code, 2)
                self.assertEqual(counters, {})
                if expect_json:
                    envelope = json.loads(result.stdout)
                    self.assertEqual(envelope["schema"], "base-cli.error")
                    self.assertIn("No such option", envelope["message"])
                else:
                    self.assertEqual(result.stdout, "")
                    self.assertIn("No such option", result.stderr)

    def test_click_usage_errors_use_the_resolved_default_json_mode(self) -> None:
        import click

        @click.group(
            name="default-map-json",
            context_settings={"default_map": {"json": True}, "token_normalize_func": str.lower},
        )
        def group() -> None:
            pass

        @group.command()
        @click.argument("required", required=True)
        def child(required: str) -> None:
            click.echo(required)

        app = base_cli.App(
            name="default-map-json",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(
                json=base_cli.LifecycleOption("--json/--no-json"),
            ),
        )
        command = app.attach(group)

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(command, ["child"], home=Path(home))

        self.assertEqual(result.exit_code, 2, result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "base-cli.error")
        self.assertEqual(payload["code"], "usage_error")
        self.assertIn("Missing argument", payload["message"])

    def test_lazy_command_resolver_is_called_only_by_real_dispatch(self) -> None:
        counters: dict[str, Any] = {}
        command = self._attached_click_command(counters, lazy=True)

        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(command, ["status"], home=Path(home))

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(counters["lazy_resolutions"], 1)
        self.assertEqual(counters["commands"], 1)
        self.assertEqual(counters["callbacks"], 1)
        self.assertEqual(counters["close_hooks"], 1)

    def test_option_values_and_flags_do_not_false_activate_json_mode(self) -> None:
        equivalent_payload_outputs: list[str] = []
        cases = (
            (["status", "--payload", "--json"], "PAYLOAD=--json"),
            (["status", "--payload=--json"], "PAYLOAD=--json"),
            (["status", "--pair", "first", "--json"], "PAIR=('first', '--json')"),
            (["status", "-p--json"], "PAYLOAD=--json"),
            (["status", "--", "--json"], "TAIL=--json"),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                app = self._native_app()
                with tempfile.TemporaryDirectory() as home:
                    result = base_cli.testing.invoke(app, args, home=Path(home))

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(expected, result.stdout)
                self.assertFalse(result.stdout.lstrip().startswith("{"), result.stdout)
                if args in (
                    ["status", "--payload", "--json"],
                    ["status", "--payload=--json"],
                    ["status", "-p--json"],
                ):
                    equivalent_payload_outputs.append(result.stdout)

        self.assertEqual(len(set(equivalent_payload_outputs)), 1)

    def test_root_leaf_and_negated_json_flags_follow_click_precedence(self) -> None:
        cases = (
            (["--json", "status"], True),
            (["status", "--json"], True),
            (["--json", "status", "--no-json"], False),
            (["--no-json", "status", "--json"], True),
        )
        for args, expect_json in cases:
            with self.subTest(args=args):
                app = self._native_app()
                with tempfile.TemporaryDirectory() as home:
                    result = base_cli.testing.invoke(app, args, home=Path(home))

                self.assertEqual(result.exit_code, 0, result.output)
                if expect_json:
                    self.assertEqual(json.loads(result.stdout)["schema"], "base-cli.output")
                else:
                    self.assertTrue(result.stdout.startswith("PAYLOAD="), result.stdout)


if __name__ == "__main__":
    unittest.main()
