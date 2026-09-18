from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "benchmark_runtime.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_runtime", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - test setup failure
    raise ImportError(f"Unable to load {_SCRIPT_PATH}")
benchmark_runtime = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark_runtime)


class BenchmarkSummaryTests(unittest.TestCase):
    def test_summary_reports_percentile_and_variance_separately(self) -> None:
        summary = benchmark_runtime._summary([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

        self.assertEqual(summary["median"], 4.0)
        self.assertAlmostEqual(summary["p95"], 6.7)
        self.assertEqual(summary["maximum"], 7.0)
        self.assertEqual(summary["median_absolute_deviation"], 2.0)

    def test_summary_requires_enough_samples_for_percentiles(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least three"):
            benchmark_runtime._summary([1.0, 2.0])

    def test_framework_comparison_has_stable_public_set(self) -> None:
        self.assertEqual(
            benchmark_runtime.FRAMEWORKS,
            ("base-cli", "click", "typer", "cyclopts"),
        )

    def test_platform_profiles_have_explicit_budget_vectors(self) -> None:
        for profile in ("unix", "macos", "windows", "wsl"):
            budgets = benchmark_runtime._budgets_for_platform(profile)
            self.assertEqual(
                set(budgets),
                {
                    "cold_import_p95",
                    "cold_invocation_p95",
                    "lifecycle_increment_over_click_p95",
                    "warm_invocation_p95",
                },
            )
            self.assertGreater(budgets["cold_invocation_p95"], budgets["cold_import_p95"])

    def test_environment_metadata_has_reproduction_fields(self) -> None:
        metadata = benchmark_runtime._environment_metadata()

        for key in (
            "system",
            "release",
            "machine",
            "python_implementation",
            "python_version",
            "python_abi",
            "cpu_count",
        ):
            self.assertIn(key, metadata)

    def test_comparison_separates_click_parser_from_lifecycle_increment(self) -> None:
        metrics = self._complete_results(lifecycle_p95=4.0, click_p95=1.5)

        comparisons = benchmark_runtime._comparisons(metrics)

        self.assertEqual(comparisons["lifecycle_increment_over_click_warm_p95_ms"], 2.5)
        self.assertAlmostEqual(comparisons["base_cli_lifecycle_to_click_warm_p95_ratio"], 4.0 / 1.5)

    def test_check_requires_every_declared_comparator(self) -> None:
        metrics = self._complete_results()
        metrics["cyclopts"] = {"status": "unavailable"}

        with mock.patch.object(benchmark_runtime, "BENCHMARK_PLATFORM", "macos"):
            failures = benchmark_runtime._check_results(metrics)

        self.assertTrue(any("required comparator cyclopts is unavailable" in failure for failure in failures))

    def test_check_rejects_realistic_lifecycle_regression(self) -> None:
        metrics = self._complete_results(lifecycle_p95=5.5, click_p95=0.2)

        with mock.patch.object(benchmark_runtime, "BENCHMARK_PLATFORM", "macos"):
            failures = benchmark_runtime._check_results(metrics)

        self.assertTrue(any("lifecycle increment over Click p95 exceeded 5 ms" in failure for failure in failures))

    def test_check_rejects_cold_start_budget_regression(self) -> None:
        metrics = self._complete_results(base_import_p95=751.0)

        with mock.patch.object(benchmark_runtime, "BENCHMARK_PLATFORM", "macos"):
            failures = benchmark_runtime._check_results(metrics)

        self.assertTrue(any("cold import p95 exceeded 750 ms" in failure for failure in failures))

    def test_check_rejects_a_missing_required_feature_scenario(self) -> None:
        metrics = self._complete_results()
        metrics["base-cli"]["features"] = {}

        with mock.patch.object(benchmark_runtime, "BENCHMARK_PLATFORM", "macos"):
            failures = benchmark_runtime._check_results(metrics)

        self.assertTrue(any("missing diagnostics_ms p95" in failure for failure in failures))

    def test_github_summary_separates_lifecycle_overhead_from_parser(self) -> None:
        metrics = self._complete_results(lifecycle_p95=4.0, click_p95=1.5)
        report = {
            "platform_profile": "macos",
            "environment": {"python_version": "3.13.0"},
            "iterations_per_scenario": 31,
            "framework_versions": {framework: "1.0" for framework in benchmark_runtime.FRAMEWORKS},
            "results": metrics,
            "comparisons": benchmark_runtime._comparisons(metrics),
            "budgets_ms": benchmark_runtime._budgets_for_platform("macos"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "summary.md"
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(path)}):
                benchmark_runtime._write_github_summary(report)
            summary = path.read_text(encoding="utf-8")

        self.assertIn("Framework comparison (p95, milliseconds)", summary)
        self.assertIn("Lifecycle overhead versus parser dispatch", summary)
        self.assertIn("base-cli lifecycle increment over Click warm p95", summary)
        self.assertIn("Base CLI feature scenarios", summary)

    @staticmethod
    def _summary(p95: float) -> dict[str, float]:
        return {
            "median": p95 * 0.8,
            "p95": p95,
            "maximum": p95 * 1.1,
            "median_absolute_deviation": p95 * 0.05,
        }

    @classmethod
    def _complete_results(
        cls,
        *,
        base_import_p95: float = 10.0,
        lifecycle_p95: float = 2.0,
        click_p95: float = 1.0,
    ) -> dict[str, dict[str, object]]:
        results: dict[str, dict[str, object]] = {}
        for framework in benchmark_runtime.FRAMEWORKS:
            results[framework] = {
                "cold_import_ms": cls._summary(base_import_p95 if framework == "base-cli" else 8.0),
                "cold_invocation_ms": cls._summary(20.0),
                "warm_invocation_ms": cls._summary(click_p95 if framework == "click" else 1.0),
            }
        results["base-cli"].update(
            {
                "lifecycle_warm_invocation_ms": cls._summary(lifecycle_p95),
                "production_warm_invocation_ms": cls._summary(4.0),
                "features": {
                    "lifecycle_noop_ms": cls._summary(1.0),
                    "json_success_ms": cls._summary(1.0),
                    "json_error_ms": cls._summary(1.0),
                    "diagnostics_ms": cls._summary(1.0),
                    "nested_command_ms": cls._summary(1.0),
                    "persistence_disabled_ms": cls._summary(1.0),
                    "persistence_enabled_ms": cls._summary(1.0),
                },
            }
        )
        return results
