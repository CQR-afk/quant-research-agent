"""Offline guard and accounting tests for post-hoc Step 4.6 diagnostics."""

from __future__ import annotations

import ast
import json
import shutil
import unittest
from pathlib import Path

import pandas as pd

from quantlab.research.breadth_study import FEATURE_COLUMNS
from quantlab.research.dataset import project_root
from quantlab.research.signal_diagnostics import (
    DIAGNOSTIC_ROLE, INDUSTRY_WARNING, ic_distribution, session_horizon,
    validate_no_trade_exclusion, validate_prediction_input_alignment,
    verify_immutable_file, write_json_exclusive,
)
from quantlab.research.breadth_study import validate_frozen_universes
from quantlab.research.walkforward_splits import validate_development_dates


class SignalDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = project_root() / "tests" / "_testdata" / "signal_diagnostics" / self._testMethodName
        self.temp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        expected_root = (project_root() / "tests" / "_testdata" / "signal_diagnostics").resolve()
        target = self.temp.resolve()
        if expected_root not in target.parents:
            raise AssertionError("Refusing to remove a test directory outside the workspace test root.")
        shutil.rmtree(target)

    def test_step45c_prediction_model_and_dataset_hashes_are_immutable(self):
        frozen = validate_frozen_universes(project_root() / "config")
        self.assertEqual(frozen["hashes"]["U50"], "d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218")
        report = project_root() / "artifacts" / "reports" / "breadth_robustness_20261005T021422Z_83390a3990.json"
        payload = json.loads(report.read_text(encoding="utf-8"))
        path = project_root() / "data" / "processed" / "ashare_research_u100_qfq_v1_dev_2018_2023.parquet"
        verify_immutable_file(path, payload["dataset_sha256"]["U100"])
        model_hash = payload["experiments"]["U100"]["folds"][0]["file_sha256"]["model"]
        model_path = Path(payload["experiments"]["U100"]["folds"][0]["files"]["model"])
        verify_immutable_file(model_path, model_hash)
        with self.assertRaises(ValueError):
            verify_immutable_file(path, "0" * 64)

    def test_date_guard_refuses_post_development_dates(self):
        valid = pd.DataFrame({"prediction_date": ["2023-12-29"], "label_end_date": ["2023-12-29"]})
        validate_development_dates(valid)
        invalid = pd.DataFrame({"prediction_date": ["2023-12-29"], "label_end_date": ["2024-01-02"]})
        with self.assertRaises(ValueError):
            validate_development_dates(invalid)

    def test_no_trade_and_unknown_rows_are_rejected(self):
        passed = {"trade_status": {"trading": 100, "provider_reported_nontrading": 0, "unknown": 0},
                  "provider_reported_no_trade_sessions": 5}
        self.assertEqual(validate_no_trade_exclusion(passed)["provider_no_trade_sessions_excluded"], 5)
        failed = {"trade_status": {"trading": 99, "provider_reported_nontrading": 1, "unknown": 0},
                  "provider_reported_no_trade_sessions": 5}
        with self.assertRaisesRegex(ValueError, "no-trade"):
            validate_no_trade_exclusion(failed)

    def test_prediction_and_saved_feature_rows_must_align(self):
        predictions = pd.DataFrame({"symbol": ["000001.SZ"], "prediction_date": ["2021-01-04"]})
        features = pd.DataFrame([[0.0] * len(FEATURE_COLUMNS)], columns=FEATURE_COLUMNS)
        validate_prediction_input_alignment(predictions, features)
        with self.assertRaisesRegex(ValueError, "aligned"):
            validate_prediction_input_alignment(pd.concat([predictions, predictions]), features)

    def test_ic_distribution_tail_contribution_accounts_for_all_dates(self):
        values = [-.8, -.4, -.2, .1, .2, .3, .4, .5, .6, .7]
        daily = pd.DataFrame({"prediction_date": pd.date_range("2021-01-01", periods=10),
                              "rank_ic": values, "sample_count": [100] * 10})
        summary, extremes = ic_distribution(daily, lower_tail_fraction=.2)
        self.assertEqual(summary["valid_dates"], 10)
        self.assertEqual(summary["positive_day_ratio"], .7)
        self.assertEqual(summary["negative_day_ratio"], .3)
        self.assertAlmostEqual(summary["negative_lower_tail"]["sum_ic"], -1.2)
        self.assertAlmostEqual(summary["negative_lower_tail"]["mean_contribution_per_fold_date"], -.12)
        self.assertEqual(extremes.iloc[0]["rank_ic"], -.8)

    def test_five_valid_observations_can_span_more_exchange_sessions(self):
        sessions = pd.date_range("2021-01-04", periods=10, freq="B")
        self.assertEqual(session_horizon(sessions[0], sessions[5], sessions), 5)
        self.assertEqual(session_horizon(sessions[0], sessions[7], sessions), 7)
        with self.assertRaises(ValueError):
            session_horizon(sessions[2], sessions[2], sessions)

    def test_industry_classification_is_marked_current_snapshot(self):
        self.assertEqual(INDUSTRY_WARNING, "CURRENT_INDUSTRY_CLASSIFICATION_NOT_HISTORICAL_PIT")

    def test_reports_are_exclusive_and_immutable(self):
        path = self.temp / "report.json"
        write_json_exclusive(path, {"diagnostic_role": DIAGNOSTIC_ROLE})
        with self.assertRaises(FileExistsError):
            write_json_exclusive(path, {"diagnostic_role": "changed"})

    def test_diagnostic_entrypoint_has_no_fit_or_candidate_selection(self):
        source = (project_root() / "src" / "quantlab" / "research" / "signal_diagnostics.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_signal_diagnostics")
        calls = {node.func.attr for node in ast.walk(run) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse({"fit", "fit_predict", "train_test_split"} & calls)
        self.assertNotIn("LGBMRegressor", source)
        self.assertFalse(any("select" in name.lower() for name in calls))


if __name__ == "__main__":
    unittest.main()
