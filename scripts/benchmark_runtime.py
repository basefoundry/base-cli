#!/usr/bin/env python3
"""Track import and isolated invocation costs with stable, checked budgets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

# Fresh-process startup is materially slower on native Windows and on WSL
# when the checkout is on the Windows-mounted filesystem.  CI can select the
# platform explicitly with BASE_CLI_BENCHMARK_PLATFORM; the fallback keeps
# local runs useful without requiring a setting.
IMPORT_P95_BUDGETS_MS = {
    "unix": 750.0,
    "macos": 750.0,
    "windows": 1_000.0,
    "wsl": 1_000.0,
}


def _is_wsl() -> bool:
    try:
        proc_version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return "microsoft" in proc_version or "wsl" in proc_version


def _benchmark_platform() -> str:
    configured = os.environ.get("BASE_CLI_BENCHMARK_PLATFORM", "").strip().lower()
    if configured:
        if configured not in IMPORT_P95_BUDGETS_MS:
            supported = ", ".join(sorted(IMPORT_P95_BUDGETS_MS))
            raise ValueError(f"BASE_CLI_BENCHMARK_PLATFORM must be one of {supported}; got {configured!r}")
        return configured
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if _is_wsl():
        return "wsl"
    return "unix"


BENCHMARK_PLATFORM = _benchmark_platform()
IMPORT_P95_BUDGET_MS = IMPORT_P95_BUDGETS_MS[BENCHMARK_PLATFORM]
INVOCATION_P95_BUDGET_MS = 1_500.0
DEFAULT_ITERATIONS = 7
FRAMEWORKS = ("base-cli", "click", "typer", "cyclopts")


class Summary(TypedDict):
    median: float
    p95: float
    maximum: float


class FrameworkMetrics(TypedDict, total=False):
    status: str
    import_ms: Summary
    invocation_ms: Summary


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
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable benchmark results",
    )
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("--iterations must be at least 3")

    metrics: dict[str, FrameworkMetrics] = {}
    for framework in FRAMEWORKS:
        if importlib.util.find_spec(framework.replace("-", "_")) is None:
            metrics[framework] = {"status": "unavailable"}
            continue
        metrics[framework] = {
            "import_ms": _summary(_measure_import(args.iterations, framework)),
            "invocation_ms": _summary(_measure_invocations(args.iterations, framework)),
        }
    if args.json:
        print(
            json.dumps(
                {
                    "iterations": args.iterations,
                    "platform": BENCHMARK_PLATFORM,
                    "import_p95_budget_ms": IMPORT_P95_BUDGET_MS,
                    "frameworks": metrics,
                },
                sort_keys=True,
            )
        )
    else:
        print(f"benchmark platform: {BENCHMARK_PLATFORM} (import p95 budget {IMPORT_P95_BUDGET_MS:.0f} ms)")
        for framework, result in metrics.items():
            if result.get("status") == "unavailable":
                print(f"{framework}: unavailable (install it to include this comparison)")
                continue
            assert "import_ms" in result and "invocation_ms" in result
            print(
                f"{framework} import_ms: median={{median:.2f}} p95={{p95:.2f}} max={{maximum:.2f}}".format(
                    **result["import_ms"]
                )
            )
            print(
                f"{framework} invocation_ms: median={{median:.2f}} p95={{p95:.2f}} max={{maximum:.2f}}".format(
                    **result["invocation_ms"]
                )
            )

    if not args.check:
        return 0
    failures = []
    base_metrics = metrics["base-cli"]
    if base_metrics.get("status") == "unavailable":
        failures.append("base-cli benchmark is unavailable")
    else:
        assert "import_ms" in base_metrics and "invocation_ms" in base_metrics
        if base_metrics["import_ms"]["p95"] > IMPORT_P95_BUDGET_MS:
            failures.append(f"base-cli import p95 exceeded {IMPORT_P95_BUDGET_MS:.0f} ms")
        if base_metrics["invocation_ms"]["p95"] > INVOCATION_P95_BUDGET_MS:
            failures.append(f"base-cli invocation p95 exceeded {INVOCATION_P95_BUDGET_MS:.0f} ms")
    if failures:
        print("Performance budget failure: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


def _measure_import(iterations: int, framework: str = "base-cli") -> list[float]:
    package_root = Path(__file__).resolve().parents[1] / "lib" / "python"
    environment = dict(os.environ)
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = f"{package_root}{os.pathsep}{existing_path}" if existing_path else str(package_root)
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        subprocess.run(
            [sys.executable, "-c", f"import {framework.replace('-', '_')}"],
            check=True,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        samples.append(_elapsed_ms(started))
    return samples


def _measure_invocations(iterations: int, framework: str = "base-cli") -> list[float]:
    if framework == "click":
        return _measure_click_invocations(iterations)
    if framework == "typer":
        return _measure_typer_invocations(iterations)
    if framework == "cyclopts":
        return _measure_cyclopts_invocations(iterations)
    import base_cli
    from base_cli.testing import invoke

    app = base_cli.App(name="benchmark-runtime")

    @app.command()
    def main(ctx: base_cli.Context[Any, Any, Any]) -> None:
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


def _measure_click_invocations(iterations: int) -> list[float]:
    import click
    from click.testing import CliRunner

    @click.command()
    def command() -> None:
        return None

    runner = CliRunner()
    return _measure_runner(iterations, lambda: runner.invoke(cast(Any, command), []).exit_code)


def _measure_typer_invocations(iterations: int) -> list[float]:
    import typer
    from click.testing import CliRunner
    from typer.main import get_command

    app = typer.Typer()

    @app.command()
    def callback() -> None:
        return None

    command = get_command(app)
    runner = CliRunner()
    return _measure_runner(iterations, lambda: runner.invoke(cast(Any, command), []).exit_code)


def _measure_cyclopts_invocations(iterations: int) -> list[float]:
    cyclopts = importlib.import_module("cyclopts")

    app = cyclopts.App()

    @app.default  # type: ignore[untyped-decorator]
    def callback() -> None:
        return None

    return _measure_runner(iterations, lambda: cast(Any, app)([]))


def _measure_runner(iterations: int, callback: Callable[[], Any]) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        result = callback()
        if result not in (None, 0):
            raise RuntimeError(f"benchmark invocation failed with status {result!r}")
        samples.append(_elapsed_ms(started))
    return samples


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


def _summary(samples: list[float]) -> Summary:
    p95 = statistics.quantiles(samples, n=20, method="inclusive")[18]
    return {
        "median": statistics.median(samples),
        "p95": p95,
        "maximum": max(samples),
    }


if __name__ == "__main__":
    raise SystemExit(main())
