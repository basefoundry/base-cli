from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_pypi_metadata import validate_metadata

README = """\
# base-cli

| `1.2.3` | [Apache-2.0](LICENSE) | `python -m pip install base-cli` | [v1.2.3](https://github.com/basefoundry/base-cli/releases/tag/v1.2.3) |
"""


class PyPIMetadataValidationTests(unittest.TestCase):
    def test_accepts_matching_description(self) -> None:
        metadata = {"info": {"version": "1.2.3", "description": README}}
        self.assertEqual(validate_metadata(metadata, "1.2.3", README), [])

    def test_rejects_stale_description(self) -> None:
        metadata = {"info": {"version": "1.2.3", "description": README.replace("1.2.3", "1.2.2")}}
        errors = validate_metadata(metadata, "1.2.3", README)
        self.assertTrue(any("does not match" in error for error in errors))

    def test_rejects_prepublication_description(self) -> None:
        metadata = {"info": {"version": "1.2.3", "description": README + "Source release candidate: 1.2.3."}}
        errors = validate_metadata(metadata, "1.2.3", README)
        self.assertTrue(any("pre-publication wording" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
