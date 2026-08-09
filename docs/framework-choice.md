# Framework choice guide

`base-cli` is intentionally a lifecycle layer, not a replacement parser. The
right choice depends on whether a project needs only argument parsing or also
needs a repeatable operational contract around every invocation.

## Comparison

| Concern | Click | Typer | base-cli |
| --- | --- | --- | --- |
| Argument parsing and command trees | Core capability | Click-based, type-hint-friendly layer | Uses Click and can attach to Typer trees |
| Consistent per-run context | Consumer-defined | Consumer-defined | `Context` carries paths, config, logging, and cleanup |
| Logging and diagnostics | Application-defined | Application-defined | Structured stderr logging and persistent run metadata |
| Configuration policy | Application-defined | Application-defined | Consumer-owned `CliProfile` boundary with optional batteries |
| Cleanup and temporary state | Application-defined | Application-defined | Deterministic lifecycle hooks and per-run paths |
| Machine-readable contracts | Application-defined | Application-defined | Versioned JSON and record/output contracts |
| Best fit | A small or custom command surface | Typed Click applications | Production CLIs that need consistent operations across commands |

This is a boundary comparison, not a feature-count ranking. Click and Typer
remain the parser and command-definition choices; base-cli composes with them
when the application also needs lifecycle, diagnostics, and compatibility
contracts.

## Five-minute evaluation

1. Install the wheel in a clean environment:

   ```bash
   python -m pip install base-cli
   ```

2. Copy the [minimal command](https://github.com/basefoundry/base-cli/tree/main/examples/minimal_cli)
   and run its test suite.

3. Add one command-specific option and confirm that logs remain on stderr
   while command output remains on stdout.

4. Run the same command with `--debug` and `--keep-temp`, then inspect the
   run context and retained log paths.

5. If the application already uses Typer, install `base-cli[typer]` and
   follow the [Typer adapter guide](typer-adapter.md).

For a production migration checklist, continue with
the [adopter readiness guide](adopter-readiness.md).
