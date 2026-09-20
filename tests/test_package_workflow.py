from __future__ import annotations

from pathlib import Path


def test_package_workflow_uses_numeric_reproducibility_epoch() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert "github.event.head_commit.timestamp" not in workflow
    assert workflow.count("SOURCE_DATE_EPOCH: '0'") == 2


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
    assert "--clobber" not in workflow
