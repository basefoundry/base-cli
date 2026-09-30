from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import validate_release_provenance as provenance_module
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

    def test_rejects_deleted_tag_event(self) -> None:
        errors = valid(deleted=True)
        self.assertTrue(any("deleted tag" in error for error in errors))

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

    def test_main_requires_event_payload_without_turning_empty_path_into_directory(self) -> None:
        def git_success(*args: str) -> tuple[int, str]:
            if args[:2] == ("cat-file", "-t"):
                return 0, "tag"
            if args[:2] == ("rev-parse", "--verify"):
                return 0, SOURCE
            if args[:2] == ("merge-base", "--is-ancestor"):
                return 0, ""
            if args == ("rev-parse", "--is-shallow-repository"):
                return 0, "false"
            raise AssertionError(args)

        stderr = io.StringIO()
        environment = {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF_TYPE": "tag",
            "GITHUB_REF_NAME": "v1.0.0",
            "GITHUB_SHA": SOURCE,
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(sys, "argv", ["validate_release_provenance.py"]),
            mock.patch.object(provenance_module, "_git", side_effect=git_success),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            provenance_module.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("event payload is required", stderr.getvalue())
        self.assertNotIn("Is a directory", stderr.getvalue())

    def test_main_reads_deleted_event_payload(self) -> None:
        def git_success(*args: str) -> tuple[int, str]:
            if args[:2] == ("cat-file", "-t"):
                return 0, "tag"
            if args[:2] == ("rev-parse", "--verify"):
                return 0, SOURCE
            if args[:2] == ("merge-base", "--is-ancestor"):
                return 0, ""
            if args == ("rev-parse", "--is-shallow-repository"):
                return 0, "false"
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps({"deleted": True}), encoding="utf-8")
            stderr = io.StringIO()
            environment = {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "v1.0.0",
                "GITHUB_SHA": SOURCE,
            }
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(
                    sys,
                    "argv",
                    ["validate_release_provenance.py", "--event-path", str(event_path)],
                ),
                mock.patch.object(provenance_module, "_git", side_effect=git_success),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                provenance_module.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("deleted tag", stderr.getvalue())

    def test_main_fails_closed_when_shallow_state_query_fails(self) -> None:
        def git_failure(*args: str) -> tuple[int, str]:
            if args[:2] == ("cat-file", "-t"):
                return 0, "tag"
            if args[:2] == ("rev-parse", "--verify"):
                return 0, SOURCE
            if args[:2] == ("merge-base", "--is-ancestor"):
                return 0, ""
            if args == ("rev-parse", "--is-shallow-repository"):
                return 128, ""
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            environment = {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "v1.0.0",
                "GITHUB_SHA": SOURCE,
            }
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(
                    sys,
                    "argv",
                    ["validate_release_provenance.py", "--event-path", str(event_path)],
                ),
                mock.patch.object(provenance_module, "_git", side_effect=git_failure),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                provenance_module.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("is-shallow-repository", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
