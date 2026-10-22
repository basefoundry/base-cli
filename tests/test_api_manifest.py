from __future__ import annotations

import json
import unittest
from pathlib import Path

import base_cli
import base_cli.core as core


class ApiManifestTests(unittest.TestCase):
    def test_manifest_partitions_the_facade(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "docs" / "api-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tiers = manifest["tiers"]
        names = [name for tier in tiers.values() for name in tier]

        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(base_cli.__all__))
        self.assertEqual(set(core.__all__), set(tiers["core"]))
        self.assertTrue(all(hasattr(base_cli, name) for name in names))

    def test_core_surface_has_no_optional_integration_imports(self) -> None:
        import sys

        self.assertNotIn("typer", sys.modules)
        self.assertNotIn("rich", sys.modules)
        self.assertNotIn("opentelemetry", sys.modules)

        self.assertEqual(core.__all__[0], "App")
        self.assertIn("run_app", core.__all__)


if __name__ == "__main__":
    unittest.main()
