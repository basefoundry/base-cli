from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_pypi_metadata import validate_artifact_hashes, validate_metadata

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

    def test_verifies_pypi_file_hashes_against_reviewed_artifacts_and_checksums(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "base_cli-1.2.3-py3-none-any.whl"
            sdist = root / "base_cli-1.2.3.tar.gz"
            wheel.write_bytes(b"wheel")
            sdist.write_bytes(b"sdist")
            checksums = "\n".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in (wheel, sdist)
            )
            (root / "SHA256SUMS").write_text(checksums + "\n", encoding="utf-8")
            metadata = {
                "info": {"version": "1.2.3"},
                "urls": [
                    {"filename": path.name, "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
                    for path in (wheel, sdist)
                ],
            }
            errors, evidence = validate_artifact_hashes(metadata, root)

            self.assertEqual(errors, [])
            self.assertTrue(evidence["result"])

    def test_rejects_pypi_hash_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "base_cli-1.2.3-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            (root / "SHA256SUMS").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
            metadata = {
                "info": {"version": "1.2.3"},
                "urls": [{"filename": wheel.name, "digests": {"sha256": "0" * 64}}],
            }

            errors, evidence = validate_artifact_hashes(metadata, root)

            self.assertTrue(any("does not match" in error for error in errors))
            self.assertFalse(evidence["result"])


if __name__ == "__main__":
    unittest.main()
