from __future__ import annotations

import io
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
