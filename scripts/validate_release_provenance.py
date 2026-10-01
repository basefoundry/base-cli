#!/usr/bin/env python3
"""Fail closed unless a publication comes from a reviewed main-line commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from .release_metadata_helpers import validate_tag_prefix
except ImportError:  # pragma: no cover - direct script execution
    from release_metadata_helpers import validate_tag_prefix

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
VALID_PUBLISH_TARGETS = {"", "testpypi", "pypi"}


def validate_release_provenance(
    *,
    event_name: str,
    ref_type: str,
    tag: str,
    publish_target: str,
    source_commit: str,
    tag_type: str,
    resolved_tag_commit: str,
    main_reachable: bool,
    repository_shallow: bool,
    forced: bool = False,
    deleted: bool = False,
) -> list[str]:
    """Return violations for the source that is about to be published."""
    errors: list[str] = []
    if publish_target not in VALID_PUBLISH_TARGETS:
        errors.append(f"unsupported publication target {publish_target!r}")
    if not FULL_SHA.fullmatch(source_commit):
        errors.append("reviewed source commit must be a full 40-character commit SHA")
    if repository_shallow:
        errors.append("release provenance cannot be verified from a shallow repository")

    tag_release = ref_type == "tag"
    if publish_target == "pypi" and ref_type != "tag":
        errors.append("PyPI publication requires a version tag, not a branch or pull request ref")

    if tag_release:
        tag_error = validate_tag_prefix(tag)
        if tag_error is not None:
            errors.append(tag_error)
        if tag_type != "tag":
            errors.append("release tag must be an annotated tag; lightweight tags are rejected")
        if not FULL_SHA.fullmatch(resolved_tag_commit):
            errors.append("release tag must resolve to a full commit SHA")
        elif resolved_tag_commit != source_commit:
            errors.append(
                f"release tag does not resolve to the reviewed source commit ({resolved_tag_commit} != {source_commit})"
            )
        if not main_reachable:
            errors.append("release tag commit is not reachable from the trusted origin/main history")
        if forced:
            errors.append("forced tag updates are rejected for release publication")
        if deleted:
            errors.append("deleted tag events are rejected for release publication")

    return errors


def _git(*args: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def _git_failure(*args: str) -> str:
    return f"git {' '.join(args)} failed; release provenance cannot be verified"


def _git_checked(git_errors: list[str], *args: str, allowed_codes: tuple[int, ...] = ()) -> tuple[int, str]:
    code, output = _git(*args)
    if code != 0 and code not in allowed_codes:
        git_errors.append(_git_failure(*args))
    return code, output


def _git_required(git_errors: list[str], *args: str) -> str:
    code, output = _git_checked(git_errors, *args)
    return output if code == 0 else ""


def _read_event_flags(event_path: Path) -> tuple[bool, bool, list[str]]:
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, False, [f"could not read the GitHub event payload: {exc}"]
    return bool(payload.get("forced")), bool(payload.get("deleted")), []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--ref-type", default=os.environ.get("GITHUB_REF_TYPE", ""))
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--publish-target", default=os.environ.get("PUBLISH_TARGET", ""))
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--main-ref", default="refs/remotes/origin/main")
    args = parser.parse_args()

    tag_type = ""
    resolved_tag_commit = ""
    main_reachable = False
    git_errors: list[str] = []
    if args.ref_type == "tag":
        tag_ref = f"refs/tags/{args.tag}"
        tag_type = _git_required(git_errors, "cat-file", "-t", tag_ref)
        resolved_tag_commit = _git_required(git_errors, "rev-parse", "--verify", f"{tag_ref}^{{}}")

        if resolved_tag_commit:
            ancestor_code, _ = _git_checked(
                git_errors,
                "merge-base",
                "--is-ancestor",
                resolved_tag_commit,
                args.main_ref,
                allowed_codes=(1,),
            )
            if ancestor_code == 0:
                main_reachable = True

    forced = False
    deleted = False
    event_errors: list[str] = []
    if args.ref_type == "tag" or args.publish_target == "pypi":
        event_path = Path(args.event_path) if args.event_path else None
        if event_path is None:
            event_errors.append("GitHub event payload is required for release provenance validation")
        else:
            forced, deleted, event_errors = _read_event_flags(event_path)

    shallow_output = _git_required(git_errors, "rev-parse", "--is-shallow-repository")
    if not shallow_output:
        repository_shallow = True
    elif shallow_output not in {"true", "false"}:
        git_errors.append("git rev-parse returned an unknown shallow-repository state")
        repository_shallow = True
    else:
        repository_shallow = shallow_output == "true"

    errors = (
        event_errors
        + git_errors
        + validate_release_provenance(
            event_name=args.event_name,
            ref_type=args.ref_type,
            tag=args.tag,
            publish_target=args.publish_target,
            source_commit=args.source_commit,
            tag_type=tag_type,
            resolved_tag_commit=resolved_tag_commit,
            main_reachable=main_reachable,
            repository_shallow=repository_shallow,
            forced=forced,
            deleted=deleted,
        )
    )
    if errors:
        for error in errors:
            print(f"release provenance validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated release provenance for {args.source_commit}")


if __name__ == "__main__":
    main()
