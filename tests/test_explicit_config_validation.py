from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import base_cli
import base_cli.app as app_module
from base_cli._lifecycle import RunRecorder
from base_cli.config import load_yaml_file
from base_cli.testing import invoke


def _combined_output(result: Any) -> str:
    output = result.output
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    return output if not stderr or stderr in output else output + stderr


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class ExplicitConfigValidationTests(unittest.TestCase):
    def _app_with_forbidden_profile_loaders(
        self,
        calls: list[str],
    ) -> base_cli.App:
        def forbidden(name: str):
            def callback(*_args: object) -> object:
                calls.append(name)
                raise AssertionError(f"{name} must not run before explicit-path validation")

            return callback

        profile = base_cli.CliProfile.generic(
            discover_project=forbidden("discover_project"),
            load_user_config=forbidden("load_user_config"),
            load_config=forbidden("load_config"),
            resolve_workspace_root=forbidden("resolve_workspace_root"),
        )
        profile = replace(
            profile,
            resolve_runtime=forbidden("resolve_runtime"),
            history_writer=forbidden("history_writer"),
        )
        return base_cli.App(name="strict-config-path", profile=profile)

    def _assert_no_runtime_artifacts(self, home: Path) -> None:
        cache_root = home / ".cache"
        self.assertFalse(cache_root.exists())
        self.assertEqual(list(cache_root.rglob("run.json")), [])
        self.assertEqual(list(cache_root.rglob("*.log")), [])

    def test_missing_explicit_config_is_rejected_before_profile_or_runtime_startup(self) -> None:
        calls: list[str] = []
        command_calls: list[None] = []
        app = self._app_with_forbidden_profile_loaders(calls)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            command_calls.append(None)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            missing = home / "missing-config.yml"
            with (
                mock.patch.object(app_module, "configure_logger") as configure_logger,
                mock.patch.object(RunRecorder, "start") as start_metadata,
            ):
                result = invoke(app, ["--config", str(missing)], home=home)

            output = _combined_output(result)
            self.assertEqual(result.exit_code, 2, output)
            self.assertIn(str(missing), output)
            self.assertIn("does not exist", output)
            self.assertNotIn("Traceback", output)
            self.assertEqual(calls, [])
            self.assertEqual(command_calls, [])
            configure_logger.assert_not_called()
            start_metadata.assert_not_called()
            self._assert_no_runtime_artifacts(home)

    def test_unreadable_explicit_config_is_rejected_before_profile_or_runtime_startup(self) -> None:
        import click.types

        calls: list[str] = []
        command_calls: list[None] = []
        app = self._app_with_forbidden_profile_loaders(calls)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            command_calls.append(None)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config = home / "unreadable-config.yml"
            config.write_text("environment: test\n", encoding="utf-8")
            real_access = os.access

            def deny_config_read(path: os.PathLike[str] | str, mode: int) -> bool:
                if Path(path) == config and mode == os.R_OK:
                    return False
                return real_access(path, mode)

            with (
                mock.patch.object(click.types.os, "access", side_effect=deny_config_read),
                mock.patch.object(app_module, "configure_logger") as configure_logger,
                mock.patch.object(RunRecorder, "start") as start_metadata,
            ):
                result = invoke(app, ["--config", str(config)], home=home)

            output = _combined_output(result)
            self.assertEqual(result.exit_code, 2, output)
            self.assertIn(str(config), output)
            self.assertIn("is not readable", output)
            self.assertNotIn("Traceback", output)
            self.assertEqual(calls, [])
            self.assertEqual(command_calls, [])
            configure_logger.assert_not_called()
            start_metadata.assert_not_called()
            self._assert_no_runtime_artifacts(home)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is not available")
    def test_non_regular_explicit_config_is_rejected_before_profile_or_runtime_startup(self) -> None:
        calls: list[str] = []
        command_calls: list[None] = []
        app = self._app_with_forbidden_profile_loaders(calls)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            command_calls.append(None)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config = home / "config.fifo"
            os.mkfifo(config)
            with (
                mock.patch.object(app_module, "configure_logger") as configure_logger,
                mock.patch.object(RunRecorder, "start") as start_metadata,
            ):
                result = invoke(app, ["--config", str(config)], home=home)

            output = _combined_output(result)
            self.assertEqual(result.exit_code, 2, output)
            self.assertIn(str(config), output)
            self.assertIn("is not a regular file", output)
            self.assertEqual(calls, [])
            self.assertEqual(command_calls, [])
            configure_logger.assert_not_called()
            start_metadata.assert_not_called()
            self._assert_no_runtime_artifacts(home)

    def test_explicit_config_removed_after_parsing_is_not_silently_ignored(self) -> None:
        discovery_calls: list[None] = []
        lifecycle_calls: list[str] = []
        command_calls: list[None] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config = home / "disappearing-config.yml"
            config.write_text("environment: test\n", encoding="utf-8")

            def remove_config(_cwd: Path) -> None:
                discovery_calls.append(None)
                config.unlink()

            def forbidden(name: str):
                def callback(*_args: object) -> object:
                    lifecycle_calls.append(name)
                    raise AssertionError(f"{name} must not run after explicit config disappears")

                return callback

            profile = replace(
                base_cli.CliProfile.generic(discover_project=remove_config),
                resolve_runtime=forbidden("resolve_runtime"),
                history_writer=forbidden("history_writer"),
            )
            app = base_cli.App(name="disappearing-config", profile=profile)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                del ctx
                command_calls.append(None)

            with (
                mock.patch.object(app_module, "configure_logger") as configure_logger,
                mock.patch.object(RunRecorder, "start") as start_metadata,
            ):
                result = invoke(app, ["--config", str(config)], home=home)

            output = _combined_output(result)
            self.assertEqual(result.exit_code, 2, output)
            self.assertIn(f"Config file '{config}' does not exist.", output)
            self.assertEqual(discovery_calls, [None])
            self.assertEqual(lifecycle_calls, [])
            self.assertEqual(command_calls, [])
            configure_logger.assert_not_called()
            start_metadata.assert_not_called()
            self._assert_no_runtime_artifacts(home)

    def test_malformed_explicit_yaml_is_rejected_before_runtime_side_effects(self) -> None:
        self._assert_config_content_rejection(
            contents="broken: [\n",
            expected_message="Config file '{path}' contains invalid YAML:",
        )

    def test_non_mapping_explicit_yaml_is_rejected_before_runtime_side_effects(self) -> None:
        self._assert_config_content_rejection(
            contents="- first\n- second\n",
            expected_message="Config file '{path}' must contain a YAML mapping.",
        )

    def test_invalid_utf8_explicit_config_is_rejected_before_runtime_side_effects(self) -> None:
        self._assert_config_content_rejection(
            contents=b"environment: \xff\xfe\n",
            expected_message="Unable to read config file '{path}':",
        )

    def _assert_config_content_rejection(
        self,
        *,
        contents: str | bytes,
        expected_message: str,
    ) -> None:
        lifecycle_calls: list[str] = []
        command_calls: list[None] = []

        def forbidden(name: str):
            def callback(*_args: object) -> object:
                lifecycle_calls.append(name)
                raise AssertionError(f"{name} must not run after invalid explicit config")

            return callback

        profile = replace(
            base_cli.CliProfile.generic(),
            resolve_runtime=forbidden("resolve_runtime"),
            history_writer=forbidden("history_writer"),
        )
        app = base_cli.App(name="invalid-config-content", profile=profile)

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            command_calls.append(None)

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config = home / "invalid-config.yml"
            if isinstance(contents, bytes):
                config.write_bytes(contents)
            else:
                config.write_text(contents, encoding="utf-8")
            with (
                mock.patch.object(app_module, "configure_logger") as configure_logger,
                mock.patch.object(RunRecorder, "start") as start_metadata,
            ):
                result = invoke(app, ["--config", str(config)], home=home)

            output = _combined_output(result)
            self.assertEqual(result.exit_code, 2, output)
            self.assertIn(expected_message.format(path=config), output)
            self.assertNotIn("Traceback", output)
            self.assertEqual(lifecycle_calls, [])
            self.assertEqual(command_calls, [])
            configure_logger.assert_not_called()
            start_metadata.assert_not_called()
            self._assert_no_runtime_artifacts(home)

    def test_absent_implicit_profile_config_remains_optional(self) -> None:
        seen: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            implicit = home / "optional-profile-config.yml"
            profile = base_cli.CliProfile.generic(
                load_user_config=lambda: load_yaml_file(implicit),
            )
            app = base_cli.App(
                name="optional-implicit-config",
                profile=profile,
                log_to_file=False,
            )

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["user_config"] = ctx.user_config

            result = invoke(app, home=home)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(seen, {"user_config": {}})
            self.assertFalse(implicit.exists())

    def test_explicit_config_validation_preserves_tilde_expansion(self) -> None:
        seen: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            config = home / "explicit-config.yml"
            config.write_text("environment: tilde\n", encoding="utf-8")
            app = base_cli.App(name="tilde-config", log_to_file=False)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["environment"] = ctx.environment

            result = invoke(app, ["--config", "~/explicit-config.yml"], home=home)

            self.assertEqual(result.exit_code, 0, _combined_output(result))
            self.assertEqual(seen, {"environment": "tilde"})


if __name__ == "__main__":
    unittest.main()
