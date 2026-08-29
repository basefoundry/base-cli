# Dependency support matrix

This page is the human-readable dependency contract for the current release
line. The package metadata is authoritative for installation; this matrix
documents the versions covered by CI and the process for widening a window.
The generated [compatibility dashboard](compatibility-dashboard.md) combines
these dependency declarations with the platform and adapter matrices.

## Core runtime

| Dependency | Declared window | CI coverage | Policy |
| --- | --- | --- | --- |
| Python | `>=3.10,<4` (CPython 3.10--3.14) | Every OS test job | Drop an end-of-life line only in a documented compatibility release |
| Click | `>=8.1,<8.5` | 8.1, 8.2, 8.3, and 8.4 lines on Python 3.10 and 3.14 | Add a matrix lane and compatibility note before widening the upper bound |
| YAML extra | `PyYAML>=6.0,<7` | 6.0 line on Python 3.10 and 3.14 | Install `base-cli[yaml]`; keep parser behavior covered by profile tests |

The `base-cli[yaml]` extra is the minimal installation for YAML profiles.
Generic consumers can install the core package without PyYAML.

## Optional integrations

Optional integrations are intentionally independent of the core window:

| Extra | Declared window | Compatibility evidence |
| --- | --- | --- |
| `typer` | `>=0.12,<0.28` | Typer 0.25.1, 0.26.0, 0.27.1, and 0.27.2 across Python 3.10--3.14 |
| `rich` | `>=13.7,<15` | Integration tests and graceful-degradation checks |
| `telemetry` | `>=1.24,<2` | Telemetry integration tests |

## Updating a window

1. Add the candidate lower or upper line to the dependency matrix. The
   current supported Click window is 8.1 through 8.4; versions outside that
   window are rejected by package metadata until they are evaluated.
2. Run the full test, type-check, documentation, and installed-wheel gates.
3. Review release notes and consumer impact, including resolver behavior.
4. Update `pyproject.toml`, this page, and the API stability contract in one
   pull request.

An untested major version is not considered supported merely because it can be
installed successfully.
