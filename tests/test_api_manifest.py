from __future__ import annotations

import json
import os
import subprocess
import sys
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
        self.assertTrue(all(getattr(core, name) is getattr(base_cli, name) for name in tiers["core"]))

    def test_core_surface_has_no_optional_integration_imports(self) -> None:
        package_root = Path(__file__).resolve().parents[1] / "lib" / "python"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_root)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import base_cli.core; "
                "assert all(name not in sys.modules for name in ('typer', 'rich', 'opentelemetry'))",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

        self.assertIn("App", core.__all__)
        self.assertIn("run_app", core.__all__)


if __name__ == "__main__":
    unittest.main()
