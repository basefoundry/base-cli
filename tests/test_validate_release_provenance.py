from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_release_provenance import validate_release_provenance

SOURCE = "a" * 40
TAG_COMMIT = SOURCE


def valid(**overrides: object) -> list[str]:
    values: dict[str, object] = {
        "event_name": "push",
        "ref_type": "tag",
        "tag": "v1.0.0",
        "publish_target": "",
        "source_commit": SOURCE,
        "tag_type": "tag",
        "resolved_tag_commit": TAG_COMMIT,
        "main_reachable": True,
        "repository_shallow": False,
    }
    values.update(overrides)
    return validate_release_provenance(**values)  # type: ignore[arg-type]


class ReleaseProvenanceValidationTests(unittest.TestCase):
    def test_accepts_annotated_tag_on_main(self) -> None:
        self.assertEqual(valid(), [])

    def test_rejects_lightweight_tag(self) -> None:
        errors = valid(tag_type="commit")
        self.assertTrue(any("annotated tag" in error for error in errors))

    def test_rejects_unmerged_commit(self) -> None:
        errors = valid(main_reachable=False)
        self.assertTrue(any("not reachable" in error for error in errors))

    def test_rejects_moved_or_mismatched_tag(self) -> None:
        errors = valid(resolved_tag_commit="b" * 40)
        self.assertTrue(any("does not resolve" in error for error in errors))

    def test_rejects_forced_tag_update(self) -> None:
        errors = valid(forced=True)
        self.assertTrue(any("forced tag" in error for error in errors))

    def test_rejects_shallow_history(self) -> None:
        errors = valid(repository_shallow=True)
        self.assertTrue(any("shallow" in error for error in errors))

    def test_rejects_pypi_dispatch_from_a_branch(self) -> None:
        errors = valid(event_name="workflow_dispatch", ref_type="branch", publish_target="pypi")
        self.assertTrue(any("requires a version tag" in error for error in errors))

    def test_allows_testpypi_branch_rehearsal_with_full_history(self) -> None:
        self.assertEqual(
            valid(
                event_name="workflow_dispatch",
                ref_type="branch",
                tag="",
                publish_target="testpypi",
                tag_type="",
                resolved_tag_commit="",
                main_reachable=False,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
