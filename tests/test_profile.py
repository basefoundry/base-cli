from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import base_cli
from base_cli.testing import invoke


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class GenericProfileTests(unittest.TestCase):
    def test_generic_profile_has_no_base_runtime_defaults(self) -> None:
        seen: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache = root / "cache"
            project = root / "project"
            project.mkdir()

            app = base_cli.App(
                name="plain-tool",
                profile=base_cli.CliProfile.generic(cache_root=cache),
            )

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["project_root"] = ctx.project_root
                seen["manifest_path"] = ctx.manifest_path
                seen["runtime_owner"] = ctx.runtime_owner
                seen["history_scope"] = ctx.history_scope
                seen["cache_dir"] = ctx.cache_dir

            result = invoke(
                app,
                home=home,
                cwd=project,
                env={
                    "BASE_CLI_PRIMARY_LOG": str(root / "base.log"),
                    "BASE_CLI_HISTORY_SCOPE": "internal",
                },
            )

            self.assertEqual(result.exit_code, 0, f"{result.output} {result.exception!r}")
            self.assertIsNone(app.profile.history_writer)
            self.assertIsNone(app.profile.display_command())
            self.assertIsNone(seen["project_root"])
            self.assertIsNone(seen["manifest_path"])
            self.assertEqual(seen["runtime_owner"], "default")
            self.assertEqual(seen["history_scope"], "primary")
            self.assertTrue(Path(seen["cache_dir"]).resolve().is_relative_to(cache.resolve()))

    def test_app_defaults_to_generic_profile(self) -> None:
        app = base_cli.App(name="plain-tool", log_to_file=False)

        self.assertIsNone(app.profile.history_writer)
        self.assertIsNone(app.profile.display_command())
        self.assertEqual(app.profile.resolve_runtime("plain-tool", None).runtime_owner, "default")

    def test_generic_profile_accepts_consumer_project_and_config_policies(self) -> None:
        seen: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            cache = root / "cache"
            project = root / "project"
            manifest = project / "tool.manifest"
            project.mkdir()
            manifest.write_text("name: demo\n", encoding="utf-8")

            def discover(_cwd: Path) -> base_cli.ProjectInfo:
                return base_cli.ProjectInfo(
                    root=project,
                    manifest=manifest,
                    name="demo",
                )

            def load_config(
                project_info: base_cli.ProjectInfo | None,
                explicit: Path | None,
            ) -> dict[str, object]:
                return {
                    "project": project_info.name if project_info is not None else None,
                    "explicit": str(explicit) if explicit is not None else None,
                }

            profile = base_cli.CliProfile.generic(
                cache_root=cache,
                discover_project=discover,
                load_config=load_config,
            )
            app = base_cli.App(name="policy-tool", profile=profile, log_to_file=False)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["project_root"] = ctx.project_root
                seen["manifest_path"] = ctx.manifest_path
                seen["project_name"] = ctx.project_name
                seen["config"] = ctx.config

            result = invoke(app, home=home, cwd=project)

        self.assertEqual(result.exit_code, 0, f"{result.output} {result.exception!r}")
        self.assertEqual(seen["project_root"], project)
        self.assertEqual(seen["manifest_path"], manifest)
        self.assertEqual(seen["project_name"], "demo")
        self.assertEqual(seen["config"], {"project": "demo", "explicit": None})

    def test_generic_profile_accepts_consumer_history_display_policy(self) -> None:
        formatter = lambda cli_name, argv: f"tool {cli_name} {' '.join(argv)}"  # noqa: E731
        profile = base_cli.CliProfile.generic(history_display_command=formatter)

        self.assertIs(profile.history_display_command, formatter)
