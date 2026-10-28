from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml


def test_package_workflow_uses_numeric_reproducibility_epoch() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert "github.event.head_commit.timestamp" not in workflow
    assert workflow.count("SOURCE_DATE_EPOCH: '0'") == 2


def test_package_workflow_can_recover_an_immutable_tag_from_default_branch() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert "release_tag:" in workflow
    assert "ref: ${{ inputs.release_tag || github.ref }}" in workflow
    assert "source_commit: ${{ steps.metadata.outputs.source_commit }}" in workflow
    assert "source_ref: ${{ steps.metadata.outputs.source_ref }}" in workflow
    assert "SOURCE_COMMIT: ${{ steps.metadata.outputs.source_commit }}" in workflow
    assert 'git archive "$SOURCE_COMMIT"' in workflow
    assert "Manual PyPI publication requires the release_tag input." in workflow
    assert '--source-commit "$SOURCE_COMMIT"' in workflow


def test_package_workflow_resolves_branch_source_ref_for_testpypi_dispatch() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/package.yml").read_text(encoding="utf-8"))
    metadata_step = next(step for step in workflow["jobs"]["build"]["steps"] if step.get("id") == "metadata")

    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "github-output"
        environment = os.environ.copy()
        environment.update(
            {
                "GITHUB_REF": "refs/heads/testpypi-rehearsal",
                "GITHUB_REF_TYPE": "branch",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_OUTPUT": str(output_path),
                "REQUESTED_RELEASE_TAG": "",
            }
        )
        subprocess.run(
            ["bash", "-euo", "pipefail", "-c", metadata_step["run"]],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs = dict(line.split("=", 1) for line in output_path.read_text(encoding="utf-8").splitlines())

    assert outputs["source_ref"] == "refs/heads/testpypi-rehearsal"
    assert outputs["source_ref_type"] == "branch"
    assert outputs["release_tag"] == ""


def test_package_workflow_uses_the_pinned_pypi_publisher_release() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert workflow.count("uses: pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086") == 2


def test_all_workflows_pin_every_third_party_action_to_a_full_sha() -> None:
    workflow_dir = Path(__file__).resolve().parents[1] / ".github/workflows"
    workflows = sorted(workflow_dir.glob("*.yml"))
    assert workflows

    unpinned: list[tuple[str, str]] = []
    for workflow_path in workflows:
        workflow = workflow_path.read_text(encoding="utf-8")
        refs = re.findall(r"^\s*-?\s*uses:\s+[^@\s]+@([^\s#]+)", workflow, flags=re.MULTILINE)
        unpinned.extend((workflow_path.name, ref) for ref in refs if not re.fullmatch(r"[0-9a-f]{40}", ref))

    assert unpinned == []


def test_package_workflow_does_not_replace_published_release_assets() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")
    verifier = (Path(__file__).resolve().parents[1] / "scripts/verify_release_assets.py").read_text(encoding="utf-8")

    assert "Verify and create immutable GitHub Release" in workflow
    assert "verify_release_assets.py" in workflow
    assert "refusing to replace immutable release assets" in verifier
    assert "gh release download" in workflow
    assert "gh attestation verify" in workflow
    assert '--source-digest "$SOURCE_COMMIT"' in workflow
    assert '--source-ref "$SOURCE_REF"' in workflow
    assert '--source-commit "$source_commit"' in workflow
    assert "--clobber" not in workflow


def test_package_workflow_uses_explicit_release_notes() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert "--generate-notes" not in workflow
    assert (
        '--notes "Published distributions and release metadata for $tag. See CHANGELOG.md for the reviewed release notes."'
        in workflow
    )


def test_package_workflow_gates_tag_publication_on_release_documentation() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert "Validate release-facing documentation" in workflow
    assert "python scripts/validate_release_docs.py" in workflow
    assert "scripts/verify_pypi_metadata.py" in workflow
    assert "name: Verify published PyPI metadata" in workflow
    assert "--artifacts-dir dist" in workflow
    assert "PYPI-BYTE-VERIFICATION.json" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "needs: [build, smoke, provenance, publish, attest, verify_pypi]" in workflow
    assert "needs: [build, smoke, provenance, attest, verify_attestations, publish, verify_pypi]" in workflow


def test_package_workflow_gates_writes_on_release_provenance() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]

    assert "name: Verify reviewed release provenance" in workflow
    assert 'git fetch --no-tags --prune origin "refs/heads/main:refs/remotes/origin/main"' in workflow
    assert "python scripts/validate_release_provenance.py" in workflow
    assert jobs["publish"]["needs"] == ["build", "smoke", "provenance", "attest", "verify_attestations"]
    assert jobs["attest"]["needs"] == ["build", "smoke", "provenance"]
    assert jobs["release"]["needs"] == [
        "build",
        "smoke",
        "provenance",
        "attest",
        "verify_attestations",
        "publish",
        "verify_pypi",
    ]


def test_package_workflow_verifies_attestations_before_publication() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]

    assert jobs["publish"]["needs"] == ["build", "smoke", "provenance", "attest", "verify_attestations"]
    assert jobs["verify_attestations"]["needs"] == ["build", "smoke", "provenance", "attest"]
    assert '--source-digest "$SOURCE_COMMIT"' in workflow
    assert "SOURCE_REF: ${{ needs.build.outputs.source_ref }}" in workflow
    assert '--source-ref "$SOURCE_REF"' in workflow
    assert '--source-ref "refs/tags/$RELEASE_TAG"' not in workflow
