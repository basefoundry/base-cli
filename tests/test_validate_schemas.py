from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import validate_schemas


class SchemaValidationTests(unittest.TestCase):
    def test_repository_schemas_are_valid_and_in_sync(self) -> None:
        validate_schemas.validate(Path(__file__).resolve().parents[1])

    def test_schema_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = root / "lib/python/base_cli/schemas/v1"
            docs = root / "docs/schemas/v1"
            package.mkdir(parents=True)
            docs.mkdir(parents=True)
            payload = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://example.test/schema",
                "title": "test",
                "type": "object",
                "required": [],
                "properties": {},
            }
            (package / "test.json").write_text(json.dumps(payload), encoding="utf-8")
            (docs / "test.json").write_text(json.dumps({**payload, "title": "drift"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                validate_schemas.validate(root)


if __name__ == "__main__":
    unittest.main()
