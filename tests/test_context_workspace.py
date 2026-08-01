from __future__ import annotations

import importlib.util
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import base_cli


@dataclass(frozen=True)
class ConsumerSettings:
    workspace_root: Path | None


def configured_app(workspace: Path | None, **kwargs: object) -> base_cli.App:
    settings = ConsumerSettings(workspace.resolve() if workspace is not None else None)
    profile = base_cli.CliProfile.generic(
        load_user_config=lambda: settings,
        resolve_workspace_root=lambda value: value.workspace_root if isinstance(value, ConsumerSettings) else None,
    )
    return base_cli.App(profile=profile, **kwargs)


@unittest.skipUnless(importlib.util.find_spec("click"), "Click is not installed")
class ContextWorkspaceRootTests(unittest.TestCase):
    def test_context_exposes_workspace_root_when_configured(self) -> None:
        seen: dict[str, Path | None] = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            home = root / "home"
            workspace = root / "workspace"
            workspace.mkdir()
            app = configured_app(workspace, name="workspace-root-configured", log_to_file=False)

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["workspace_root"] = ctx.workspace_root
                seen["user_config"] = ctx.user_config

            from base_cli.testing import invoke

            result = invoke(app, [], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen["workspace_root"], workspace.resolve())
        self.assertEqual(seen["user_config"], ConsumerSettings(workspace.resolve()))

    def test_context_workspace_root_is_none_without_configured_root(self) -> None:
        app = configured_app(None, name="workspace-root-default", log_to_file=False)
        seen: dict[str, Path | None] = {}

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            seen["workspace_root"] = ctx.workspace_root
            seen["user_config"] = ctx.user_config

        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)

            from base_cli.testing import invoke

            result = invoke(app, [], home=home)

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNone(seen["workspace_root"])
        self.assertEqual(seen["user_config"], ConsumerSettings(None))
