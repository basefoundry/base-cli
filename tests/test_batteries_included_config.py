from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import base_cli
from base_cli.config import BatteriesIncludedConfigLoader, ConfigSnapshot, _merge_mapping
from base_cli.testing import invoke


def _write_yaml(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


class BatteriesIncludedConfigTests(unittest.TestCase):
    def test_nested_merge_provenance_keeps_repeated_leaf_names_scoped(self) -> None:
        values: dict[str, object] = {}
        provenance: dict[str, str] = {}

        _merge_mapping(values, provenance, {"host": "top", "db": {"host": "one"}}, "user")
        _merge_mapping(values, provenance, {"db": {"host": "two", "tls": {"enabled": True}}}, "project")

        self.assertEqual(values, {"host": "top", "db": {"host": "two", "tls": {"enabled": True}}})
        self.assertEqual(
            provenance,
            {"host": "user", "db.host": "project", "db.tls.enabled": "project"},
        )

    def test_layered_loader_merges_in_documented_order_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            user_dir = root / "user" / "tool"
            project = root / "project"
            explicit = root / "explicit.yaml"
            _write_yaml(
                user_dir / "config.yaml",
                "environment: staging\nshared: user\nnested:\n  user: true\n",
            )
            _write_yaml(
                project / ".base-cli.yaml",
                "shared: project\nnested:\n  project: true\n",
            )
            _write_yaml(
                user_dir / "environments" / "staging.yaml",
                "shared: user-environment\nnested:\n  user_environment: true\n",
            )
            _write_yaml(
                project / "environments" / "staging.yaml",
                "shared: project-environment\nnested:\n  project_environment: true\n",
            )
            _write_yaml(
                explicit,
                "shared: explicit\nnested:\n  explicit: true\n",
            )

            snapshot = BatteriesIncludedConfigLoader(
                "tool",
                user_config_dir=user_dir,
            ).load(project, explicit)

        self.assertIsInstance(snapshot, ConfigSnapshot)
        self.assertEqual(snapshot.framework.environment, "staging")
        self.assertEqual(snapshot.config["shared"], "explicit")
        self.assertEqual(
            snapshot.config["nested"],
            {
                "user": True,
                "project": True,
                "user_environment": True,
                "project_environment": True,
                "explicit": True,
            },
        )
        self.assertEqual(snapshot.provenance["shared"], "explicit")
        self.assertEqual(snapshot.provenance["nested.user"], "user")
        self.assertEqual(snapshot.provenance["nested.project_environment"], "project:environment:staging")
        self.assertNotIn("environment", snapshot.config)

    def test_cli_environment_selects_environment_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            user_dir = root / "user-config" / "tool"
            _write_yaml(user_dir / "config.yaml", "environment: dev\n")
            _write_yaml(
                user_dir / "environments" / "prod.yaml",
                "log_level: debug\nkeep_temp: true\nanswer: 42\n",
            )
            seen: dict[str, object] = {}
            app = base_cli.App(
                name="tool",
                profile=base_cli.CliProfile.batteries_included(
                    "tool",
                    user_config_dir=user_dir,
                ),
                log_to_file=False,
            )

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["environment"] = ctx.environment
                seen["debug"] = ctx.debug
                seen["keep_temp"] = ctx.keep_temp
                seen["config"] = ctx.config
                seen["provenance"] = dict(ctx.config_provenance)

            result = invoke(app, ["--environment", "prod"], home=root / "home")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen["environment"], "prod")
        self.assertTrue(seen["debug"])
        self.assertTrue(seen["keep_temp"])
        self.assertEqual(seen["config"], {"answer": 42})
        self.assertEqual(seen["provenance"]["answer"], "user:environment:prod")

    def test_missing_optional_layers_are_empty_but_explicit_paths_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            loader = BatteriesIncludedConfigLoader(
                "tool",
                user_config_dir=root / "missing-user",
            )
            snapshot = loader.load(None, None)
            self.assertEqual(snapshot.config, {})
            self.assertEqual(snapshot.framework.environment, "dev")
            with self.assertRaisesRegex(base_cli.ConfigurationError, "does not exist"):
                loader.load(None, root / "missing-explicit.yaml")

    def test_environment_and_layer_names_cannot_escape_config_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            loader = BatteriesIncludedConfigLoader("tool", user_config_dir=root / "user")
            with self.assertRaisesRegex(base_cli.ConfigurationError, "environment"):
                loader.load(None, None, environment="../secret")
            with self.assertRaisesRegex(ValueError, "project_config_name"):
                BatteriesIncludedConfigLoader(
                    "tool",
                    user_config_dir=root / "user",
                    project_config_name="../project.yaml",
                )

    def test_framework_settings_are_validated_and_separated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            explicit = root / "config.yaml"
            _write_yaml(explicit, "environment: prod\nlog_level: verbose\n")
            loader = BatteriesIncludedConfigLoader("tool", user_config_dir=root / "user")
            with self.assertRaisesRegex(base_cli.ConfigurationError, "log_level"):
                loader.load(None, explicit)

            _write_yaml(explicit, "environment: prod\nkeep_temp: maybe\n")
            with self.assertRaisesRegex(base_cli.ConfigurationError, "keep_temp"):
                loader.load(None, explicit)

    def test_batteries_included_profile_discovers_project_config_upward(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "project"
            nested = project / "src" / "tool"
            nested.mkdir(parents=True)
            _write_yaml(project / ".base-cli.yaml", "answer: project\n")
            seen: dict[str, object] = {}
            app = base_cli.App(
                name="tool",
                profile=base_cli.CliProfile.batteries_included(
                    "tool",
                    user_config_dir=root / "user-config" / "tool",
                ),
                log_to_file=False,
            )

            @app.command()
            def main(ctx: base_cli.Context) -> None:
                seen["project_root"] = ctx.project_root
                seen["config"] = ctx.config

            result = invoke(app, [], cwd=nested, home=root / "home")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(seen["project_root"], project.resolve())
        self.assertEqual(seen["config"], {"answer": "project"})


if __name__ == "__main__":
    unittest.main()
