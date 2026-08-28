from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import validate_changelog

VALID_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Add a useful feature.

## [1.0.0] - 2026-08-28

### Fixed

- Repair a user-visible issue.

[Unreleased]: https://github.com/basefoundry/base-cli/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/basefoundry/base-cli/releases/tag/v1.0.0
"""


class ChangelogValidationTests(unittest.TestCase):
    def validate(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CHANGELOG.md"
            path.write_text(text, encoding="utf-8")
            return validate_changelog.validate_changelog(path)

    def test_accepts_valid_changelog(self) -> None:
        self.assertEqual(self.validate(VALID_CHANGELOG), [])

    def test_rejects_duplicate_bullets_and_categories(self) -> None:
        text = VALID_CHANGELOG.replace(
            "- Add a useful feature.",
            "- Add a useful feature.\n- Add a useful feature.",
        ).replace("### Fixed", "### Fixed\n\n- Another fix.\n\n### Fixed")
        errors = self.validate(text)
        self.assertTrue(any("duplicate bullet" in error for error in errors))
        self.assertTrue(any("duplicate 'Fixed'" in error for error in errors))

    def test_rejects_missing_release_link_and_internal_planning_text(self) -> None:
        text = VALID_CHANGELOG.replace(
            "- Add a useful feature.",
            "- Add an implementation plan for an agentic worker.",
        ).replace(
            "[1.0.0]: https://github.com/basefoundry/base-cli/releases/tag/v1.0.0\n",
            "",
        )
        errors = self.validate(text)
        self.assertTrue(any("internal planning text" in error for error in errors))
        self.assertTrue(any("missing release link [1.0.0]" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
