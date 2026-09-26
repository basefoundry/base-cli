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

    production_release = (event_name == "push" and ref_type == "tag") or publish_target == "pypi"
    tag_release = ref_type == "tag"
    if production_release and ref_type != "tag":
        errors.append("PyPI publication requires a version tag, not a branch or pull request ref")

    if tag_release:
        if not tag.startswith("v") or tag == "v":
            errors.append(f"release tag must be a v-prefixed version, got {tag!r}")
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


def _git_output(*args: str) -> str:
    returncode, output = _git(*args)
    return output if returncode == 0 else ""


def _read_event_flags(event_path: Path) -> tuple[bool, bool, list[str]]:
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, False, [f"could not read the GitHub event payload: {exc}"]
    return bool(payload.get("forced")), bool(payload.get("deleted")), []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--event-path", type=Path, default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--ref-type", default=os.environ.get("GITHUB_REF_TYPE", ""))
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--publish-target", default=os.environ.get("PUBLISH_TARGET", ""))
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--main-ref", default="refs/remotes/origin/main")
    args = parser.parse_args()

    tag_type = ""
    resolved_tag_commit = ""
    main_reachable = False
    if args.ref_type == "tag":
        tag_ref = f"refs/tags/{args.tag}"
        tag_type = _git_output("cat-file", "-t", tag_ref)
        resolved_tag_commit = _git_output("rev-parse", "--verify", f"{tag_ref}^{{}}")
        if resolved_tag_commit:
            main_reachable = _git("merge-base", "--is-ancestor", resolved_tag_commit, args.main_ref)[0] == 0

    forced = False
    deleted = False
    event_errors: list[str] = []
    if args.ref_type == "tag" or args.publish_target == "pypi":
        if not args.event_path:
            event_errors.append("GitHub event payload is required for release provenance validation")
        else:
            forced, deleted, event_errors = _read_event_flags(Path(args.event_path))

    errors = event_errors + validate_release_provenance(
        event_name=args.event_name,
        ref_type=args.ref_type,
        tag=args.tag,
        publish_target=args.publish_target,
        source_commit=args.source_commit,
        tag_type=tag_type,
        resolved_tag_commit=resolved_tag_commit,
        main_reachable=main_reachable,
        repository_shallow=_git_output("rev-parse", "--is-shallow-repository") == "true",
        forced=forced,
        deleted=deleted,
    )
    if errors:
        for error in errors:
            print(f"release provenance validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated release provenance for {args.source_commit}")


if __name__ == "__main__":
    main()
