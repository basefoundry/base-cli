from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_release_assets import validate_release_assets

VERSION = "1.2.3"
TAG = f"v{VERSION}"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


class ReleaseAssetVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.expected = self.root / "expected"
        self.existing = self.root / "existing"
        self.expected.mkdir()
        self.existing.mkdir()
        wheel = b"reviewed wheel bytes"
        sdist = b"reviewed source archive bytes"
        (self.expected / "base_cli-1.2.3-py3-none-any.whl").write_bytes(wheel)
        (self.expected / "base_cli-1.2.3.tar.gz").write_bytes(sdist)
        (self.expected / "SHA256SUMS").write_text(
            f"{hashlib.sha256(wheel).hexdigest()}  base_cli-1.2.3-py3-none-any.whl\n"
            f"{hashlib.sha256(sdist).hexdigest()}  base_cli-1.2.3.tar.gz\n",
            encoding="utf-8",
        )
        (self.expected / "SBOM.spdx.json").write_text(
            json.dumps(
                {
                    "name": "base-cli-1.2.3",
                    "documentNamespace": f"https://example.invalid/sbom/{VERSION}/{COMMIT}",
                    "documentComment": f"Source revision: {COMMIT}",
                }
            ),
            encoding="utf-8",
        )
        (self.expected / "RELEASE-BOM-ROW.json").write_text(
            json.dumps(
                {
                    "repository": "basefoundry/base-cli",
                    "version": VERSION,
                    "tag": TAG,
                    "commit": COMMIT,
                }
            ),
            encoding="utf-8",
        )
        for path in self.expected.iterdir():
            (self.existing / path.name).write_bytes(path.read_bytes())
        self.release_json = self.root / "release.json"
        self.release_json.write_text(
            json.dumps({"tag_name": TAG, "draft": False, "prerelease": False}),
            encoding="utf-8",
        )
        self.version_file = self.root / "VERSION"
        self.version_file.write_text(f"{VERSION}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self, *, tag: str = TAG, tag_commit: str = COMMIT) -> list[str]:
        return validate_release_assets(
            self.expected,
            self.existing,
            self.release_json,
            self.version_file,
            tag=tag,
            source_commit=COMMIT,
            resolved_tag_commit=tag_commit,
        )

    def test_accepts_identical_published_release_without_mutation(self) -> None:
        self.assertEqual(self.validate(), [])

    def test_rejects_changed_asset_bytes_with_expected_and_observed_checksums(self) -> None:
        (self.existing / "base_cli-1.2.3.tar.gz").write_bytes(b"changed archive")

        errors = self.validate()

        self.assertTrue(any("expected SHA-256" in error and "observed SHA-256" in error for error in errors))

    def test_rejects_changed_filename_set(self) -> None:
        (self.existing / "renamed.tar.gz").write_bytes((self.existing / "base_cli-1.2.3.tar.gz").read_bytes())
        (self.existing / "base_cli-1.2.3.tar.gz").unlink()

        errors = self.validate()

        self.assertTrue(any("asset filenames differ" in error for error in errors))

    def test_rejects_moved_tag_target(self) -> None:
        other_commit = "fedcba9876543210fedcba9876543210fedcba98"

        errors = self.validate(tag_commit=other_commit)

        self.assertTrue(any("resolves to" in error for error in errors))

    def test_rejects_release_metadata_for_another_tag(self) -> None:
        self.release_json.write_text(
            json.dumps({"tag_name": "v9.9.9", "draft": False, "prerelease": False}),
            encoding="utf-8",
        )

        errors = self.validate()

        self.assertTrue(any("existing release tag" in error for error in errors))

    def test_rejects_nonidentical_release_checksum_manifest(self) -> None:
        (self.expected / "SHA256SUMS").write_text("0" * 64 + "  base_cli-1.2.3.tar.gz\n", encoding="utf-8")

        errors = self.validate()

        self.assertTrue(any("SHA256SUMS" in error for error in errors))

    def test_rejects_sbom_and_release_bom_from_another_commit(self) -> None:
        sbom_path = self.expected / "SBOM.spdx.json"
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        sbom["documentComment"] = f"Source revision: {'f' * 40}"
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        bom_path = self.expected / "RELEASE-BOM-ROW.json"
        bom = json.loads(bom_path.read_text(encoding="utf-8"))
        bom["commit"] = "f" * 40
        bom_path.write_text(json.dumps(bom), encoding="utf-8")

        errors = self.validate()

        self.assertTrue(any("SBOM" in error and "source commit" in error for error in errors))
        self.assertTrue(any("RELEASE-BOM-ROW.json" in error and "source commit" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
