from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_release_docs import validate_release_docs

VALID_README = """\
# base-cli

| Version | License | Install | Release notes |
| --- | --- | --- | --- |
| `1.2.3` | [Apache-2.0](LICENSE) | `python -m pip install base-cli` | [v1.2.3](https://github.com/basefoundry/base-cli/releases/tag/v1.2.3) |

The 1.2.3 release is bound to the immutable `v1.2.3` tag.
"""


class ReleaseDocumentationValidationTests(unittest.TestCase):
    def validate(self, readme: str = VALID_README, *, tag: str = "v1.2.3") -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "VERSION"
            path = root / "README.md"
            version.write_text("1.2.3\n", encoding="utf-8")
            path.write_text(readme, encoding="utf-8")
            return validate_release_docs(version, path, tag)

    def test_accepts_matching_published_release_documentation(self) -> None:
        self.assertEqual(self.validate(), [])

    def test_rejects_stale_release_row(self) -> None:
        errors = self.validate(VALID_README.replace("1.2.3", "1.2.2").replace("v1.2.3", "v1.2.2"))
        self.assertTrue(any("do not match" in error for error in errors))

    def test_rejects_prepublication_wording(self) -> None:
        errors = self.validate(VALID_README + "Publication is pending the release checklist.\n")
        self.assertTrue(any("pre-publication wording" in error for error in errors))

    def test_rejects_tag_mismatch(self) -> None:
        errors = self.validate(tag="v9.9.9")
        self.assertTrue(any("does not match VERSION" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
