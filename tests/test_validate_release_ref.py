from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import validate_release_ref

VALID_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Continue improvements.

## [1.2.3] - 2026-08-28

### Fixed

- Repair the release workflow.
"""


class ReleaseReferenceValidationTests(unittest.TestCase):
    def validate(self, version: str, changelog: str, tag: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version_path = root / "VERSION"
            changelog_path = root / "CHANGELOG.md"
            version_path.write_text(version, encoding="utf-8")
            changelog_path.write_text(changelog, encoding="utf-8")
            return validate_release_ref.validate_release_ref(version_path, changelog_path, tag)

    def test_accepts_matching_version_and_release_notes(self) -> None:
        self.assertEqual(self.validate("1.2.3\n", VALID_CHANGELOG, "v1.2.3"), [])

    def test_rejects_mismatched_version(self) -> None:
        errors = self.validate("1.2.2\n", VALID_CHANGELOG, "v1.2.3")
        self.assertTrue(any("VERSION declares" in error for error in errors))

    def test_rejects_missing_release_notes(self) -> None:
        changelog = VALID_CHANGELOG.replace("- Repair the release workflow.\n", "")
        errors = self.validate("1.2.3\n", changelog, "v1.2.3")
        self.assertTrue(any("no release-note bullets" in error for error in errors))

    def test_rejects_undated_or_missing_section(self) -> None:
        changelog = VALID_CHANGELOG.replace("## [1.2.3] - 2026-08-28", "## [1.2.3]")
        errors = self.validate("1.2.3\n", changelog, "v1.2.3")
        self.assertTrue(any("missing a dated release section" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
