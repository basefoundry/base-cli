#!/usr/bin/env python3
"""Execute the actual checkout-free workflow with a stateful REST boundary fake."""

import contextlib
import copy
import io
import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/project-intake.yml").read_text()


def extract_reconcile_script(workflow):
    """Read the named step's literal block without adding a YAML dependency."""
    lines = workflow.splitlines(keepends=True)
    step_marker = "      - name: Reconcile Project item"
    matches = [index for index, line in enumerate(lines) if line.rstrip() == step_marker]
    if len(matches) != 1:
        raise AssertionError("Expected exactly one workflow step named Reconcile Project item.")
    start = matches[0] + 1
    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index].strip() and not lines[index].startswith("        ")
        ),
        len(lines),
    )
    step = lines[start:end]
    run_markers = [index for index, line in enumerate(step) if line.rstrip() == "        run: |"]
    if len(run_markers) != 1:
        raise AssertionError("Reconcile Project item must contain exactly one literal run: | block.")
    start = run_markers[0] + 1
    end = next(
        (
            index
            for index in range(start, len(step))
            if step[index].strip() and not step[index].startswith("          ")
        ),
        len(step),
    )
    script = textwrap.dedent("".join(step[start:end])).strip()
    if not script:
        raise AssertionError("Reconcile Project item has an empty run block.")
    return script + "\n"


SCRIPT = extract_reconcile_script(WORKFLOW)
DEFAULTS = {"Status": "Backlog", "Priority": "P2", "Size": "S", "Area": "Product", "Initiative": "Adoption Polish"}


class RestFixture:
    def __init__(self):
        self.fields = []
        for field_id, (name, default) in enumerate(DEFAULTS.items(), 1):
            options = [default]
            if name == "Status":
                options += ["Done", "In Progress", "In Review"]
            else:
                options += ["Custom"]
            self.fields.append(
                {
                    "id": field_id,
                    "name": name,
                    "data_type": "single_select",
                    "options": [{"id": label, "name": {"raw": label}} for label in options],
                }
            )
        self.current = {}
        self.exists = True
        self.closed = False
        self.duplicate = False
        self.delayed = 0
        self.delayed_search = 0
        self.mismatch = False
        self.wrong_identity = False
        self.calls = []
        self.failures = []
        self.direct_add = False
        self.stale_readback = 0
        self.patch_failure = False
        self.concurrent_status = ""
        self.filtered_search_empty = False
        self.item_read_failure = ""

    def item(self):
        return {
            "id": 101,
            "content_type": "Issue",
            "content": {"id": 42 if not self.wrong_identity else 99},
            "fields": [{"id": key, "value": {"id": value}} for key, value in self.current.items()],
        }

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        # GraphQL is unavailable in every scenario, including the happy path.
        if command[:2] != ["gh", "api"] or "graphql" in command:
            return subprocess.CompletedProcess(command, 1, "", "GraphQL quota exhausted")
        method, endpoint = command[3:5]
        self.assert_safe_request(command, kwargs)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
            if failure:
                return subprocess.CompletedProcess(command, 1, "", failure)
        result = None
        if endpoint.endswith("projectsV2/14"):
            result = {"title": "base-cli"}
        elif endpoint == "repos/basefoundry/base-cli/issues":
            result = [[{"number": 495, "id": 42, "state": "open"}, {"number": 496, "pull_request": {}}]]
        elif endpoint == "repos/basefoundry/base-cli/issues/495":
            result = {"id": 42, "state": "closed" if self.closed else "open"}
        elif endpoint.endswith("/fields"):
            # Both fields and item discovery must work beyond the first page.
            result = [self.fields[:2], self.fields[2:]]
        elif endpoint.endswith("/items"):
            if method == "POST":
                self.exists = True
                if self.duplicate:
                    return subprocess.CompletedProcess(
                        command, 1, "", "Content already exists in this project (HTTP 422)"
                    )
                result = self.item() if self.direct_add else {"value": self.item()}
            elif self.filtered_search_empty and any(arg.startswith("q=") for arg in command):
                result = [[]]
            elif not self.exists or self.delayed_search:
                self.delayed_search = max(0, self.delayed_search - 1)
                result = [[]]
            else:
                result = [
                    [
                        {
                            "id": 100,
                            "content_type": "Issue",
                            "content": {
                                "id": 41,
                                "number": 495,
                                "repository_url": "https://api.github.com/repos/another/repo",
                            },
                        },
                        {"id": 102, "content_type": "DraftIssue", "content": {"id": 42}},
                        {"id": 103, "content_type": "Issue", "content": None},
                    ],
                    [self.item()],
                ]
        elif endpoint.endswith("/items/101"):
            if method == "PATCH":
                if self.patch_failure:
                    return subprocess.CompletedProcess(command, 1, "", "Forbidden (HTTP 403)")
                if not self.mismatch:
                    for field in json.loads(kwargs["input"])["fields"]:
                        self.current[field["id"]] = field["value"]
                if self.concurrent_status:
                    self.current[1] = self.concurrent_status
                result = self.item()
            elif self.item_read_failure:
                return subprocess.CompletedProcess(command, 1, "", self.item_read_failure)
            elif self.delayed:
                self.delayed -= 1
                return subprocess.CompletedProcess(command, 1, "", "Not Found (HTTP 404)")
            elif self.stale_readback and any(c[0][3] == "PATCH" for c in self.calls):
                self.stale_readback -= 1
                result = {**self.item(), "fields": []}
            else:
                result = self.item()
        else:
            raise AssertionError(f"Unexpected REST request: {command}")
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "nonfatal gh notice")

    @staticmethod
    def assert_safe_request(command, kwargs):
        assert kwargs["timeout"] == 60
        assert kwargs["capture_output"] is True
        assert not kwargs.get("shell")
        if command[3] == "GET" and command[4].endswith(("/fields", "/items")):
            assert "--paginate" in command and "--slurp" in command


class ProjectIntakeTests(unittest.TestCase):
    def execute(self, fixture=None, **overrides):
        fixture = fixture or RestFixture()
        env = {
            "GH_TOKEN": "test-only",
            "GITHUB_REPOSITORY": "basefoundry/base-cli",
            "BASE_PROJECT_OWNER": "basefoundry",
            "BASE_PROJECT_TITLE": "base-cli",
            "BASE_PROJECT_NUMBER": "14",
            "BASE_PROJECT_ISSUE_NUMBER": "495",
            "BASE_PROJECT_DEFAULT_CLOSED_STATUS": "Done",
        }
        env.update(
            {f"BASE_PROJECT_DEFAULT_{name.upper()}": value for name, value in DEFAULTS.items() if name != "Status"}
        )
        env["BASE_PROJECT_DEFAULT_OPEN_STATUS"] = "Backlog"
        env.update(overrides)
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            patch("subprocess.run", fixture.run),
            patch("time.sleep") as sleep,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = 0
            try:
                exec(compile(SCRIPT, "project-intake.yml", "exec"), {"__name__": "__main__"})
            except SystemExit as error:
                status = error.code
        return status, stdout.getvalue(), stderr.getvalue(), sleep

    def test_hourly_sweep_recovers_an_event_without_an_item(self):
        fixture = RestFixture()
        fixture.exists = False
        status, stdout, stderr, _ = self.execute(fixture, GITHUB_EVENT_NAME="schedule", BASE_PROJECT_ISSUE_NUMBER="")
        self.assertEqual(status, 0, stderr)
        self.assertIn("verified all five fields", stdout)
        self.assertTrue(fixture.exists)
        self.assertEqual(fixture.current, {i: v for i, v in enumerate(DEFAULTS.values(), 1)})

    def test_primary_reset_headers_defer_and_redact_token(self):
        fixture = RestFixture()
        fixture.failures = ["rate limit (HTTP 403) x-ratelimit-reset: 9999999999 token-secret"]
        status, stdout, stderr, sleep = self.execute(fixture, GH_TOKEN="token-secret")
        self.assertEqual(status, 0)
        self.assertIn("status: deferred", stdout)
        self.assertNotIn("token-secret", stdout + stderr)
        sleep.assert_not_called()

    def test_partial_patch_failure_recovers_on_rerun(self):
        fixture = RestFixture()
        fixture.current = {1: "In Progress", 2: "Custom"}
        fixture.patch_failure = True
        self.assertEqual(self.execute(fixture)[0], 1)
        fixture.patch_failure = False
        status, stdout, stderr, _ = self.execute(fixture)
        self.assertEqual(status, 0, stderr)
        self.assertEqual(fixture.current[1], "In Progress")
        self.assertEqual(fixture.current[2], "Custom")
        self.assertIn("verified all five fields", stdout)

    def test_extraction_ignores_neighboring_run_steps_and_metadata(self):
        before = "      - name: Setup\n        run: |\n          echo setup\n"
        after = "        env:\n          EXAMPLE: value\n      - name: Finish\n        run: |\n          echo finish\n"
        workflow = WORKFLOW.replace("    steps:\n", "    steps:\n" + before) + "\n" + after
        self.assertEqual(extract_reconcile_script(workflow), SCRIPT)

    def test_extraction_reports_missing_or_duplicate_named_step(self):
        for workflow in (
            WORKFLOW.replace("name: Reconcile Project item", "name: Renamed"),
            WORKFLOW + "\n      - name: Reconcile Project item\n",
        ):
            with self.subTest(workflow=workflow[-80:]):
                with self.assertRaisesRegex(AssertionError, "exactly one workflow step"):
                    extract_reconcile_script(workflow)

    def test_extraction_reports_unsupported_or_empty_run_block(self):
        for workflow in (
            WORKFLOW.replace("        run: |\n", "        run: >\n"),
            "      - name: Reconcile Project item\n        run: |\n",
        ):
            with self.subTest(workflow=workflow[-80:]):
                with self.assertRaisesRegex(AssertionError, "Reconcile Project item.*run"):
                    extract_reconcile_script(workflow)

    def test_workflow_uses_trusted_inline_python_and_secret(self):
        self.assertIn("shell: python", WORKFLOW)
        self.assertIn("GH_TOKEN: ${{ secrets.BASE_PROJECT_TOKEN }}", WORKFLOW)
        self.assertIn("timeout-minutes: 30", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertNotIn("uses:", WORKFLOW)
        self.assertNotIn("github.token", SCRIPT)

    def test_graphql_unavailable_still_syncs_and_reads_back(self):
        fixture = RestFixture()
        status, stdout, stderr, _ = self.execute(fixture)
        self.assertEqual(status, 0, stderr)
        self.assertIn("verified all five fields", stdout)
        self.assertEqual(fixture.current, dict(enumerate(DEFAULTS.values(), 1)))
        self.assertEqual(sum(c[0][3] == "PATCH" for c in fixture.calls), 1)
        self.assertEqual(fixture.calls[-1][0][3:5], ["GET", "orgs/basefoundry/projectsV2/14/items/101"])

    def test_preserves_nonempty_metadata_and_active_status_without_writes(self):
        for status_value in ("In Progress", "In Review"):
            with self.subTest(status=status_value):
                fixture = RestFixture()
                fixture.current = {1: status_value, 2: "Custom", 3: "Custom", 4: "Custom", 5: "Custom"}
                expected = copy.deepcopy(fixture.current)
                status, _, stderr, _ = self.execute(fixture)
                self.assertEqual(status, 0, stderr)
                self.assertEqual(fixture.current, expected)
                self.assertTrue(all(c[0][3] == "GET" for c in fixture.calls))

    def test_close_and_reopen(self):
        fixture = RestFixture()
        fixture.closed = True
        self.assertEqual(self.execute(fixture)[0], 0)
        self.assertEqual(fixture.current[1], "Done")
        fixture.closed = False
        self.assertEqual(self.execute(fixture)[0], 0)
        self.assertEqual(fixture.current[1], "Backlog")

    def test_add_response_shapes_and_delayed_visibility(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                fixture = RestFixture()
                fixture.exists = False
                fixture.direct_add = direct
                fixture.delayed = 2
                status, _, stderr, sleep = self.execute(fixture)
                self.assertEqual(status, 0, stderr)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(sum(c[0][3] == "POST" for c in fixture.calls), 1)

    def test_duplicate_add_race_recovers_exact_item(self):
        fixture = RestFixture()
        fixture.exists = False
        fixture.duplicate = True
        fixture.delayed_search = 2
        status, _, stderr, _ = self.execute(fixture)
        self.assertEqual(status, 0, stderr)
        self.assertEqual(sum(c[0][3] == "POST" for c in fixture.calls), 1)

    def test_item_lookup_does_not_depend_on_search_filter_grammar(self):
        fixture = RestFixture()
        fixture.filtered_search_empty = True
        fixture.duplicate = True
        status, stdout, stderr, _ = self.execute(fixture)
        self.assertEqual(status, 0, stderr)
        self.assertIn("verified all five fields", stdout)
        self.assertFalse(any(c[0][3] == "POST" for c in fixture.calls))
        self.assertFalse(any(arg.startswith("q=") for c in fixture.calls for arg in c[0]))

    def test_transient_rest_failures_retry(self):
        for error in (
            "rate limit (HTTP 429) Retry-After: 7",
            "Bad Gateway (HTTP 502)",
            subprocess.TimeoutExpired("gh", 60),
        ):
            with self.subTest(error=error):
                fixture = RestFixture()
                fixture.failures = [error]
                status, _, stderr, sleep = self.execute(fixture)
                self.assertEqual(status, 0, stderr)
                self.assertEqual(sleep.call_count, 1)
                if isinstance(error, str) and "Retry-After" in error:
                    sleep.assert_called_once_with(7)

    def test_auth_and_permission_failures_are_not_retried(self):
        for error in ("Bad credentials (HTTP 401)", "Forbidden (HTTP 403)"):
            with self.subTest(error=error):
                fixture = RestFixture()
                fixture.failures = [error]
                status, stdout, stderr, sleep = self.execute(fixture)
                self.assertEqual(status, 1)
                self.assertNotIn("Synced issue", stdout)
                self.assertIn(error, stderr)
                sleep.assert_not_called()
                self.assertEqual(len(fixture.calls), 1)

    def test_persistent_rate_limit_is_deferred_for_scheduled_recovery(self):
        fixture = RestFixture()
        fixture.failures = ["rate limit (HTTP 429)"] * 3
        status, stdout, _, sleep = self.execute(fixture)
        self.assertEqual(status, 0)
        self.assertNotIn("Synced issue", stdout)
        self.assertEqual(len(fixture.calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_long_retry_after_defers_without_early_retry(self):
        fixture = RestFixture()
        fixture.failures = ["rate limit (HTTP 429) Retry-After: 3600"]
        status, _, _, sleep = self.execute(fixture)
        self.assertEqual(status, 0)
        sleep.assert_not_called()
        self.assertEqual(len(fixture.calls), 1)

    def test_missing_token_and_invalid_input_fail_before_api(self):
        for overrides in (
            {"GH_TOKEN": ""},
            {"BASE_PROJECT_ISSUE_NUMBER": "0"},
            {"BASE_PROJECT_ISSUE_NUMBER": "495; echo unsafe"},
        ):
            with self.subTest(overrides=overrides):
                fixture = RestFixture()
                self.assertEqual(self.execute(fixture, **overrides)[0], 1)
                self.assertEqual(fixture.calls, [])

    def test_invalid_option_or_field_fails_before_patch(self):
        for missing_field in (False, True):
            fixture = RestFixture()
            if missing_field:
                fixture.fields.pop()
            else:
                fixture.fields[-1]["options"] = []
            status, stdout, _, _ = self.execute(fixture)
            self.assertEqual(status, 1)
            self.assertNotIn("Synced issue", stdout)
            self.assertFalse(any(c[0][3] == "PATCH" for c in fixture.calls))

    def test_wrong_item_identity_fails(self):
        fixture = RestFixture()
        fixture.exists = False
        fixture.wrong_identity = True
        status, stdout, stderr, _ = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertIn("identity did not match", stderr)

    def test_missing_item_after_add_fails_without_duplicate_writes(self):
        fixture = RestFixture()
        fixture.exists = False
        fixture.delayed = 3
        status, stdout, stderr, sleep = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(sum(c[0][3] == "POST" for c in fixture.calls), 1)
        self.assertIn("Project item is not visible after bounded retries.", stderr)
        self.assertEqual(
            sum(c[0][3:5] == ["GET", "orgs/basefoundry/projectsV2/14/items/101"] for c in fixture.calls), 3
        )

    def test_non_404_item_read_failure_keeps_original_diagnostic(self):
        fixture = RestFixture()
        fixture.item_read_failure = "Forbidden (HTTP 403)"
        status, stdout, stderr, sleep = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertIn("Forbidden (HTTP 403)", stderr)
        self.assertNotIn("not visible after bounded retries", stderr)
        sleep.assert_not_called()

    def test_readback_mismatch_never_claims_success(self):
        fixture = RestFixture()
        fixture.mismatch = True
        status, stdout, stderr, sleep = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertIn("readback did not match", stderr)
        self.assertEqual(sleep.call_count, 2)

    def test_failed_patch_never_claims_success(self):
        fixture = RestFixture()
        fixture.patch_failure = True
        status, stdout, stderr, sleep = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertIn("Forbidden", stderr)
        sleep.assert_not_called()

    def test_duplicate_item_that_never_becomes_visible_fails(self):
        fixture = RestFixture()
        fixture.exists = False
        fixture.duplicate = True
        fixture.delayed_search = 10
        status, stdout, stderr, sleep = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertIn("not visible after bounded retries", stderr)
        self.assertEqual(sleep.call_count, 2)

    def test_stale_readback_recovers(self):
        fixture = RestFixture()
        fixture.stale_readback = 2
        status, _, stderr, sleep = self.execute(fixture)
        self.assertEqual(status, 0, stderr)
        self.assertEqual(sleep.call_count, 2)

    def test_preserves_concurrent_open_status_change(self):
        for status_value in ("In Progress", "In Review"):
            with self.subTest(status=status_value):
                fixture = RestFixture()
                fixture.concurrent_status = status_value
                status, stdout, stderr, sleep = self.execute(fixture)
                self.assertEqual(status, 0, stderr)
                self.assertEqual(fixture.current[1], status_value)
                self.assertIn("Preserved a concurrent open-issue Project status change", stdout)
                self.assertEqual(sum(c[0][3] == "PATCH" for c in fixture.calls), 1)
                sleep.assert_not_called()

    def test_open_status_readback_rejects_done_and_invalid_options(self):
        for status_value in ("Done", "invalid-option"):
            with self.subTest(status=status_value):
                fixture = RestFixture()
                fixture.concurrent_status = status_value
                status, stdout, stderr, _ = self.execute(fixture)
                self.assertEqual(status, 1)
                self.assertNotIn("Synced issue", stdout)
                self.assertIn("bounded retries: Status", stderr)

    def test_closed_status_readback_remains_strict(self):
        fixture = RestFixture()
        fixture.closed = True
        fixture.concurrent_status = "In Progress"
        status, stdout, stderr, _ = self.execute(fixture)
        self.assertEqual(status, 1)
        self.assertNotIn("Synced issue", stdout)
        self.assertIn("bounded retries: Status", stderr)


if __name__ == "__main__":
    unittest.main()
