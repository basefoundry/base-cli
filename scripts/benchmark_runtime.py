#!/usr/bin/env python3
"""Measure comparable Python CLI startup, invocation, and lifecycle scenarios."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any, TypedDict, cast

# Budgets are intentionally profile-specific: hosted Windows and WSL runners
# have materially higher process and filesystem startup variance than POSIX.
IMPORT_P95_BUDGETS_MS = {
    "unix": 750.0,
    "macos": 750.0,
    "windows": 1_000.0,
    "wsl": 1_000.0,
}
COLD_INVOCATION_P95_BUDGETS_MS = {
    "unix": 2_000.0,
    "macos": 2_000.0,
    "windows": 4_000.0,
    "wsl": 4_000.0,
}
LIFECYCLE_OVERHEAD_P95_BUDGETS_MS = {
    "unix": 5.0,
    "macos": 5.0,
    "windows": 15.0,
    "wsl": 15.0,
}
WARM_INVOCATION_P95_BUDGETS_MS = {
    "unix": 50.0,
    "macos": 50.0,
    "windows": 100.0,
    "wsl": 100.0,
}
DEFAULT_ITERATIONS = 31
FRAMEWORKS = ("base-cli", "click", "typer", "cyclopts")
RESULT_SCHEMA = "base-cli.benchmark"
RESULT_SCHEMA_VERSION = 1


class Summary(TypedDict):
    median: float
    p95: float
    maximum: float
    median_absolute_deviation: float


FrameworkMetrics = dict[str, Any]


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"number of samples per scenario (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument("--check", action="store_true", help="fail when a required comparator or budget is missing")
    parser.add_argument("--json", action="store_true", help="emit the versioned machine-readable report to stdout")
    parser.add_argument("--output", type=Path, help="write the versioned machine-readable report to this file")
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("--iterations must be at least 3")

    results: dict[str, FrameworkMetrics] = {}
    for framework in FRAMEWORKS:
        if importlib.util.find_spec(framework.replace("-", "_")) is None:
            results[framework] = {"status": "unavailable"}
            continue
        results[framework] = {
            "cold_import_ms": _summary(_measure_import(args.iterations, framework)),
            "warm_invocation_ms": _summary(_measure_invocations(args.iterations, framework)),
            "cold_invocation_ms": _summary(_measure_cold_invocations(args.iterations, framework)),
        }

    if results["base-cli"].get("status") != "unavailable":
        results["base-cli"]["lifecycle_warm_invocation_ms"] = _summary(_measure_lifecycle_invocations(args.iterations))
        results["base-cli"]["production_warm_invocation_ms"] = _summary(
            _measure_production_invocations(args.iterations)
        )
        results["base-cli"]["features"] = cast(Any, _measure_base_cli_features(args.iterations))

    report = {
        "schema": RESULT_SCHEMA,
        "schema_version": RESULT_SCHEMA_VERSION,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": _package_version(),
        "source_revision": _source_revision(),
        "platform_profile": BENCHMARK_PLATFORM,
        "iterations_per_scenario": args.iterations,
        "environment": _environment_metadata(),
        "framework_versions": _framework_versions(),
        "results": results,
        "comparisons": _comparisons(results),
        "budgets_ms": _budgets_for_platform(BENCHMARK_PLATFORM),
    }

    failures = _check_results(results) if args.check else []
    _write_github_summary(report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Benchmark report written to {args.output}")
    if failures:
        print("Benchmark contract failure: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


def _package_version() -> str:
    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return "unknown"


def _source_revision() -> str | None:
    configured = os.environ.get("SOURCE_REVISION") or os.environ.get("GITHUB_SHA")
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    revision = completed.stdout.strip()
    return revision or None


def _environment_metadata() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_abi": sysconfig.get_config_var("SOABI"),
        "cpu_count": os.cpu_count(),
        "github_actions": os.environ.get("GITHUB_ACTIONS", "").lower() == "true",
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }


def _framework_versions() -> dict[str, str | None]:
    distributions = {
        "base-cli": "base-cli",
        "click": "click",
        "typer": "typer",
        "cyclopts": "cyclopts",
    }
    versions: dict[str, str | None] = {}
    for framework, distribution in distributions.items():
        try:
            versions[framework] = distribution_version(distribution)
        except PackageNotFoundError:
            versions[framework] = None
    return versions


def _budgets_for_platform(platform_profile: str) -> dict[str, float]:
    return {
        "cold_import_p95": IMPORT_P95_BUDGETS_MS[platform_profile],
        "cold_invocation_p95": COLD_INVOCATION_P95_BUDGETS_MS[platform_profile],
        "lifecycle_increment_over_click_p95": LIFECYCLE_OVERHEAD_P95_BUDGETS_MS[platform_profile],
        "warm_invocation_p95": WARM_INVOCATION_P95_BUDGETS_MS[platform_profile],
    }


def _comparisons(results: dict[str, FrameworkMetrics]) -> dict[str, float | None]:
    base = results.get("base-cli", {})
    click = results.get("click", {})
    base_import = _metric_p95(base, "cold_import_ms")
    click_import = _metric_p95(click, "cold_import_ms")
    lifecycle = _metric_p95(base, "lifecycle_warm_invocation_ms")
    click_warm = _metric_p95(click, "warm_invocation_ms")
    return {
        "base_cli_to_click_import_p95_ratio": _ratio(base_import, click_import),
        "lifecycle_increment_over_click_warm_p95_ms": (
            max(0.0, lifecycle - click_warm) if lifecycle is not None and click_warm is not None else None
        ),
        "base_cli_lifecycle_to_click_warm_p95_ratio": _ratio(lifecycle, click_warm),
    }


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _metric_p95(metrics: FrameworkMetrics, key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, dict):
        p95 = value.get("p95")
        return float(p95) if isinstance(p95, (int, float)) else None
    return None


def _check_results(results: dict[str, FrameworkMetrics]) -> list[str]:
    failures: list[str] = []
    for framework in FRAMEWORKS:
        metrics = results.get(framework, {"status": "unavailable"})
        if metrics.get("status") == "unavailable":
            failures.append(f"required comparator {framework} is unavailable; install the benchmark extras")
            continue
        for metric in ("cold_import_ms", "cold_invocation_ms", "warm_invocation_ms"):
            if _metric_p95(metrics, metric) is None:
                failures.append(f"required comparator {framework} is missing {metric} p95")

    base = results.get("base-cli", {})
    if base.get("status") == "unavailable":
        return failures

    for metric in ("lifecycle_warm_invocation_ms", "production_warm_invocation_ms"):
        if _metric_p95(base, metric) is None:
            failures.append(f"base-cli benchmark is missing {metric} p95")

    features = base.get("features")
    expected_features = {
        "lifecycle_noop_ms",
        "json_success_ms",
        "json_error_ms",
        "diagnostics_ms",
        "nested_command_ms",
        "persistence_disabled_ms",
        "persistence_enabled_ms",
    }
    if not isinstance(features, dict):
        failures.append("base-cli benchmark is missing feature scenarios")
    else:
        for feature in sorted(expected_features):
            if _metric_p95(cast(FrameworkMetrics, {"metric": features.get(feature)}), "metric") is None:
                failures.append(f"base-cli benchmark is missing {feature} p95")

    import_p95 = _metric_p95(base, "cold_import_ms")
    if import_p95 is not None and import_p95 > IMPORT_P95_BUDGETS_MS[BENCHMARK_PLATFORM]:
        failures.append(f"base-cli cold import p95 exceeded {IMPORT_P95_BUDGETS_MS[BENCHMARK_PLATFORM]:.0f} ms")

    cold_p95 = _metric_p95(base, "cold_invocation_ms")
    if cold_p95 is not None and cold_p95 > COLD_INVOCATION_P95_BUDGETS_MS[BENCHMARK_PLATFORM]:
        failures.append(
            f"base-cli cold invocation p95 exceeded {COLD_INVOCATION_P95_BUDGETS_MS[BENCHMARK_PLATFORM]:.0f} ms"
        )

    for framework in FRAMEWORKS:
        warm_p95 = _metric_p95(results.get(framework, {}), "warm_invocation_ms")
        if warm_p95 is not None and warm_p95 > WARM_INVOCATION_P95_BUDGETS_MS[BENCHMARK_PLATFORM]:
            failures.append(
                f"{framework} warm no-op invocation p95 exceeded "
                f"{WARM_INVOCATION_P95_BUDGETS_MS[BENCHMARK_PLATFORM]:.0f} ms"
            )

    lifecycle_p95 = _metric_p95(base, "lifecycle_warm_invocation_ms")
    click_p95 = _metric_p95(results.get("click", {}), "warm_invocation_ms")
    if lifecycle_p95 is not None and click_p95 is not None:
        if click_p95 <= 0:
            failures.append("Click warm invocation p95 must be greater than zero for a valid comparison")
        increment = max(0.0, lifecycle_p95 - click_p95)
        budget = LIFECYCLE_OVERHEAD_P95_BUDGETS_MS[BENCHMARK_PLATFORM]
        if increment > budget:
            failures.append(f"base-cli lifecycle increment over Click p95 exceeded {budget:.0f} ms")

    production_p95 = _metric_p95(base, "production_warm_invocation_ms")
    warm_budget = WARM_INVOCATION_P95_BUDGETS_MS[BENCHMARK_PLATFORM]
    if production_p95 is not None and production_p95 > warm_budget:
        failures.append(f"base-cli production-boundary warm invocation p95 exceeded {warm_budget:.0f} ms")

    if isinstance(features, dict):
        for name, value in features.items():
            p95 = _metric_p95(cast(FrameworkMetrics, {"metric": value}), "metric")
            if p95 is not None and p95 > warm_budget:
                failures.append(f"base-cli {name} p95 exceeded {warm_budget:.0f} ms")
    return failures


def _print_report(report: dict[str, Any]) -> None:
    print(
        f"benchmark platform: {report['platform_profile']} | Python {report['environment']['python_version']} "
        f"| {report['iterations_per_scenario']} samples/scenario"
    )
    for framework, metrics in report["results"].items():
        if metrics.get("status") == "unavailable":
            print(f"{framework}: unavailable")
            continue
        for key, value in metrics.items():
            if isinstance(value, dict) and "p95" in value:
                print(
                    f"{framework} {key}: median={value['median']:.2f} ms "
                    f"p95={value['p95']:.2f} ms max={value['maximum']:.2f} ms "
                    f"MAD={value['median_absolute_deviation']:.2f} ms"
                )
            elif key == "features" and isinstance(value, dict):
                for feature, summary in value.items():
                    print(
                        f"base-cli {feature}: median={summary['median']:.2f} ms "
                        f"p95={summary['p95']:.2f} ms max={summary['maximum']:.2f} ms "
                        f"MAD={summary['median_absolute_deviation']:.2f} ms"
                    )
    print("comparisons: " + json.dumps(report["comparisons"], sort_keys=True))


def _write_github_summary(report: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    profile = report["platform_profile"]
    lines = [
        f"## Base CLI performance — {profile}",
        "",
        f"Python {report['environment']['python_version']} · "
        f"{report['iterations_per_scenario']} samples per scenario · "
        f"base-cli {report['framework_versions'].get('base-cli') or 'unavailable'}",
        "",
        "### Framework comparison (p95, milliseconds)",
        "",
        "| Framework | Fresh-process import | Cold invocation | Warm invocation |",
        "| --- | ---: | ---: | ---: |",
    ]
    for framework in FRAMEWORKS:
        metrics = report["results"].get(framework, {})
        version = report["framework_versions"].get(framework) or "unavailable"
        lines.append(
            f"| {framework} {version} | {_summary_value(metrics, 'cold_import_ms')} | "
            f"{_summary_value(metrics, 'cold_invocation_ms')} | {_summary_value(metrics, 'warm_invocation_ms')} |"
        )

    comparison = report["comparisons"]
    lines.extend(
        [
            "",
            "### Lifecycle overhead versus parser dispatch",
            "",
            "| Measure | Result | Budget |",
            "| --- | ---: | ---: |",
            f"| base-cli lifecycle increment over Click warm p95 | "
            f"{_format_number(comparison.get('lifecycle_increment_over_click_warm_p95_ms'))} ms | "
            f"{_format_number(report['budgets_ms']['lifecycle_increment_over_click_p95'])} ms |",
            f"| base-cli / Click lifecycle warm p95 | "
            f"{_format_number(comparison.get('base_cli_lifecycle_to_click_warm_p95_ratio'))}× | informational |",
            "",
            "### Base CLI feature scenarios (p95, milliseconds)",
            "",
            "| Scenario | p95 |",
            "| --- | ---: |",
        ]
    )
    features = report["results"].get("base-cli", {}).get("features", {})
    for feature, summary in sorted(features.items()):
        lines.append(f"| {feature.removesuffix('_ms').replace('_', ' ')} | {_format_number(summary.get('p95'))} ms |")
    lines.append("")
    try:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines))
    except OSError as exc:
        print(f"Could not write GitHub Actions summary: {exc}", file=sys.stderr)


def _summary_value(metrics: FrameworkMetrics, name: str) -> str:
    summary = metrics.get(name)
    if not isinstance(summary, dict):
        return "—"
    return f"{_format_number(summary.get('p95'))} ms"


def _format_number(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.2f}"


def _measure_import(iterations: int, framework: str) -> list[float]:
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


def _measure_cold_invocations(iterations: int, framework: str) -> list[float]:
    package_root = Path(__file__).resolve().parents[1] / "lib" / "python"
    environment = dict(os.environ)
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = f"{package_root}{os.pathsep}{existing_path}" if existing_path else str(package_root)
    programs = {
        "base-cli": (
            "import base_cli\n"
            "app = base_cli.App(name='benchmark-cold', log_to_file=False)\n"
            "@app.command()\n"
            "def command(ctx):\n    del ctx\n"
            "raise SystemExit(base_cli.run_app(app, []))\n"
        ),
        "click": (
            "import click\n"
            "@click.command()\n"
            "def command():\n    return None\n"
            "command.main([], prog_name='benchmark-cold', standalone_mode=False)\n"
        ),
        "typer": (
            "import typer\n"
            "from typer.main import get_command\n"
            "app = typer.Typer()\n"
            "@app.command()\n"
            "def command():\n    return None\n"
            "get_command(app).main([], prog_name='benchmark-cold', standalone_mode=False)\n"
        ),
        "cyclopts": ("import cyclopts\napp = cyclopts.App()\n@app.default\ndef command():\n    return None\napp([])\n"),
    }
    samples: list[float] = []
    with tempfile.TemporaryDirectory(prefix="base-cli-cold-benchmark-") as tmpdir:
        environment["HOME"] = tmpdir
        environment["XDG_CACHE_HOME"] = str(Path(tmpdir) / ".cache")
        environment["BASE_CLI_CACHE_DIR"] = str(Path(tmpdir) / ".cache")
        if os.name == "nt":
            environment["USERPROFILE"] = tmpdir
            environment["LOCALAPPDATA"] = str(Path(tmpdir) / "AppData" / "Local")
        for _ in range(iterations):
            started = time.perf_counter_ns()
            subprocess.run(
                [sys.executable, "-c", programs[framework]],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            samples.append(_elapsed_ms(started))
    return samples


def _measure_invocations(iterations: int, framework: str) -> list[float]:
    if framework == "click":
        return _measure_click_invocations(iterations)
    if framework == "typer":
        return _measure_typer_invocations(iterations)
    if framework == "cyclopts":
        return _measure_cyclopts_invocations(iterations)
    return _measure_production_invocations(iterations)


def _measure_production_invocations(iterations: int) -> list[float]:
    import base_cli
    from base_cli.testing import invoke

    app = base_cli.App(name="benchmark-runtime", log_to_file=False)

    @app.command()
    def main(ctx: Any) -> None:
        del ctx

    _command = app.click_command
    samples: list[float] = []
    with tempfile.TemporaryDirectory(prefix="base-cli-benchmark-") as tmpdir:
        home = Path(tmpdir)
        for _ in range(iterations):
            started = time.perf_counter_ns()
            result = invoke(app, [], home=home)
            elapsed = _elapsed_ms(started)
            if result.exit_code != 0:
                raise RuntimeError(f"benchmark invocation failed: {result.output}")
            samples.append(elapsed)
    return samples


def _measure_lifecycle_invocations(iterations: int) -> list[float]:
    import base_cli
    from click.testing import CliRunner

    app = base_cli.App(name="benchmark-lifecycle", log_to_file=False)

    @app.command()
    def main(ctx: Any) -> None:
        del ctx

    command = cast(Any, app.click_command)
    runner = CliRunner()
    return _measure_runner(iterations, lambda: runner.invoke(command, []).exit_code)


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


def _measure_base_cli_features(iterations: int) -> dict[str, Summary]:
    import base_cli
    import click
    from base_cli.testing import invoke

    def make_app(name: str, *, log_to_file: bool = False) -> base_cli.App:
        return base_cli.App(name=name, log_to_file=log_to_file)

    lifecycle = make_app("benchmark-lifecycle-noop")

    @lifecycle.command()
    def lifecycle_noop(ctx: Any) -> None:
        del ctx

    json_success = make_app("benchmark-json-success")
    json_success.lifecycle_options = base_cli.LifecycleOptions(
        json=base_cli.LifecycleOption("--json"),
    )

    @json_success.command()
    def success(ctx: Any) -> None:
        del ctx
        print("ok")

    json_error = make_app("benchmark-json-error")
    json_error.lifecycle_options = base_cli.LifecycleOptions(
        json=base_cli.LifecycleOption("--json"),
    )

    @json_error.command()
    def failure(ctx: Any) -> None:
        del ctx
        raise click.ClickException("expected benchmark error")

    diagnostics = make_app("benchmark-diagnostics")

    @diagnostics.command()
    def diagnose(ctx: Any) -> None:
        ctx.log.debug("benchmark diagnostic")

    nested = make_app("benchmark-nested")

    @nested.subcommand("noop")
    def nested_noop(ctx: Any) -> None:
        del ctx

    persistence_disabled = make_app("benchmark-persistence-disabled", log_to_file=False)

    @persistence_disabled.command()
    def log_without_file(ctx: Any) -> None:
        ctx.log.info("benchmark persistence probe")

    persistence_enabled = make_app("benchmark-persistence-enabled", log_to_file=True)

    @persistence_enabled.command()
    def log_with_file(ctx: Any) -> None:
        ctx.log.info("benchmark persistence probe")

    scenarios = {
        "lifecycle_noop_ms": (lifecycle, [], 0, None),
        "json_success_ms": (json_success, ["--json"], 0, "success"),
        "json_error_ms": (json_error, ["--json"], 1, "error"),
        "diagnostics_ms": (diagnostics, ["--debug"], 0, None),
        "nested_command_ms": (nested, ["noop"], 0, None),
        "persistence_disabled_ms": (persistence_disabled, [], 0, None),
        "persistence_enabled_ms": (persistence_enabled, [], 0, None),
    }
    for app, _args, _expected_exit, _json_type in scenarios.values():
        _command = app.click_command

    results: dict[str, Summary] = {}
    for metric_name, (app, args, expected_exit, json_type) in scenarios.items():
        samples: list[float] = []
        with tempfile.TemporaryDirectory(prefix=f"{app.name}-") as tmpdir:
            home = Path(tmpdir)
            for _ in range(iterations):
                started = time.perf_counter_ns()
                result = invoke(app, args, home=home)
                elapsed = _elapsed_ms(started)
                if result.exit_code != expected_exit:
                    raise RuntimeError(f"{metric_name} benchmark failed: {result.output}")
                if json_type is not None:
                    try:
                        payload = json.loads(result.stdout)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"{metric_name} did not emit a JSON envelope") from exc
                    if payload.get("type") != json_type:
                        raise RuntimeError(f"{metric_name} emitted unexpected JSON envelope type")
                samples.append(elapsed)
        results[metric_name] = _summary(samples)
    return results


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
    if len(samples) < 3:
        raise ValueError("at least three benchmark samples are required")
    median = statistics.median(samples)
    p95 = statistics.quantiles(samples, n=20, method="inclusive")[18]
    return {
        "median": median,
        "p95": p95,
        "maximum": max(samples),
        "median_absolute_deviation": statistics.median(abs(sample - median) for sample in samples),
    }


if __name__ == "__main__":
    raise SystemExit(main())
