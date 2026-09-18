from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import base_cli
from base_cli.testing import invoke

_LEVELS = ("debug", "info", "warning", "error", "critical")
_EXPECTED_LEVELS = {
    "debug": ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    "info": ("INFO", "WARNING", "ERROR", "CRITICAL"),
    "warning": ("WARNING", "ERROR", "CRITICAL"),
    "error": ("ERROR", "CRITICAL"),
    "critical": ("CRITICAL",),
}


def _config_profile(root: Path, *, log_level: str, keep_temp: bool = False) -> base_cli.CliProfile:
    config_dir = root / "config" / "tool"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        f"log_level: {log_level}\nkeep_temp: {str(keep_temp).lower()}\n",
        encoding="utf-8",
    )
    return base_cli.CliProfile.batteries_included("tool", user_config_dir=config_dir)


def _emit_test_logs(context: base_cli.Context[Any, Any, Any]) -> None:
    for level in _LEVELS:
        getattr(context.log, level)(f"{level}-marker")


def _native_log_callback(seen: list[bool]) -> Any:
    def main(ctx: base_cli.Context[Any, Any, Any]) -> None:
        seen.append(ctx.debug)
        _emit_test_logs(ctx)

    return main


def _attached_log_callback(seen: list[bool]) -> Any:
    def main() -> None:
        context = base_cli.get_current_context()
        seen.append(context.debug)
        _emit_test_logs(context)

    return main


def _retention_callback(observed: dict[str, Any]) -> Any:
    def status(ctx: base_cli.Context[Any, Any, Any]) -> None:
        observed["debug"] = ctx.debug
        observed["keep_temp"] = ctx.keep_temp
        observed["temp_dir"] = ctx.temp_dir
        (ctx.temp_dir / "marker").write_text("retention marker", encoding="utf-8")

    return status


def _json_marker_levels(stderr: str) -> list[str]:
    return [
        str(payload["level"])
        for line in stderr.splitlines()
        if line.strip()
        for payload in (json.loads(line),)
        if str(payload.get("message", "")).endswith("-marker")
    ]


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class FrameworkConfigRuntimeTests(unittest.TestCase):
    def test_all_configured_levels_filter_native_text_and_json_logs(self) -> None:
        for configured_level in _LEVELS:
            for json_mode in (False, True):
                with self.subTest(level=configured_level, json=json_mode), tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    seen: list[bool] = []
                    options = base_cli.LifecycleOptions(
                        json=base_cli.LifecycleOption("--json") if json_mode else None,
                    )
                    app = base_cli.App(
                        name="tool",
                        profile=_config_profile(root, log_level=configured_level),
                        lifecycle_options=options,
                        log_to_file=False,
                    )

                    app.command()(_native_log_callback(seen))

                    args = ["--json"] if json_mode else []
                    result = invoke(app, args, home=root / "home")

                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(seen, [configured_level == "debug"])
                    if json_mode:
                        self.assertEqual(_json_marker_levels(result.stderr), list(_EXPECTED_LEVELS[configured_level]))
                    else:
                        for level in _LEVELS:
                            self.assertEqual(
                                f"{level}-marker" in result.stderr,
                                level.upper() in _EXPECTED_LEVELS[configured_level],
                                result.stderr,
                            )

    @unittest.skipUnless(importlib.util.find_spec("typer"), "Typer is not installed")
    def test_all_configured_levels_filter_attached_typer_text_and_json_logs(self) -> None:
        import typer

        for configured_level in _LEVELS:
            for json_mode in (False, True):
                with self.subTest(level=configured_level, json=json_mode), tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    seen: list[bool] = []
                    typer_app = typer.Typer()

                    typer_app.command()(_attached_log_callback(seen))

                    command = base_cli.attach_typer(
                        typer_app,
                        name="tool",
                        profile=_config_profile(root, log_level=configured_level),
                        lifecycle_options=base_cli.LifecycleOptions(
                            json=base_cli.LifecycleOption("--json") if json_mode else None,
                        ),
                        log_to_file=False,
                    )
                    args = ["--json"] if json_mode else []
                    result = invoke(command, args, home=root / "home")

                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(seen, [configured_level == "debug"])
                    if json_mode:
                        self.assertEqual(_json_marker_levels(result.stderr), list(_EXPECTED_LEVELS[configured_level]))
                    else:
                        for level in _LEVELS:
                            self.assertEqual(
                                f"{level}-marker" in result.stderr,
                                level.upper() in _EXPECTED_LEVELS[configured_level],
                                result.stderr,
                            )

    def test_configured_stream_filter_does_not_reduce_persistent_debug_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            seen: dict[str, Any] = {}
            app = base_cli.App(
                name="tool",
                profile=_config_profile(root, log_level="critical"),
            )

            @app.command()
            def main(ctx: base_cli.Context[Any, Any, Any]) -> None:
                seen["log_file"] = ctx.log_file
                ctx.log.debug("persistent-debug-marker")
                ctx.log.critical("critical-stream-marker")

            result = invoke(app, home=root / "home")
            log_text = Path(seen["log_file"]).read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("persistent-debug-marker", result.stderr)
        self.assertIn("critical-stream-marker", result.stderr)
        self.assertIn("persistent-debug-marker", log_text)

    def test_quiet_raises_configured_debug_stream_to_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            seen: list[tuple[bool, bool]] = []
            app = base_cli.App(
                name="tool",
                profile=_config_profile(root, log_level="debug"),
                log_to_file=False,
            )

            def quiet_callback(ctx: base_cli.Context[Any, Any, Any]) -> None:
                seen.append((ctx.debug, ctx.quiet))
                _emit_test_logs(ctx)

            app.command()(quiet_callback)

            result = invoke(app, ["--quiet"], home=root / "home")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen, [(True, True)])
        for level in _LEVELS:
            self.assertEqual(
                f"{level}-marker" in result.stderr,
                level.upper() in _EXPECTED_LEVELS["warning"],
                result.stderr,
            )

    def test_explicit_negative_flags_override_config_and_cleanup_metadata(self) -> None:
        for case, args, expected_debug, expected_keep in (
            (
                "root negative values override true config",
                ["--no-debug", "--no-keep-temp", "status"],
                False,
                False,
            ),
            (
                "leaf positive values override root negative values",
                ["--no-debug", "--no-keep-temp", "status", "--debug", "--keep-temp"],
                True,
                True,
            ),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                observed: dict[str, Any] = {}
                app = base_cli.App(
                    name="tool",
                    profile=_config_profile(root, log_level="debug", keep_temp=True),
                    lifecycle_options=base_cli.LifecycleOptions(
                        debug=base_cli.LifecycleOption("--debug/--no-debug"),
                        keep_temp=base_cli.LifecycleOption("--keep-temp/--no-keep-temp"),
                    ),
                )

                app.subcommand("status")(_retention_callback(observed))

                result = invoke(app, args, home=root / "home")
                metadata_files = list((root / "home" / ".cache").glob("**/run.json"))

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(observed["debug"], expected_debug)
                self.assertEqual(observed["keep_temp"], expected_keep)
                self.assertEqual(len(metadata_files), 1)
                metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
                self.assertEqual(metadata["preserve"], expected_keep)
                temp_dir = Path(observed["temp_dir"])
                marker = temp_dir / "marker"
                if expected_keep:
                    self.assertTrue(marker.exists())
                elif marker.exists():
                    # Platforms without safe directory-handle cleanup fail closed,
                    # so a false keep-temp value is recorded but cleanup is warned.
                    self.assertIn("Temp directory cleanup failed", result.stderr)

    def test_config_overrides_callable_defaults_but_environment_and_default_map_override_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            observed: list[tuple[bool, bool]] = []
            options = base_cli.LifecycleOptions(
                debug=base_cli.LifecycleOption("--debug/--no-debug", envvar="TOOL_DEBUG", default=lambda: True),
                keep_temp=base_cli.LifecycleOption(
                    "--keep-temp/--no-keep-temp",
                    envvar="TOOL_KEEP_TEMP",
                    default=lambda: True,
                ),
            )
            app = base_cli.App(
                name="tool",
                profile=_config_profile(root, log_level="error", keep_temp=False),
                lifecycle_options=options,
                log_to_file=False,
            )

            @app.command()
            def main(ctx: base_cli.Context[Any, Any, Any]) -> None:
                observed.append((ctx.debug, ctx.keep_temp))

            default_result = invoke(app, home=root / "default-home")
            self.assertEqual(default_result.exit_code, 0, default_result.output)
            self.assertEqual(observed, [(False, False)])

            env_result = invoke(
                app,
                home=root / "env-home",
                env={"TOOL_DEBUG": "1", "TOOL_KEEP_TEMP": "1"},
            )
            self.assertEqual(env_result.exit_code, 0, env_result.output)
            self.assertEqual(observed[-1], (True, True))

            command = app.click_command
            command.context_settings["default_map"] = {"debug": True, "keep_temp": True}
            map_result = invoke(app, home=root / "map-home")
            self.assertEqual(map_result.exit_code, 0, map_result.output)
            self.assertEqual(observed[-1], (True, True))

            config_file = root / "config" / "tool" / "config.yaml"
            config_file.write_text("log_level: debug\nkeep_temp: true\n", encoding="utf-8")
            false_env_result = invoke(
                app,
                home=root / "false-env-home",
                env={"TOOL_DEBUG": "0", "TOOL_KEEP_TEMP": "0"},
            )
            self.assertEqual(false_env_result.exit_code, 0, false_env_result.output)
            self.assertEqual(observed[-1], (False, False))

            map_app = base_cli.App(
                name="tool",
                profile=_config_profile(root, log_level="debug", keep_temp=True),
                lifecycle_options=base_cli.LifecycleOptions(
                    debug=base_cli.LifecycleOption("--debug/--no-debug"),
                    keep_temp=base_cli.LifecycleOption("--keep-temp/--no-keep-temp"),
                ),
                log_to_file=False,
            )

            @map_app.command()
            def map_main(ctx: base_cli.Context[Any, Any, Any]) -> None:
                observed.append((ctx.debug, ctx.keep_temp))

            map_app.click_command.context_settings["default_map"] = {"debug": False, "keep_temp": False}
            false_map_result = invoke(map_app, home=root / "false-map-home")
            self.assertEqual(false_map_result.exit_code, 0, false_map_result.output)
            self.assertEqual(observed[-1], (False, False))


if __name__ == "__main__":
    unittest.main()
