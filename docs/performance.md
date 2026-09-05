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

The CI quality job checks the base-cli sample p95 against these budgets. The
benchmark records the selected platform profile in both text and JSON output;
set `BASE_CLI_BENCHMARK_PLATFORM` when a runner's filesystem or virtualization
boundary is not represented by the host operating system. Supported profiles
are `unix`, `macos`, `windows`, and `wsl`.

| Measurement | Budget |
| --- | ---: |
| Fresh `import base_cli` (native Unix/macOS) | 750 ms |
| Fresh `import base_cli` (native Windows) | 1,000 ms |
| Fresh `import base_cli` (WSL2 on a Windows-mounted checkout) | 1,000 ms |
| Isolated invocation and runtime filesystem setup | 1,500 ms |

The benchmark reports the median, p95, and maximum for seven samples. Pass
`--json` for a stable machine-readable result suitable for archiving or CI
comparison. These
budgets are intentionally broad enough for hosted runners while still
detecting accidental quadratic startup work, unbounded metadata scans, or
unexpected dependency imports. A performance improvement should preserve the
same lifecycle and persistence assertions covered by the adversarial tests.

## Retention recovery work bounds

Run-bundle recovery is intentionally incremental. Discovery reads direct-child
metadata and does not recursively size bundles unless a `max_total_bytes`
decision requires it. The following deterministic bounds apply to each
foreground pass (protected bundles and unreadable entries are retained):

| Fixture | Metadata entries considered | Recursive size walks | Bundle removals | Index entries written |
| --- | ---: | ---: | ---: | ---: |
| 20 bundles | 20 | 0 for count/age policies; up to 20 for byte policy | up to 20 | up to 20 |
| 2,000 bundles | 2,000 | up to 512 for byte policy | up to 256 | up to 512 |
| 10,000 bundles | 10,000 | up to 512 for byte policy | up to 256 | up to 512 |

When a bound prevents a complete reconciliation, base-cli leaves the
unprocessed bundles intact, writes a partial index with `complete: false`, and
emits a warning describing the remaining policy debt. A later invocation
continues from the filesystem; the index is an observation aid, never an
authorization to delete a path. The retention regression suite covers count,
age, byte limits, deep trees, corrupt metadata/index files, unreadable files,
concurrent invocations, and live-run lease protection.

The regression suite uses deterministic Hypothesis examples (`derandomize`
enabled), fixed multiprocessing workloads, and explicit seed values in every
worker payload. Property cases cover redaction and command-protocol framing;
spawned processes cover history append, private metadata replacement, logging,
extension discovery caches, and run-bundle retention. Ctrl+C is tested through
both the lifecycle boundary and a real POSIX subprocess signal. Windows keeps
the portable lifecycle and persistence checks while skipping only assertions
that require POSIX signal or descriptor semantics.
