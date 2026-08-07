#!/usr/bin/env python3
"""Track import and isolated invocation costs with stable, checked budgets."""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IMPORT_P95_BUDGET_MS = 750.0
INVOCATION_P95_BUDGET_MS = 1_500.0
DEFAULT_ITERATIONS = 7


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"number of samples per benchmark (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the documented p95 budgets are exceeded",
    )
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("--iterations must be at least 3")

    import_samples = _measure_import(args.iterations)
    invocation_samples = _measure_invocations(args.iterations)
    metrics = {
        "import_ms": _summary(import_samples),
        "invocation_ms": _summary(invocation_samples),
    }
    print("import_ms: median={median:.2f} p95={p95:.2f} max={maximum:.2f}".format(**metrics["import_ms"]))
    print("invocation_ms: median={median:.2f} p95={p95:.2f} max={maximum:.2f}".format(**metrics["invocation_ms"]))

    if not args.check:
        return 0
    failures = []
    if metrics["import_ms"]["p95"] > IMPORT_P95_BUDGET_MS:
        failures.append(f"import p95 exceeded {IMPORT_P95_BUDGET_MS:.0f} ms")
    if metrics["invocation_ms"]["p95"] > INVOCATION_P95_BUDGET_MS:
        failures.append(f"invocation p95 exceeded {INVOCATION_P95_BUDGET_MS:.0f} ms")
    if failures:
        print("Performance budget failure: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


def _measure_import(iterations: int) -> list[float]:
    package_root = Path(__file__).resolve().parents[1] / "lib" / "python"
    environment = dict(os.environ)
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = f"{package_root}{os.pathsep}{existing_path}" if existing_path else str(package_root)
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        subprocess.run(
            [sys.executable, "-c", "import base_cli"],
            check=True,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        samples.append(_elapsed_ms(started))
    return samples


def _measure_invocations(iterations: int) -> list[float]:
    import base_cli
    from base_cli.testing import invoke

    app = base_cli.App(name="benchmark-runtime")

    @app.command()
    def main(ctx: base_cli.Context) -> None:
        del ctx

    samples: list[float] = []
    with tempfile.TemporaryDirectory(prefix="base-cli-benchmark-") as tmpdir:
        home = Path(tmpdir)
        for _ in range(iterations):
            started = time.perf_counter_ns()
            result = invoke(app, [], home=home)
            if result.exit_code != 0:
                raise RuntimeError(f"benchmark invocation failed: {result.output}")
            samples.append(_elapsed_ms(started))
    return samples


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


def _summary(samples: list[float]) -> dict[str, float]:
    p95 = statistics.quantiles(samples, n=20, method="inclusive")[18]
    return {
        "median": statistics.median(samples),
        "p95": p95,
        "maximum": max(samples),
    }


if __name__ == "__main__":
    raise SystemExit(main())
