from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_reference_uses_framework_descriptions_for_runtime_constants() -> None:
    reference = (ROOT / "docs" / "api-reference.md").read_text(encoding="utf-8")
    assert "Installed base-cli distribution version exposed by the public facade." in reference
    assert "Version number for the stable JSON output, error, and log contracts." in reference
    assert "str(object='')" not in reference
    assert "dict(**kwargs)" not in reference


def test_typer_guide_states_repeated_attachment_contract() -> None:
    guide = (ROOT / "docs" / "typer-adapter.md").read_text(encoding="utf-8")
    assert "without new lifecycle arguments is idempotent" in guide
    assert "lifecycle arguments after attachment is" in guide
