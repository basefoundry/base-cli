from __future__ import annotations

import os
from pathlib import Path

import pytest
from base_cli import CliProfile
from base_cli.config import ConfigurationError, load_yaml_file


def test_unsafe_ancestor_is_refused_and_optout_is_explicit(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX ownership/mode contract")
    project = tmp_path / "project"
    child = project / "sub"
    child.mkdir(parents=True)
    config = project / ".base-cli.yaml"
    config.write_text("keep_temp: true\n")
    project.chmod(0o777)
    try:
        with pytest.raises(ConfigurationError, match="Untrusted.*project"):
            CliProfile.batteries_included("trust").discover_project(child)
        assert CliProfile.batteries_included("trust", verify_discovered_config=False).discover_project(child)
    finally:
        project.chmod(0o700)
    config.chmod(0o666)
    with pytest.raises(ConfigurationError, match="Untrusted.*base-cli"):
        CliProfile.batteries_included("trust").discover_project(child)


def test_group_writable_owned_project_paths_are_accepted(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX ownership/mode contract")
    project = tmp_path / "project"
    child = project / "sub"
    child.mkdir(parents=True)
    config = project / ".base-cli.yaml"
    config.write_text("keep_temp: true\n")
    project.chmod(0o775)
    config.chmod(0o664)
    try:
        assert CliProfile.batteries_included("group-writable").discover_project(child)
    finally:
        config.chmod(0o600)
        project.chmod(0o700)


def test_marker_and_depth_bound_discovery(tmp_path: Path) -> None:
    (tmp_path / ".base-cli.yaml").write_text("environment: test\n")
    child = tmp_path / "project"
    child.mkdir()
    (child / ".git").write_text("gitdir: elsewhere\n")
    assert CliProfile.batteries_included("trust").discover_project(child) is None
    assert CliProfile.batteries_included("trust", project_boundary_marker=None).discover_project(child)
    assert (
        CliProfile.batteries_included(
            "trust", project_boundary_marker=None, max_project_ancestor_depth=0
        ).discover_project(child)
        is None
    )
    assert CliProfile.generic().discover_project(tmp_path) is None


def test_yaml_input_size_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "large.yaml"
    path.write_bytes(b"value: " + b"x" * 1_048_576)
    with pytest.raises(ConfigurationError, match="maximum size"):
        load_yaml_file(path)


def test_project_environment_file_has_same_trust_gate(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission contract")
    (tmp_path / ".base-cli.yaml").write_text("environment: test\n")
    environment = tmp_path / "environments" / "test.yaml"
    environment.parent.mkdir()
    environment.write_text("keep_temp: true\n")
    environment.chmod(0o666)
    profile = CliProfile.batteries_included("trust", user_config_dir=tmp_path / "user")
    project = profile.discover_project(tmp_path)
    with pytest.raises(ConfigurationError, match="Untrusted.*test.yaml"):
        profile.load_config(project, None)


def test_merge_alias_expansion_is_bounded_before_construction(tmp_path: Path) -> None:
    path = tmp_path / "aliases.yaml"
    lines = ["level0: &level0 {key: value}"]
    for depth in range(1, 8):
        aliases = ", ".join([f"*level{depth - 1}"] * 10)
        lines.append(f"level{depth}: &level{depth} {{<<: [{aliases}]}}")
    path.write_text("\n".join(lines))
    with pytest.raises(ConfigurationError, match="expansion"):
        load_yaml_file(path)
