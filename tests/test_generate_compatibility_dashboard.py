from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import generate_compatibility_dashboard


class CompatibilityDashboardTests(unittest.TestCase):
    def test_dashboard_contains_declared_and_tested_surfaces(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dashboard = generate_compatibility_dashboard.generate_dashboard(root)
        for expected in (
            "`>=3.10,<4`",
            "`click>=8.1,<8.6`",
            "`PyYAML>=6.0,<7`",
            "`typer>=0.12,<0.28`",
            "8.1, 8.2, 8.3, 8.4, 8.5",
            "0.25.1, 0.26.0, 0.27.1",
            "Debian 12",
            "Fedora latest",
            "WSL2",
        ):
            self.assertIn(expected, dashboard)

    def test_checked_in_dashboard_is_current(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = generate_compatibility_dashboard.generate_dashboard(root)
        actual = (root / "docs/compatibility-dashboard.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
