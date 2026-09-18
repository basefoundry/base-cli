# Performance and adversarial-regression contract

`base-cli` treats startup and filesystem behavior as part of its public
quality contract. The benchmark is a comparative regression check, not a claim
that a lifecycle framework should outpace bare parsers. Its scenarios separate
interpreter/import cost, parser dispatch, the base-cli lifecycle, optional
features, and persistence.

Install the complete local validation set, including every comparator:

```bash
python -m pip install '.[dev,typer,quality,benchmark]'
python scripts/benchmark_runtime.py --check --iterations 31 --output benchmark-results.json
```

The benchmark is also part of the local aggregate:

```bash
./tests/full_validate.sh --gate benchmark
```

## Scenario contract

The comparative set is Click, Typer, Cyclopts, and base-cli. Each framework
registers an equivalent zero-argument no-op command. Fresh-process
measurements include Python startup, framework import, command construction,
and dispatch through the framework's normal entry point. Warm parser samples
reuse command objects; Click and Typer use Click's `CliRunner`, Cyclopts uses
its `App` call, and base-cli reports both a shared Click-runner lifecycle
sample and an end-to-end `base_cli.testing.invoke()` sample. The runner shape
for each value is recorded here so comparisons do not imply identical
mechanisms where framework APIs differ.

Base-cli-only feature samples cover:

- successful and failed JSON envelopes;
- debug diagnostics on the user stream;
- nested-command dispatch;
- persistence disabled versus enabled, with the same log event in both cases.

These feature costs are reported separately from parser comparisons. JSON
success/error and nested dispatch use the public `App`/lifecycle API; persistence
samples differ only in whether file logging is enabled. Measurements are
in-process, warm, and use isolated temporary homes.

## CI budgets and evidence

CI collects 31 samples per scenario on Python 3.13 for each supported
benchmark profile: native Unix, macOS, Windows, and WSL2. `--check` fails if a
required comparator or scenario is missing, a p95 exceeds its profile budget,
or the measured base-cli lifecycle increment over Click exceeds its separate
profile budget. The lifecycle-to-Click ratio remains visible for interpretation,
but is not itself gated because Click's sub-millisecond baseline makes ratios
highly sensitive to timer granularity. The warm budgets also apply to each
base-cli feature scenario. Percentile gates catch practical regressions while
keeping noisy single maxima visible without making one scheduler outlier block
a change.

| Budget (p95) | Unix | macOS | Windows | WSL2 |
| --- | ---: | ---: | ---: | ---: |
| Cold import, including interpreter startup | 750 ms | 750 ms | 1,000 ms | 1,000 ms |
| Cold no-op invocation, including startup and dispatch | 2,000 ms | 2,000 ms | 4,000 ms | 4,000 ms |
| Base-cli lifecycle increment over Click warm dispatch | 5 ms | 5 ms | 15 ms | 15 ms |
| Warm invocation and base-cli feature scenarios | 50 ms | 50 ms | 100 ms | 100 ms |

An initial 31-sample local calibration on macOS (Python 3.14.6, Apple Silicon)
measured approximately 101 ms for base-cli cold import, 0.56 ms for warm
lifecycle dispatch, and 15.9 ms p95 for file-persisted logging. These are
development-host measurements, not adoption claims or release comparisons.
The first complete hosted run records the corresponding four-platform
baselines; review that evidence before tightening any platform budget.

Each report is versioned as `base-cli.benchmark` schema version 1 and contains
the package version, source revision, UTC timestamp, platform profile, Python
version/ABI, OS release, architecture, CPU count, sample count, medians, p95,
maximum, median absolute deviation, and parser/lifecycle comparison values.
The Tests workflow retains a distinct JSON artifact for each platform profile
for 90 days. Download the artifact from the corresponding `Benchmark (...)`
or `Validate (WSL)` Actions job to compare dated runs.

The profile can be selected explicitly with
`BASE_CLI_BENCHMARK_PLATFORM` when a runner's filesystem or virtualization
boundary is not represented by its host OS. Supported values are `unix`,
`macos`, `windows`, and `wsl`.

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
