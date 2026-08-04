from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any

import base_cli
from base_cli.testing import invoke


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class TypedContractTests(unittest.TestCase):
    def test_attachment_contract_is_public_and_preserves_command_identity(self) -> None:
        import click

        @click.command(name="contract")
        def command() -> None:
            pass

        app = base_cli.App(name="contract", log_to_file=False)
        adapter: base_cli.AttachmentAdapter[Any] = app
        self.assertIs(adapter.attach(command), command)
        self.assertIsInstance(base_cli.AttachmentContract, type)

    def test_native_async_callbacks_are_rejected_at_registration(self) -> None:
        app = base_cli.App(name="async-registration", log_to_file=False)

        with self.assertRaisesRegex(RuntimeError, "Native async Click callbacks"):

            @app.command()
            async def command(_context: base_cli.Context[Any, Any, Any]) -> None:
                pass

    def test_attached_async_callbacks_are_rejected_before_mutation(self) -> None:
        import click

        @click.command(name="async-attached")
        async def command() -> None:
            pass

        original_params = tuple(command.params)
        app = base_cli.App(name="async-attached", log_to_file=False)
        with self.assertRaisesRegex(RuntimeError, "Native async Click callbacks"):
            app.attach(command)
        self.assertEqual(tuple(command.params), original_params)
        self.assertFalse(hasattr(command, "__base_cli_attachment__"))

    def test_sync_callback_returning_awaitable_is_rejected_and_closed(self) -> None:
        import click

        closed: list[bool] = []

        class Awaitable:
            def __await__(self) -> Any:
                yield
                return None

            def close(self) -> None:
                closed.append(True)

        @click.command(name="awaitable-result")
        def command() -> Any:
            return Awaitable()

        app = base_cli.App(name="awaitable-result", log_to_file=False)
        app.attach(command)
        with tempfile.TemporaryDirectory() as tmpdir:
            result = invoke(
                app,
                [],
                home=Path(tmpdir),
                reraise_unexpected=True,
            )
        self.assertEqual(result.exit_code, 1)
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertIn("Native async Click callbacks", str(result.exception))
        self.assertEqual(closed, [True])

    def test_foreign_reserved_markers_are_rejected_transactionally(self) -> None:
        import click

        for marker_name in (
            "__base_cli_attachment__",
            "__base_cli_lifecycle_instrumented__",
            "__base_cli_main_instrumented__",
        ):
            with self.subTest(marker_name=marker_name):
                @click.command(name=f"marker-{marker_name[-5:]}")
                def command() -> None:
                    pass

                original_params = tuple(command.params)
                setattr(command, marker_name, True)
                app = base_cli.App(name=command.name or "marker", log_to_file=False)
                with self.assertRaisesRegex(RuntimeError, "reserved"):
                    app.attach(command)
                self.assertEqual(tuple(command.params), original_params)
                self.assertFalse(hasattr(app, "_attached_command") and app._attached_command is command)


if __name__ == "__main__":
    unittest.main()
