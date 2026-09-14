from pathlib import Path

ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs" / "optional-output-dependencies.md"
INDEX = ROOT / "docs" / "index.md"
VALIDATOR = ROOT / "scripts" / "validate_docs.py"


def test_optional_output_dependency_guide_is_linked_from_the_documentation_index() -> None:
    assert "optional-output-dependencies.md" in INDEX.read_text(encoding="utf-8")
    assert "optional-output-dependencies.md" in VALIDATOR.read_text(encoding="utf-8")


def test_optional_output_dependency_guide_matches_declared_extras_and_fallbacks() -> None:
    text = DOC.read_text(encoding="utf-8")

    for required in (
        "base-cli[yaml]",
        "PyYAML>=6.0,<7",
        "base-cli[typer]",
        "base-cli[rich]",
        "base-cli[telemetry]",
        "base_cli.render_records",
        "base_cli.output",
        "PyYAML is required for YAML output.",
        "output-contracts.md",
        "dependency-support.md",
    ):
        assert required in text
