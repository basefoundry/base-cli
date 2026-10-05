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
highly sensitive to timer granularity. Warm budgets apply to all non-persistence
base-cli feature scenarios; file persistence has a separate platform budget
because runner filesystems vary materially. Percentile gates catch practical
regressions while keeping noisy single maxima visible without making one
scheduler outlier block a change.

| Budget (p95) | Unix | macOS | Windows | WSL2 |
| --- | ---: | ---: | ---: | ---: |
| Cold import, including interpreter startup | 750 ms | 750 ms | 1,000 ms | 1,000 ms |
| Cold no-op invocation, including startup and dispatch | 2,000 ms | 2,000 ms | 4,000 ms | 4,000 ms |
| Base-cli lifecycle increment over Click warm dispatch | 5 ms | 5 ms | 15 ms | 15 ms |
| Warm invocation and non-persistence feature scenarios | 50 ms | 50 ms | 100 ms | 100 ms |
| File-persistence-enabled scenario | 125 ms | 125 ms | 250 ms | 50 ms |

An initial 31-sample local calibration on macOS (Python 3.14.6, Apple Silicon)
measured approximately 101 ms for base-cli cold import, 0.56 ms for warm
lifecycle dispatch, and 15.9 ms p95 for file-persisted logging. These are
development-host measurements, not adoption claims or release comparisons.
The first hosted 31-sample baseline measured file-persistence p95 at 22 ms on
Ubuntu, 21 ms on macOS, 158 ms on Windows, and 27 ms on WSL2. Windows also had
a high median absolute deviation (26 ms), so persistence has its own Windows
budget instead of weakening other warm-scenario gates. These measurements are
CI calibration evidence, not adoption claims or release comparisons; review
subsequent retained artifacts before tightening platform budgets.

October 2026 hosted recalibration separates sustained persistence cost from
filesystem tails on Unix/macOS: median must remain at most **50 ms** and p95
at most **125 ms**. The previous 50 ms p95 cap repeatedly rejected otherwise
unchanged runtime code, including the validation-only PR. Observed pairs were
14.66/118.04 ms (Unix median/p95) and 24.93/61.93 and 26.12/87.37 ms (macOS).
Evidence: [Unix run](https://github.com/basefoundry/base-cli/actions/runs/37048785893)
and [macOS validation-only run](https://github.com/basefoundry/base-cli/actions/runs/37052368353).
A sustained slowdown over 50 ms still fails; p95 over 125 ms also fails.
Windows, WSL, parser, import, and non-persistence limits are unchanged.

Each report is versioned as `base-cli.benchmark` schema version 2 and contains
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
continues from the filesystem. An atomic advisory cursor rotates the bounded
byte-size walk across invocations, including after process restart; the index
is an observation aid, never an authorization to delete a path. The retention regression suite covers count,
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

### Logging hot path

Secure log handlers keep their private sidecar descriptor open until cleanup,
with an advisory lock around each append and a fresh descriptor after fork.
Human formatters cache up to 256 source paths for the current invocation and
project binding. Repeated paths require no filesystem resolution. Sidecar I/O
errors are routed through `logging.Handler.handleError` and do not fail commands.

### Concurrent retention

Retention takes a nonblocking maintenance lock on POSIX and Windows. A busy lock
skips that pass at debug level; the next successful invocation reconciles policy
debt. Startup and teardown both acquire the lock before scanning. Command work
never waits for a stopped lock holder. Deletion still revalidates metadata and
leases under the lock.

`RetentionPolicy.safe_defaults()` includes `max_total_bytes=512 MiB`, so the
default policy uses the byte-policy recursive-walk bounds described above. A
consumer that needs only count/age retention can explicitly omit the byte cap.
The concurrent benchmark in #391 measures up to twelve processes against one
warmed cache. It caps each batch at the runner's available CPU count and uses
additional batches on smaller runners so the p95-to-serial ratio is less
dominated by scheduler oversubscription.

### Benchmark report v2: contention and logging

`base-cli.benchmark` schema version 2 adds `results.base-cli.stress` and
`stress_budgets` with explicit units. Twelve synchronized subprocesses share a
cache warmed past the default 20-bundle cap. Three batches report 36 invocation
samples, serial/concurrent p95 milliseconds, and their p95 ratio; process import
and the start barrier are outside the invocation timer. Logging measures 3,000
INFO records per sample through a real lifecycle, both with and without persistent
files, reporting microseconds/record and records/second.

| Profile | Concurrent / serial p95 cap | Log p95 microseconds/record cap |
| --- | ---: | ---: |
| unix | 6 | 80 |
| macos | 6 | 200 |
| windows | 10 | 150 |
| wsl | 10 | 100 |

These initial hosted caps allow scheduling/filesystem variation while detecting
material regressions. `--check` rejects missing, nonfinite, and over-budget stress
metrics. Reports retain the same CI artifact name and 90-day retention with the
v2 schema marker; consumers must branch on that marker. Local and first hosted
measurements are retained with this PR before further tightening of the caps.

Development-host calibration (macOS, Python 3.14.6, 31 serial/log samples and
36 concurrent samples): concurrent/serial p95 ratio 2.42; ephemeral logging p95
6.20 microseconds/record; persistent logging p95 14.15 microseconds/record.
These are local measurements; hosted per-profile results are retained separately.

The [first hosted stress run](https://github.com/basefoundry/base-cli/actions/runs/37054920383)
recorded the following 31-sample logging and 36-sample concurrency results:

| Profile | Concurrent / serial p95 | Ephemeral log p95 (us/record) | Persistent log p95 (us/record) |
| --- | ---: | ---: | ---: |
| macos (3-core arm64, Python 3.13) | 4.49 | 16.01 | 46.42 |
| windows | 2.99 | 20.14 | 49.03 |
| wsl | 4.25 | 11.44 | 22.90 |

The retained Unix stress artifact measured 55.58 us/record p95. The initial macOS
40 us/record p95 estimate rejected a run whose persistent median
was 25.82 us/record. The first retained macOS stress run reported 46.42 us/record,
but a repeat on the same 3-core arm64 profile reached 129.66 us/record p95 while
the functional and platform validation jobs stayed green. The hosted macOS cap is
therefore 200 us/record: it retains a meaningful guard above the observed runner
tail without turning filesystem scheduling variance into a false merge blocker.
The corresponding `base-cli-benchmark-{profile}-37054920383` and
`base-cli-benchmark-macos-37297556739` artifacts contain machine metadata and all
measured summaries. The Unix calibration is covered by the hosted evidence linked
above.
