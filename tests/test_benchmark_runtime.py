from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "benchmark_runtime.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_runtime", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - test setup failure
    raise ImportError(f"Unable to load {_SCRIPT_PATH}")
benchmark_runtime = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark_runtime)


class BenchmarkSummaryTests(unittest.TestCase):
    def test_summary_reports_interpolated_p95_separately_from_maximum(self) -> None:
        summary = benchmark_runtime._summary([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

        self.assertEqual(summary["median"], 4.0)
        self.assertAlmostEqual(summary["p95"], 6.7)
        self.assertEqual(summary["maximum"], 7.0)

    def test_framework_comparison_has_stable_public_set(self) -> None:
        self.assertEqual(
            benchmark_runtime.FRAMEWORKS,
            ("base-cli", "click", "typer", "cyclopts"),
        )

    def test_platform_profiles_have_explicit_import_budgets(self) -> None:
        self.assertEqual(benchmark_runtime.IMPORT_P95_BUDGETS_MS["unix"], 750.0)
        self.assertEqual(benchmark_runtime.IMPORT_P95_BUDGETS_MS["macos"], 750.0)
        self.assertEqual(benchmark_runtime.IMPORT_P95_BUDGETS_MS["windows"], 1_000.0)
        self.assertEqual(benchmark_runtime.IMPORT_P95_BUDGETS_MS["wsl"], 1_000.0)
