from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

import base_cli


@unittest.skipUnless(importlib.util.find_spec("typer"), "Typer is not installed")
class TyperAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        import typer

        self.typer = typer

    def test_adapter_preserves_typer_tree_typed_parameters_and_context(self) -> None:
        cli = self.typer.Typer(help="Typer root")
        observed: dict[str, object] = {}

        @cli.command()
        def greet(
            name: str,
            count: int = self.typer.Option(1, min=1),
            access_code: str = self.typer.Option(...),
        ) -> None:
            context = base_cli.get_current_context()
            observed["run_id"] = context.run_id
            observed["command"] = context.cli_name
            for _ in range(count):
                self.typer.echo(f"hello {name}")
            del access_code

        command = base_cli.attach_typer(
            cli,
            name="typer-cli",
            log_to_file=False,
            sensitive_parameters={"access_code"},
        )

        self.assertTrue(callable(command.main))
        vendor_click = getattr(self.typer, "_click", None)
        if vendor_click is not None:
            self.assertIsInstance(command, vendor_click.Command)
        self.assertEqual(command.name, "typer-cli")
        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(
                command,
                ["--quiet", "Ada", "--count", "2", "--access-code", "secret"],
                home=Path(home),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.count("hello Ada"), 2)
        self.assertIsInstance(observed["run_id"], str)
        self.assertEqual(observed["command"], "typer-cli")

    def test_nested_apps_help_and_click_exception_remain_native(self) -> None:
        admin = self.typer.Typer(help="Administrative commands")
        cli = self.typer.Typer(help="Root help")
        cli.add_typer(admin, name="admin")

        @admin.command()
        def status() -> None:
            self.typer.echo("ready")

        @admin.command()
        def fail() -> None:
            raise self.typer.BadParameter("invalid state")

        command = base_cli.attach_typer(cli, name="nested-cli", log_to_file=False)
        with tempfile.TemporaryDirectory() as home:
            help_result = base_cli.testing.invoke(
                command,
                ["--help"],
                home=Path(home),
            )
            status_result = base_cli.testing.invoke(
                command,
                ["--quiet", "admin", "status"],
                home=Path(home),
            )
            failure_result = base_cli.testing.invoke(
                command,
                ["admin", "fail"],
                home=Path(home),
            )

        self.assertEqual(help_result.exit_code, 0, help_result.output)
        self.assertIn("Root help", help_result.stdout)
        plain_help = re.sub(r"\x1b\[[0-9;]*m", "", help_result.stdout)
        self.assertIn("--debug", plain_help)
        self.assertEqual(status_result.exit_code, 0, status_result.output)
        self.assertIn("ready", status_result.stdout)
        self.assertNotEqual(failure_result.exit_code, 0)
        self.assertIn("invalid state", failure_result.stderr)

    def test_adapter_class_caches_generated_command(self) -> None:
        cli = self.typer.Typer()

        @cli.command()
        def status() -> None:
            self.typer.echo("ready")

        adapter = base_cli.TyperAdapter(cli)
        command = adapter.attach(name="cached-cli", log_to_file=False)
        self.assertIs(command, adapter.command)
        self.assertEqual(command.name, "cached-cli")

    def test_repeated_attachment_is_idempotent_and_changed_arguments_are_rejected(self) -> None:
        cli = self.typer.Typer()

        @cli.command()
        def status() -> None:
            self.typer.echo("ready")

        adapter = base_cli.TyperAdapter(cli)
        first = adapter.attach(name="cached-cli", log_to_file=False)
        second = adapter.attach()
        self.assertIs(first, second)
        with self.assertRaisesRegex(TypeError, "cannot be changed"):
            adapter.attach(name="other-cli", log_to_file=False)

    def test_adapter_uses_owner_dialect_for_version_option(self) -> None:
        cli = self.typer.Typer()

        @cli.command()
        def status() -> None:
            self.typer.echo("ready")

        command = base_cli.attach_typer(
            cli,
            name="versioned-cli",
            version="9.8.7",
            log_to_file=False,
        )
        with tempfile.TemporaryDirectory() as home:
            result = base_cli.testing.invoke(
                command,
                ["--version"],
                home=Path(home),
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("versioned-cli, version 9.8.7", result.stdout)

    def test_unnamed_multi_command_requires_a_lifecycle_name(self) -> None:
        cli = self.typer.Typer()

        @cli.command()
        def first() -> None:
            pass

        @cli.command()
        def second() -> None:
            pass

        with self.assertRaisesRegex(RuntimeError, "unnamed command group"):
            base_cli.attach_typer(cli, log_to_file=False)


if __name__ == "__main__":
    unittest.main()
