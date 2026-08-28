# Compatibility dashboard

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
| Python | `>=3.10,<4` | Python 3.10, Python 3.11, Python 3.12, Python 3.13, Python 3.14 | Supported and tested |
| Click | `click>=8.1,<8.5` | 8.1, 8.2, 8.3, 8.4 | Supported and tested on Python 3.10 and 3.14 |
| PyYAML extra | `PyYAML>=6.0,<7` | 6.0 | Supported when `base-cli[yaml]` is installed |
| Typer extra | `typer>=0.12,<0.28` | 0.25.1, 0.26.0, 0.27.1 | Supported through `attach_typer()` |
| Platforms | Pure-Python core | macos-latest, ubuntu-latest, windows-latest, Debian 12, Fedora latest, WSL2 | Supported tiers documented below |

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
