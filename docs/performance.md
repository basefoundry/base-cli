# Performance and adversarial-regression contract

`base-cli` treats startup and filesystem behavior as part of its public
quality contract. The checked benchmark is intentionally small and runs from
the source checkout:

```bash
python scripts/benchmark_runtime.py --check
```

It records fresh-process import time and the cost of an isolated production
invocation through `base_cli.testing.invoke`. The comparison mode measures
equivalent no-op commands for base-cli, Click, Typer, and (when installed)
Cyclopts. Install the optional benchmark extra to include Cyclopts:

```bash
python -m pip install 'base-cli[benchmark]'
```

The CI quality job checks the base-cli sample p95 against these budgets:

| Measurement | Budget |
| --- | ---: |
| Fresh `import base_cli` | 750 ms |
| Isolated invocation and runtime filesystem setup | 1,500 ms |

The benchmark reports the median, p95, and maximum for seven samples. Pass
`--json` for a stable machine-readable result suitable for archiving or CI
comparison. These
budgets are intentionally broad enough for hosted runners while still
detecting accidental quadratic startup work, unbounded metadata scans, or
unexpected dependency imports. A performance improvement should preserve the
same lifecycle and persistence assertions covered by the adversarial tests.

The regression suite uses deterministic Hypothesis examples (`derandomize`
enabled), fixed multiprocessing workloads, and explicit seed values in every
worker payload. Property cases cover redaction and command-protocol framing;
spawned processes cover history append, private metadata replacement, logging,
extension discovery caches, and run-bundle retention. Ctrl+C is tested through
both the lifecycle boundary and a real POSIX subprocess signal. Windows keeps
the portable lifecycle and persistence checks while skipping only assertions
that require POSIX signal or descriptor semantics.
