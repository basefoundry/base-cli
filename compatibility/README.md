# Downstream compatibility consumers

These are three maintained reference consumer fixtures used as compatibility
evidence. They are deliberately separate packages with separate names, entry
points, and test suites; none imports another fixture or Base product code.
They are not independent adopters and must not be presented as customer
outcomes.

| Consumer | Shape | Use case | Compatibility outcome |
| --- | --- | --- | --- |
| [Atlas](consumers/atlas_click/README.md) | Existing Click group | Inventory/status automation | Existing tree remains intact after `attach()`. |
| [Beacon](consumers/beacon_typer/README.md) | Typed Typer app | Deployment command | Typer validation/help remains native through `attach_typer()`. |
| [Cinder](consumers/cinder_automation/README.md) | Native `App` | Scheduled reconciliation | Dry-run and JSON output are deterministic and safe. |

## How the evidence is retained

`scripts/validate_consumers.py` validates the manifest, package metadata, and
required compatibility documentation. The `Reference consumers` workflow
builds and installs the base-cli wheel first, installs each consumer without
dependency resolution, verifies that the installed distribution still matches
the source release, and runs each consumer's tests. This prevents pip from
silently replacing the release candidate with an older published wheel.

The downstream job records a dated JSON result as the
`base-cli-compatibility-evidence-<run-id>` artifact. It binds the result to the
framework revision and version and lists the exact fixture and matrix that
passed. The record remains `schema_version: 1`; `source_version` identifies
the checked-out `VERSION`, while `framework_version` identifies the installed
distribution. These are additive metadata fields, so readers should not reject
the record merely because a newer record contains additional fields. See
[`adoption-evidence.md`](../docs/adoption-evidence.md) for the claim and
permission boundary.

The same workflow runs the Typer adapter and Beacon fixture against Typer
0.25.1, 0.26.0, 0.27.1, and 0.27.2 on Python 3.10 through 3.14. This matrix
covers the transition from Click's public command classes to Typer's vendored
Click fork.

Run the same checks locally:

```bash
python scripts/validate_consumers.py
python -m build --wheel
python -m pip install dist/base_cli-*.whl
for consumer in compatibility/consumers/*; do python -m pip install --no-deps "$consumer"; done
python -m pip check
for tests in compatibility/consumers/*/tests; do python -m pytest "$tests"; done
```

The fixture metadata targets `base-cli>=0.5,<0.6`. Dependency-resolving
installation therefore requires the 0.5 package to be available from the
configured index. The workflow instead installs the reviewed wheel first,
installs each fixture with `--no-deps`, verifies the installed framework
version, and then runs `pip check` so the compatibility result cannot silently
fall back to an older published framework.

The fixtures are not customer claims. A permissioned public adopter can be
added as a separate record while retaining the same downstream contract tests.
