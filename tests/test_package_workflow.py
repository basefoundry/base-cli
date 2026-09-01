from __future__ import annotations

from pathlib import Path


def test_package_workflow_uses_numeric_reproducibility_epoch() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text(encoding="utf-8")

    assert "github.event.head_commit.timestamp" not in workflow
    assert workflow.count("SOURCE_DATE_EPOCH: '0'") == 2
