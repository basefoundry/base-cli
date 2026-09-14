from pathlib import Path

ROOT = Path(__file__).parents[1]
DOC = ROOT / "docs" / "strict-json-consumer.md"
INDEX = ROOT / "docs" / "index.md"


def test_strict_json_consumer_guide_is_linked_from_the_documentation_index() -> None:
    assert "strict-json-consumer.md" in INDEX.read_text(encoding="utf-8")


def test_strict_json_consumer_guide_covers_json_and_ndjson_contract_boundaries() -> None:
    text = DOC.read_text(encoding="utf-8")

    for required in (
        "JSON.parse",
        "base-cli.output",
        "base-cli.record",
        "output-success.json",
        "ndjson-record.json",
        "invalid-output-extra-field.json",
        "validate_contract_fixtures.mjs",
        "stderr",
    ):
        assert required in text
