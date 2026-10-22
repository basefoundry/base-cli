# Starter template

The maintained starter is [`examples/minimal_cli`](../examples/minimal_cli/).
It is deliberately a copyable project rather than an interactive generator:
the first five minutes should expose the framework's public boundaries instead
of hiding them behind template machinery.

## Five-minute proof

Copy the directory into a clean location and run the same commands a new
consumer would use:

```bash
cp -R examples/minimal_cli my-cli
cd my-cli
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
python -m pip install -e .
base-minimal --name Ada
python -m pytest
```

The starter has no private-repository dependency and uses only the public
`base_cli` facade. Its tests cover a successful invocation, lifecycle help,
and the missing-option error boundary. The repository's example workflow
installs and tests it as an installed consumer as well.

## What to customize

The command implementation belongs in
`src/minimal_cli/cli.py`; the package and console-script names belong in
`pyproject.toml`; product behavior belongs in `tests/` and `README.md`.
Keep `base_cli.run_app(app)` as the process boundary and do not import
framework-private modules. Add JSON, configuration, or optional integrations
only when the product needs them, following the corresponding contract docs.

The starter's `base-cli>=0.5.1,<1.0` requirement is a release-line guard for
the current pre-1.0 framework. It should be updated deliberately when the
consumer adopts base-cli 1.0.

## Why there is no `base-cli init` command yet

The copyable starter is the 1.0 adoption surface. A generator would add a
second templating and upgrade contract before there is enough consumer
feedback to stabilize it. Interactive scaffolding remains a follow-up design
item; it must not become a hidden dependency of the core package.
