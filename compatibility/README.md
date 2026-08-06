# Downstream compatibility consumers

These are three independent, maintainable consumer fixtures used as adoption
evidence until a non-Base team grants permission for a public case study. They
are deliberately separate packages with separate names, entry points, and test
suites; none imports another fixture or Base product code.

| Consumer | Shape | Use case | Compatibility outcome |
| --- | --- | --- | --- |
| [Atlas](consumers/atlas_click/README.md) | Existing Click group | Inventory/status automation | Existing tree remains intact after `attach()`. |
| [Beacon](consumers/beacon_typer/README.md) | Typed Typer app | Deployment command | Typer validation/help remains native through `attach_typer()`. |
| [Cinder](consumers/cinder_automation/README.md) | Native `App` | Scheduled reconciliation | Dry-run and JSON output are deterministic and safe. |

## How the evidence is retained

`scripts/validate_consumers.py` validates the manifest, package metadata, and
required compatibility documentation. The `Reference consumers` workflow
builds and installs the base-cli wheel first, installs each consumer with its
own dependencies, and runs each consumer's tests. This catches import,
packaging, adapter, and contract regressions without relying on repository
source imports.

The same workflow runs the Typer adapter and Beacon fixture against Typer
0.25.1, 0.26.0, and 0.27.1 on Python 3.10 through 3.14. This matrix covers the
transition from Click's public command classes to Typer's vendored Click fork.

Run the same checks locally:

```bash
python scripts/validate_consumers.py
python -m build --wheel
python -m pip install dist/base_cli-*.whl
for consumer in compatibility/consumers/*; do python -m pip install "$consumer"; done
for tests in compatibility/consumers/*/tests; do python -m pytest "$tests"; done
```

The fixtures are not customer claims. A permissioned public adopter can be
substituted in the manifest while retaining the same downstream contract tests.
