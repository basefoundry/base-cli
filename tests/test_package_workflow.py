from __future__ import annotations

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
    assert "SOURCE_COMMIT: ${{ steps.metadata.outputs.source_commit }}" in workflow
    assert 'git archive "$SOURCE_COMMIT"' in workflow
    assert "Manual PyPI publication requires the release_tag input." in workflow
    assert '--source-commit "$SOURCE_COMMIT"' in workflow


def test_package_workflow_uses_a_published_pypi_publisher_image_tag() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert workflow.count("uses: pypa/gh-action-pypi-publish@v1.14.2") == 2
    assert "pypa/gh-action-pypi-publish@a892a5a61159132606e93a2fa6f4358831b04d26" not in workflow


def test_package_workflow_does_not_replace_published_release_assets() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")
    verifier = (Path(__file__).resolve().parents[1] / "scripts/verify_release_assets.py").read_text(encoding="utf-8")

    assert "Verify and create immutable GitHub Release" in workflow
    assert "verify_release_assets.py" in workflow
    assert "refusing to replace immutable release assets" in verifier
    assert "gh release download" in workflow
    assert "gh attestation verify" in workflow
    assert '--source-digest "$GITHUB_SHA"' in workflow
    assert '--source-ref "$GITHUB_REF"' in workflow
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
    assert "needs: [build, smoke, provenance, publish, attest, verify_pypi]" in workflow


def test_package_workflow_gates_writes_on_release_provenance() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow)["jobs"]

    assert "name: Verify reviewed release provenance" in workflow
    assert 'git fetch --no-tags --prune origin "refs/heads/main:refs/remotes/origin/main"' in workflow
    assert "python scripts/validate_release_provenance.py" in workflow
    assert jobs["publish"]["needs"] == ["build", "smoke", "provenance"]
    assert jobs["attest"]["needs"] == ["build", "smoke", "provenance"]
    assert jobs["release"]["needs"] == ["build", "smoke", "provenance", "publish", "attest", "verify_pypi"]
