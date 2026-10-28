from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

import yaml


def test_dependabot_policy_rejects_an_unverified_collaborator_head_commit() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/issue-branch-policy.yml").read_text(encoding="utf-8"))
    policy_run = workflow["jobs"]["policy"]["steps"][0]["run"]
    head_sha = "a" * 40

    fake_gh = """#!/bin/sh
set -eu
path=""
for argument in "$@"; do
  case "$argument" in
    repos/*) path="$argument"; break ;;
  esac
done
case "$path" in
  repos/basefoundry/base-cli/pulls\\?state=open\\&per_page=100)
    printf '474\\t%s\\n' "$TEST_HEAD_SHA"
    ;;
  repos/basefoundry/base-cli/pulls/474)
    printf '%s\\n' '{"number":474,"state":"open","user":{"login":"dependabot[bot]"},"head":{"sha":"'"$TEST_HEAD_SHA"'","ref":"dependabot/github_actions/astral-sh/setup-uv-10.3.1","repo":{"full_name":"basefoundry/base-cli"}}}'
    ;;
  repos/basefoundry/base-cli/commits/*)
    printf '%s\\n' '{"author":{"login":"collaborator"},"commit":{"verification":{"verified":false,"reason":"unsigned"}}}'
    ;;
  repos/basefoundry/base-cli/statuses/*)
    ;;
  *)
    printf 'unexpected gh api path: %s\\n' "$path" >&2
    exit 2
    ;;
esac
"""

    with tempfile.TemporaryDirectory() as directory:
        fake_bin = Path(directory) / "bin"
        fake_bin.mkdir()
        gh_path = fake_bin / "gh"
        gh_path.write_text(fake_gh, encoding="utf-8")
        gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "GITHUB_REPOSITORY": "basefoundry/base-cli",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_RUN_ID": "123",
                "GH_TOKEN": "test-token",
                "POLICY_CONTEXT": "base/issue-branch-policy",
                "PULL_REQUEST_NUMBER": "474",
                "ISSUE_EVENT_NUMBER": "",
                "ISSUE_EVENT_LABEL": "",
                "DEFAULT_BRANCH": "main",
                "EVENT_NAME": "pull_request_target",
                "EVENT_HEAD_SHA": head_sha,
                "DISPATCH_HEAD_SHA": "",
                "PREVIOUS_HEAD_SHA": "",
                "RUN_SHA": "b" * 40,
                "TEST_HEAD_SHA": head_sha,
            }
        )
        completed = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", policy_run],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )

    assert completed.returncode != 0
    assert "One or more PRs sharing this commit violate branch policy" in completed.stderr
    assert "Validated Dependabot branch" not in completed.stdout
