from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
STARTER = ROOT / "examples" / "minimal_cli"
STARTER_DOC = ROOT / "docs" / "starter-template.md"


def test_starter_has_the_copyable_project_boundaries() -> None:
    for relative in (
        "README.md",
        "pyproject.toml",
        "src/minimal_cli/cli.py",
        "src/minimal_cli/__init__.py",
        "tests/test_cli.py",
    ):
        assert (STARTER / relative).is_file(), relative


def test_starter_pins_the_current_pre_one_release_line() -> None:
    metadata = (STARTER / "pyproject.toml").read_text(encoding="utf-8")
    assert 'base-cli>=0.5.1,<1.0' in metadata


def test_starter_docs_explain_copying_and_safe_customization() -> None:
    text = STARTER_DOC.read_text(encoding="utf-8")
    for required in (
        "examples/minimal_cli",
        'python -m pip install -e ".[dev]"',
        "python -m pytest",
        "base_cli.run_app(app)",
        "no `base-cli init` command yet",
    ):
        assert required in text
