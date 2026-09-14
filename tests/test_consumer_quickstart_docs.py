from pathlib import Path

ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs" / "consumer-quickstart.md"
INDEX = ROOT / "docs" / "index.md"
VALIDATOR = ROOT / "scripts" / "validate_docs.py"


def test_consumer_quickstart_is_linked_from_the_documentation_index() -> None:
    assert "consumer-quickstart.md" in INDEX.read_text(encoding="utf-8")
    assert "consumer-quickstart.md" in VALIDATOR.read_text(encoding="utf-8")


def test_consumer_quickstart_uses_only_public_apis_and_explains_both_modes() -> None:
    text = DOC.read_text(encoding="utf-8")

    for required in (
        "base_cli.App",
        "base_cli.LifecycleOptions",
        'base_cli.LifecycleOption("--json")',
        "base_cli.run_app(app)",
        "python hello.py --name Ada",
        "python hello.py --json --name Ada",
        "base-cli.output",
        "dependency-support.md",
        "output-contracts.md",
    ):
        assert required in text
