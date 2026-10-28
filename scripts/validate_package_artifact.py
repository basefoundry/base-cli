#!/usr/bin/env python3
"""Validate the distributions produced for a base-cli release."""

from __future__ import annotations

import argparse
import email
import hashlib
import re
import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import NoReturn

PACKAGE_NAME = "base-cli"
IMPORT_NAME = "base_cli"
MINIMUM_PYTHON = ">=3.10"
REQUIRED_DEPENDENCIES = ("click<8.6,>=8.1",)
YAML_DEPENDENCY_PREFIX = "PyYAML<7,>=6.0"
DOCUMENTATION_URL = "Documentation, https://basefoundry.github.io/base-cli/"
STARTUP_HOOK_NAMES = frozenset({"sitecustomize.py", "usercustomize.py"})
NATIVE_OR_EXECUTABLE_SUFFIXES = (".dll", ".dylib", ".exe", ".pyd", ".so")
ALLOWED_WHEEL_DIST_INFO_FILES = frozenset({"METADATA", "RECORD", "WHEEL", "top_level.txt", "entry_points.txt"})
ALLOWED_SDIST_FILES = frozenset(
    {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "SECURITY.md",
        "setup.cfg",
        "VERSION",
        "base_manifest.yaml",
        "pyproject.toml",
    }
)
ALLOWED_SDIST_PREFIXES = (
    ".github/",
    "docs/",
    "examples/",
    "compatibility/",
    "lib/python/base_cli/",
    "scripts/",
    "tests/",
    "base_cli.egg-info/",
    "lib/python/base_cli.egg-info/",
)


def fail(message: str) -> NoReturn:
    print(f"artifact validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_expected_version() -> str:
    version_path = Path(__file__).resolve().parents[1] / "VERSION"
    lines = version_path.read_text(encoding="utf-8").splitlines()
    if not lines or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?", lines[0].strip()):
        fail(f"invalid VERSION file: {version_path}")
    return lines[0].strip()


def expected_package_files() -> set[str]:
    package_root = Path(__file__).resolve().parents[1] / "lib" / "python" / IMPORT_NAME
    if not package_root.is_dir():
        fail(f"package source directory does not exist: {package_root}")
    return {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def expected_package_hashes() -> dict[str, str]:
    package_root = Path(__file__).resolve().parents[1] / "lib" / "python" / IMPORT_NAME
    return {
        relative.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        for relative in [path.relative_to(package_root)]
    }


def validate_member_name(name: str, artifact_name: str) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        fail(f"{artifact_name} contains an unsafe archive member path {name!r}")
    lower_name = normalized.lower()
    if lower_name.endswith(".pth") or path.name.lower() in STARTUP_HOOK_NAMES:
        fail(f"{artifact_name} contains a Python startup hook {name!r}")
    if lower_name.endswith(NATIVE_OR_EXECUTABLE_SUFFIXES):
        fail(f"{artifact_name} contains an unexpected executable or native library {name!r}")


def validate_archive_names(names: list[str], artifact_name: str) -> None:
    if len(names) != len(set(names)):
        fail(f"{artifact_name} contains duplicate archive members")
    for name in names:
        validate_member_name(name, artifact_name)


def read_expected_readme() -> str:
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    try:
        return readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"could not read the source README.md: {exc}")


def normalized_text(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def validate_metadata_description(metadata: email.message.Message, expected_readme: str, artifact_name: str) -> None:
    payload = metadata.get_payload(decode=True)
    if not isinstance(payload, bytes):
        fail(f"{artifact_name} has no text long description")
    try:
        description = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{artifact_name} long description is not UTF-8: {exc}")
    if normalized_text(description) != normalized_text(expected_readme):
        fail(f"{artifact_name} long description does not match the source README.md")


def validate_wheel(
    path: Path,
    expected_version: str,
    package_files: set[str],
    package_hashes: dict[str, str],
    expected_readme: str,
) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        validate_archive_names(names, path.name)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            fail(f"{path.name} must contain exactly one dist-info METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))

        expected_headers = {
            "Name": PACKAGE_NAME,
            "Version": expected_version,
            "License": "Apache-2.0",
            "Requires-Python": MINIMUM_PYTHON,
        }
        for header, expected in expected_headers.items():
            if metadata.get(header) != expected:
                fail(f"{path.name} has {header}={metadata.get(header)!r}; expected {expected!r}")
        validate_metadata_description(metadata, expected_readme, path.name)

        dependencies = set(metadata.get_all("Requires-Dist", []))
        for dependency in REQUIRED_DEPENDENCIES:
            if dependency not in dependencies:
                fail(f"{path.name} is missing runtime dependency {dependency!r}")
        if metadata.get("Provides-Extra") != "yaml":
            extras = metadata.get_all("Provides-Extra", [])
            if "yaml" not in extras:
                fail(f"{path.name} does not advertise the yaml optional extra")
        yaml_dependencies = {
            dependency
            for dependency in dependencies
            if dependency.startswith(YAML_DEPENDENCY_PREFIX) and 'extra == "yaml"' in dependency
        }
        if not yaml_dependencies:
            fail(f"{path.name} does not bind PyYAML to the yaml optional extra")
        if any(dependency == YAML_DEPENDENCY_PREFIX for dependency in dependencies):
            fail(f"{path.name} makes PyYAML a core dependency instead of an optional extra")
        if DOCUMENTATION_URL not in metadata.get_all("Project-URL", []):
            fail(f"{path.name} is missing the canonical Documentation project URL")

        if f"{IMPORT_NAME}/py.typed" not in names:
            fail(f"{path.name} does not contain {IMPORT_NAME}/py.typed")
        if not any(
            name.endswith(".dist-info/LICENSE") or name.endswith(".dist-info/licenses/LICENSE") for name in names
        ):
            fail(f"{path.name} does not contain the packaged LICENSE file")
        if any(name.startswith("tests/") or f"/{IMPORT_NAME}/tests/" in name for name in names):
            fail(f"{path.name} contains repository test files")

        dist_info_prefix = metadata_names[0].rsplit("/", 1)[0] + "/"
        for info in archive.infolist():
            name = info.filename
            mode_bits = info.external_attr >> 16
            if stat.S_ISLNK(mode_bits):
                fail(f"{path.name} contains a symbolic link archive member {name!r}")
            if info.is_dir():
                continue
            mode = mode_bits & 0o7777
            if mode & 0o111:
                fail(f"{path.name} contains an executable archive member {name!r}")
            if name.startswith(f"{IMPORT_NAME}/"):
                relative = name[len(IMPORT_NAME) + 1 :]
                if relative not in package_files:
                    fail(f"{path.name} contains non-allowlisted package file {name!r}")
                if hashlib.sha256(archive.read(name)).hexdigest() != package_hashes[relative]:
                    fail(f"{path.name} package file {name!r} differs from the reviewed source")
                continue
            if name.startswith(dist_info_prefix):
                relative = name[len(dist_info_prefix) :]
                if relative in {"LICENSE", "licenses/LICENSE"}:
                    continue
                if relative not in ALLOWED_WHEEL_DIST_INFO_FILES:
                    fail(f"{path.name} contains non-allowlisted metadata file {name!r}")
                continue
            fail(f"{path.name} contains non-allowlisted archive member {name!r}")


def validate_sdist(path: Path, expected_version: str, package_hashes: dict[str, str], expected_readme: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        validate_archive_names(names, path.name)
        roots = {name.split("/", 1)[0] for name in names if name}
        if len(roots) != 1:
            fail(f"{path.name} must contain exactly one top-level directory")
        root = next(iter(roots))
        root_prefix = f"{root}/"
        for member in members:
            name = member.name
            if name == root or member.isdir():
                continue
            if member.issym() or member.islnk():
                fail(f"{path.name} contains a symbolic or hard link: {name!r}")
            if member.mode & 0o111 and not (name.startswith(f"{root_prefix}scripts/") and name.endswith(".py")):
                fail(f"{path.name} contains an executable archive member {name!r}")
            if not name.startswith(root_prefix):
                fail(f"{path.name} contains member outside its top-level directory: {name!r}")
            relative = name[len(root_prefix) :]
            if relative not in ALLOWED_SDIST_FILES and not relative.startswith(ALLOWED_SDIST_PREFIXES):
                fail(f"{path.name} contains non-allowlisted archive member {relative!r}")
            package_prefix = "lib/python/base_cli/"
            if relative.startswith(package_prefix):
                package_relative = relative[len(package_prefix) :]
                payload = archive.extractfile(member)
                if package_relative not in package_hashes or payload is None:
                    fail(f"{path.name} contains non-allowlisted package file {relative!r}")
                if hashlib.sha256(payload.read()).hexdigest() != package_hashes[package_relative]:
                    fail(f"{path.name} package file {relative!r} differs from the reviewed source")
        required_suffixes = {"pyproject.toml", "README.md", "LICENSE", "VERSION"}
        present_suffixes = {name.rsplit("/", 1)[-1] for name in names}
        missing = required_suffixes - present_suffixes
        if missing:
            fail(f"{path.name} is missing sdist files: {', '.join(sorted(missing))}")
        version_members = [member for member in archive.getmembers() if member.name.endswith("/VERSION")]
        if len(version_members) != 1:
            fail(f"{path.name} must contain exactly one VERSION file")
        version_text = archive.extractfile(version_members[0])
        if version_text is None or version_text.read().decode().splitlines()[0].strip() != expected_version:
            fail(f"{path.name} VERSION does not match {expected_version}")
        readme_members = [member for member in members if member.name == f"{root_prefix}README.md"]
        if len(readme_members) != 1:
            fail(f"{path.name} must contain exactly one README.md")
        readme_text = archive.extractfile(readme_members[0])
        if readme_text is None or normalized_text(readme_text.read().decode("utf-8")) != normalized_text(
            expected_readme
        ):
            fail(f"{path.name} README.md does not match the source README.md")
        pkg_info_members = [member for member in members if member.name == f"{root_prefix}PKG-INFO"]
        if len(pkg_info_members) != 1:
            fail(f"{path.name} must contain exactly one PKG-INFO")
        pkg_info = archive.extractfile(pkg_info_members[0])
        if pkg_info is None:
            fail(f"{path.name} PKG-INFO could not be read")
        metadata = email.message_from_bytes(pkg_info.read())
        validate_metadata_description(metadata, expected_readme, path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="directory containing the built distributions")
    args = parser.parse_args()
    if not args.dist.is_dir():
        fail(f"distribution directory does not exist: {args.dist}")

    expected_version = read_expected_version()
    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        fail(f"expected one wheel and one sdist, found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)")
    expected_stem = f"base_cli-{expected_version}"
    if not wheels[0].name.startswith(expected_stem) or not sdists[0].name.startswith(expected_stem):
        fail(f"artifact filenames do not match version {expected_version}")

    expected_readme = read_expected_readme()
    package_hashes = expected_package_hashes()
    validate_wheel(wheels[0], expected_version, set(package_hashes), package_hashes, expected_readme)
    validate_sdist(sdists[0], expected_version, package_hashes, expected_readme)
    print(f"Validated {PACKAGE_NAME} {expected_version}: wheel, sdist, metadata, package data, and test boundary.")


if __name__ == "__main__":
    main()
