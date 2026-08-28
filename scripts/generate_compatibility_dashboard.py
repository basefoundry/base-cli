#!/usr/bin/env python3
"""Generate the compatibility dashboard from package and CI declarations."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import tomllib

MATRIX_PATTERN = re.compile(r"(?m)^[ \t]+(?P<key>[a-z-]+):\s*\[(?P<values>[^\]]*)\]")
QUOTED_VALUE = re.compile(r"['\"]([^'\"]+)['\"]")


def _matrix_values(path: Path, key: str) -> list[str]:
    """Read a simple quoted matrix array from a workflow file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    inline_pattern = re.compile(rf"^(?P<indent>\s+){re.escape(key)}:\s*\[(?P<values>[^\]]*)\]")
    block_pattern = re.compile(rf"^(?P<indent>\s+){re.escape(key)}:\s*$")
    for index, line in enumerate(lines):
        inline_match = inline_pattern.match(line)
        if inline_match is not None:
            return QUOTED_VALUE.findall(inline_match.group("values"))
        block_match = block_pattern.match(line)
        if block_match is None:
            continue
        base_indent = len(block_match.group("indent"))
        values: list[str] = []
        for child in lines[index + 1 :]:
            if not child.strip():
                continue
            indent = len(child) - len(child.lstrip())
            value_match = re.match(r"^\s+-\s*(?:['\"])?([^'\"\s]+)(?:['\"])?\s*$", child)
            if indent <= base_indent:
                break
            if value_match is not None:
                values.append(value_match.group(1))
        if values:
            return values

    text = "\n".join(lines)
    for match in MATRIX_PATTERN.finditer(text):
        if match.group("key") == key:
            return QUOTED_VALUE.findall(match.group("values"))
    raise ValueError(f"{path} has no matrix array for {key!r}")


def _dependency_window(dependencies: list[str], name: str) -> str:
    for dependency in dependencies:
        if dependency.lower().startswith(name.lower()):
            return dependency
    raise ValueError(f"pyproject.toml has no dependency window for {name!r}")


def generate_dashboard(root: Path) -> str:
    """Return deterministic Markdown generated from repository declarations."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = [str(value) for value in pyproject["dependencies"]]
    optional = pyproject["optional-dependencies"]
    typer_dependencies = [str(value) for value in optional["typer"]]
    yaml_dependencies = [str(value) for value in optional["yaml"]]

    tests_workflow = root / ".github/workflows/tests.yml"
    dependency_workflow = root / ".github/workflows/dependency-matrix.yml"
    compatibility_workflow = root / ".github/workflows/compatibility.yml"
    python_versions = _matrix_values(tests_workflow, "python-version")
    native_platforms = _matrix_values(tests_workflow, "os")
    click_versions = _matrix_values(dependency_workflow, "click-version")
    pyyaml_versions = _matrix_values(dependency_workflow, "pyyaml-version")
    typer_versions = _matrix_values(compatibility_workflow, "typer-version")

    distribution_names = re.findall(
        r"(?m)^\s+- name: (?P<name>(?:Debian|Fedora)[^\n]*)$",
        tests_workflow.read_text(encoding="utf-8"),
    )
    platforms = [*native_platforms, *distribution_names, "WSL2"]
    python_text = ", ".join(f"Python {version}" for version in python_versions)
    click_text = ", ".join(version.removesuffix(".*") for version in click_versions)
    yaml_text = ", ".join(version.removesuffix(".*") for version in pyyaml_versions)
    typer_text = ", ".join(typer_versions)
    platform_text = ", ".join(platforms)
    click_window = _dependency_window(dependencies, "click")
    yaml_window = _dependency_window(yaml_dependencies, "PyYAML")
    typer_window = _dependency_window(typer_dependencies, "typer")

    return f"""# Compatibility dashboard

[![Tests](https://img.shields.io/github/actions/workflow/status/basefoundry/base-cli/tests.yml?branch=main&label=tests)](https://github.com/basefoundry/base-cli/actions/workflows/tests.yml)
[![Dependency matrix](https://img.shields.io/github/actions/workflow/status/basefoundry/base-cli/dependency-matrix.yml?branch=main&label=dependencies)](https://github.com/basefoundry/base-cli/actions/workflows/dependency-matrix.yml)
[![Reference consumers](https://img.shields.io/github/actions/workflow/status/basefoundry/base-cli/compatibility.yml?branch=main&label=consumers)](https://github.com/basefoundry/base-cli/actions/workflows/compatibility.yml)

This page is generated from the package metadata and the matrices in
`.github/workflows/tests.yml`, `dependency-matrix.yml`, and
`compatibility.yml`. The badges above reflect the latest completed `main`
workflow runs; a green badge means the declared matrix passed, while a gray or
red badge means that evidence is pending or needs investigation.

## Current support contract

| Surface | Declared support | Completed CI coverage | Interpretation |
| --- | --- | --- | --- |
| Python | `>=3.10,<4` | {python_text} | Supported and tested |
| Click | `{click_window}` | {click_text} | Supported and tested on Python 3.10 and 3.14 |
| PyYAML extra | `{yaml_window}` | {yaml_text} | Supported when `base-cli[yaml]` is installed |
| Typer extra | `{typer_window}` | {typer_text} | Supported through `attach_typer()` |
| Platforms | Pure-Python core | {platform_text} | Supported tiers documented below |

## What the labels mean

- **Supported and tested** means the combination is declared by package
  metadata and exercised by a named CI matrix.
- **Supported, not exhaustive** means the package contract applies, but CI
  samples representative versions or platforms rather than every patch level.
- **Untested** means a combination may install but is not a support claim.
- **Unsupported** means package metadata or an explicit policy excludes it;
  reports from that combination are welcome but are not release blockers.

The platform boundary is detailed in
[`platform-support.md`](platform-support.md), and dependency-window changes
must follow [`dependency-support.md`](dependency-support.md). Reference
consumer results are compatibility evidence, not customer adoption claims; see
[`adoption-evidence.md`](adoption-evidence.md).

## Updating the dashboard

Do not edit this page's matrix by hand. When a package window or CI matrix
changes, run:

```bash
python scripts/generate_compatibility_dashboard.py
python scripts/generate_compatibility_dashboard.py --check
```

The CI quality and documentation jobs fail when the generated page is stale.
Update the dashboard and the relevant support-policy/release-note entry in the
same pull request. The generator intentionally records declarations and live
workflow links, while GitHub Actions remains the source of truth for the most
recent run outcome.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/compatibility-dashboard.md"))
    parser.add_argument("--check", action="store_true", help="fail when output is stale")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    generated = generate_dashboard(root)
    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"compatibility dashboard validation failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        if current != generated:
            print(
                f"compatibility dashboard validation failed: {args.output} is stale; run the generator",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"Validated generated compatibility dashboard: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generated, encoding="utf-8")
    print(f"Generated compatibility dashboard: {args.output}")


if __name__ == "__main__":
    main()
