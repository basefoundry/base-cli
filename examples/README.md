# Reference applications

These four small applications are maintained as installable, copy-pasteable
reference implementations for `base-cli`. They are intentionally independent
packages: install the framework first, then install any example from its own
directory.

| Example | Demonstrates | Launcher |
| --- | --- | --- |
| [`minimal_cli`](minimal_cli/README.md) | A single `base_cli.App` command | `base-minimal` |
| [`nested_click_app`](nested_click_app/README.md) | Native Click groups and plugin discovery | `base-nested` |
| [`typer_app`](typer_app/README.md) | Typed Typer commands through the optional adapter | `base-typer` |
| [`automation_observability_app`](automation_observability_app/README.md) | JSON output, dry runs, Rich, and telemetry | `base-automation` |

## End-to-end smoke flow

From a checkout of `base-cli`, build and install the framework wheel, then
install one or more examples. This is the same order used by CI, so it tests
the public, installed package rather than the repository import path:

```bash
python -m pip install --upgrade build
python -m build --wheel
python -m pip install dist/base_cli-*.whl
python -m pip install examples/minimal_cli
base-minimal --help
base-minimal --name Ada
```

Each example README contains the equivalent install, configuration, output and
error behavior, test, shell completion, release, and troubleshooting sections.
For a production rollout, pin `base-cli` and the example's optional extras,
run the package's tests in CI, and publish a wheel and signed provenance
metadata from a clean build environment.
