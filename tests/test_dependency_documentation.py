from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DependencyDocumentationTests(unittest.TestCase):
    def test_click_window_is_consistent_across_metadata_and_public_docs(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        documents = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in (
                "docs/dependency-support.md",
                "docs/platform-support.md",
                "docs/api-stability.md",
                "docs/compatibility-dashboard.md",
            )
        )
        self.assertIn('"click>=8.1,<8.6"', metadata)
        self.assertRegex(documents, re.compile(r"Click `>=8\.1,<8\.6`"))
        self.assertIn("8.1, 8.2, 8.3, 8.4, 8.5", documents)
        self.assertNotIn("Click `>=8.1,<8.5`", documents)


if __name__ == "__main__":
    unittest.main()
