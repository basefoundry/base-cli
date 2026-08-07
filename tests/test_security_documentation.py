from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SecurityDocumentationTests(unittest.TestCase):
    def test_security_policy_defines_private_reporting_support_and_disclosure(self) -> None:
        policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

        for phrase in (
            "Private Vulnerability Reporting",
            "Supported versions",
            "3 business days",
            "90-day coordinated disclosure",
            "Reporter credit",
            "runtime threat model",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, policy)

    def test_threat_model_covers_required_assets_boundaries_and_responsibilities(self) -> None:
        model = (ROOT / "docs" / "security-threat-model.md").read_text(encoding="utf-8").casefold()

        for phrase in (
            "argv",
            "environment",
            "configuration",
            "logs",
            "history",
            "symlink",
            "permissions",
            "plugins",
            "concurrency",
            "inherited",
            "telemetry",
            "non-goals",
            "consumer responsibilities",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, model)

    def test_security_review_maps_threats_to_tests_and_controls(self) -> None:
        review = (ROOT / "docs" / "security-review.md").read_text(encoding="utf-8")

        for phrase in (
            "Threat-to-control map",
            "tests/test_redaction_security.py",
            "tests/test_cleanup_security.py",
            "tests/test_extensions.py",
            "tests/test_integrations.py",
            "bandit",
            "pip-audit --strict",
            "Reviewer sign-off",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, review)


if __name__ == "__main__":
    unittest.main()
