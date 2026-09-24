from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import base_cli
from base_cli.config import load_yaml_file
from base_cli.errors import ConfigurationError
from base_cli.output import OutputFormatError, render_records


class OptionalYamlDependencyTests(unittest.TestCase):
    def test_yaml_output_explains_optional_install_when_yaml_is_missing(self) -> None:
        stream = io.StringIO()
        with mock.patch.dict(sys.modules, {"yaml": None}):
            with self.assertRaisesRegex(OutputFormatError, r"base-cli\[yaml\]"):
                render_records(
                    ({"name": "value"},),
                    requested_format="yaml",
                    columns=(("NAME", "name"),),
                    stream=stream,
                )

    def test_run_app_reports_missing_yaml_as_actionable_usage_error(self) -> None:
        app = base_cli.App(
            name="optional-yaml-output",
            log_to_file=False,
            lifecycle_options=base_cli.LifecycleOptions(json=base_cli.LifecycleOption("--json")),
        )

        @app.command()
        def main(ctx: base_cli.Context) -> None:
            del ctx
            render_records(
                ({"name": "value"},),
                requested_format="yaml",
                columns=(("NAME", "name"),),
            )

        for args in ([], ["--json"]):
            with self.subTest(json=args == ["--json"]), tempfile.TemporaryDirectory() as home:
                with mock.patch.dict(sys.modules, {"yaml": None}):
                    result = base_cli.testing.invoke(app, args, home=Path(home))

                self.assertEqual(result.exit_code, base_cli.ExitCode.USAGE_ERROR)
                if args:
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["code"], "output_format_error")
                    self.assertEqual(payload["details"]["exit_code"], base_cli.ExitCode.USAGE_ERROR)
                    self.assertIn("base-cli[yaml]", payload["message"])
                    self.assertEqual(result.stderr, "")
                else:
                    self.assertEqual(result.stdout, "")
                    self.assertIn("Error: PyYAML is required", result.stderr)
                    self.assertIn("base-cli[yaml]", result.stderr)

    def test_yaml_config_explains_optional_install_when_yaml_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            path.write_text("answer: 42\n", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"yaml": None}):
                with self.assertRaisesRegex(ConfigurationError, r"base-cli\[yaml\]"):
                    load_yaml_file(path, required=True)

    def test_core_facade_import_does_not_import_yaml(self) -> None:
        self.assertIn("base_cli", sys.modules)
        self.assertTrue(hasattr(base_cli, "App"))


if __name__ == "__main__":
    unittest.main()
