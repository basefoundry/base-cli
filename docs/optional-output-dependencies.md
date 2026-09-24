# Optional output dependencies

The core `base-cli` package keeps its dependency boundary small. Install an
extra only when the consumer selects the corresponding integration or output
path; do not add optional dependencies to the core runtime just to make a
format available.

## Capability map

| Capability | Install extra | Core or optional behavior |
| --- | --- | --- |
| Human text, CSV, TSV, JSON, and NDJSON records | none | Available from the core package through `base_cli.output`; redirected text is TSV. |
| YAML configuration or YAML output | `base-cli[yaml]` | Provides `PyYAML>=6.0,<7`; loading or rendering YAML without it fails with an actionable error. |
| Typer application adapter | `base-cli[typer]` | Provides `typer>=0.25.1,<0.28`; importing the adapter without it fails with an actionable error. |
| Rich interactive tables | `base-cli[rich]` | Optional presentation enhancement; the built-in deterministic table remains the fallback. |
| OpenTelemetry lifecycle spans | `base-cli[telemetry]` | Optional instrumentation; missing or unhealthy telemetry is a no-op for command status. |

The authoritative version windows are in [Dependency Support](dependency-support.md)
and `pyproject.toml`. The complete rendering rules are in [Output Contracts](output-contracts.md).

## YAML: install it or choose a core format

When a consumer intentionally renders YAML, install the extra in the same
environment as the package:

```bash
python -m pip install 'base-cli[yaml]'
```

Without PyYAML, the public renderer reports:

```text
PyYAML is required for YAML output. Install the optional dependency with `python -m pip install 'base-cli[yaml]'`.
```

If YAML is not required, choose JSON, CSV, or NDJSON in the consumer's command
before calling the renderer. JSON and NDJSON are available from the core
package and are usually the safest machine-facing fallback; the consumer must
still document which fields it reads.

## Consumer-side selection

The framework does not invent a format flag for an application. A consumer
owns the option and can pass the selected value to the public renderer:

```python
base_cli.render_records(
    records,
    requested_format=output_format,
    columns=(("NAME", "name"),),
)
```

When `output_format` is `yaml`, the consumer should either install
`base-cli[yaml]` or return the documented dependency diagnostic. When it is
`json` or `ndjson`, no optional output dependency is needed. Do not silently
turn an explicitly requested YAML result into another format; make the
fallback visible and preserve a nonzero status when the requested contract is
part of the command's required behavior.

## Other optional integrations

The same boundary applies outside output formats:

- `base-cli[typer]` is required before calling `base_cli.attach_typer()`; the
  adapter's current error names the install command and the `typer` extra.
- `base-cli[rich]` is an optional human-table enhancement. Missing Rich does
  not change CSV, TSV, JSON, or NDJSON output, and the built-in text renderer
  remains available.
- `base-cli[telemetry]` enables lifecycle spans. A missing API package,
  invalid provider, or failing exporter is logged at debug level and must not
  change the command's exit status or cleanup behavior.

Validate the selected path from both an installed package and a source
checkout when it matters to the consumer. Keep the extra, Python version, and
base-cli version in the consumer lock file; consult [API Stability](api-stability.md)
before widening a dependency window. This page documents current behavior and
does not change package metadata or runtime error classification.
