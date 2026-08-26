from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import validate_docs


class DocumentationValidationTests(unittest.TestCase):
    def test_finds_markdown_outside_declared_public_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "docs").mkdir()
            (root / "docs" / "index.md").write_text("# Public\n", encoding="utf-8")
            (root / "docs" / "accidental.md").write_text("# Unnaved\n", encoding="utf-8")
            (root / "docs" / "internal").mkdir()
            (root / "docs" / "internal" / "plan.md").write_text("# Internal\n", encoding="utf-8")

            unexpected = validate_docs.find_unexpected_docs(root)
            with self.assertRaises(SystemExit):
                validate_docs.validate_links(root)

        self.assertEqual(
            unexpected,
            [Path("docs/accidental.md"), Path("docs/internal/plan.md")],
        )


if __name__ == "__main__":
    unittest.main()
