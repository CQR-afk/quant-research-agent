from __future__ import annotations

import json
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.data.schema import normalize_daily_bars
from quantlab.experiments import daily_information_coefficients, evaluate_saved_experiment, train_lightgbm
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import build_research_dataset
from quantlab.research.features import FEATURE_COLUMNS, FeatureBuilder
from quantlab.research.readiness import evaluate_readiness
from quantlab.research.splits import FixedPeriodSplitter
from quantlab.cli import build_parser


def research_config() -> dict[str, object]:
    return {
        "adjustment": "qfq", "maximum_missing_session_ratio": 0.02,
        "minimum_data_symbols": 3, "minimum_rows_per_split": 200,
        "minimum_test_dates": 100, "minimum_cross_section_symbols": 3,
        "minimum_test_cross_section_coverage": 0.8, "embargo_sessions": 5,
        "splits": {
            "train_start": "2018-01-01", "train_end": "2019-12-31",
            "validation_start": "2020-01-01", "validation_end": "2020-12-31",
            "test_start": "2021-01-01", "test_end": "2021-12-31",
        },
        "lightgbm": {"n_estimators": 8, "learning_rate": 0.05, "num_leaves": 7,
                     "max_depth": 3, "min_child_samples": 10, "random_state": 42,
                     "n_jobs": 1, "device_type": "cpu"},
    }


def model_fixture() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    config = research_config()
    calendar = AShareTradingCalendar("2018-01-01", "2021-12-31")
    dates = calendar.sessions
    rows: list[dict[str, object]] = []
    symbols = ["000001.SZ", "600519.SH", "000333.SZ"]
    for security_index, symbol in enumerate(symbols):
        for index, day in enumerate(dates[:-5]):
            row: dict[str, object] = {
                "symbol": symbol, "prediction_date": day,
                "label_end_date": dates[index + 5], "target_5d": float(np.sin(index / 9 + security_index) / 100),
                "adjustment": "qfq", "Return_5d": float(np.cos(index / 13 + security_index) / 10),
            }
            for feature_index, feature in enumerate(FEATURE_COLUMNS):
                row[feature] = float(np.sin(index / (feature_index + 2) + security_index))
            rows.append(row)
    data = pd.DataFrame(rows)
    coverage = pd.DataFrame([{
        "symbol": symbol, "rows": len(dates) - 5, "missing_ratio": 0.0,
        "unexpected_dates": 0,
    } for symbol in symbols])
    return data, coverage, config


class ResearchDatasetAndModelTests(unittest.TestCase):
    @contextmanager
    def temporary_directory(self):
        path = Path(__file__).parent / "_testdata" / self._testMethodName
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_features_are_independent_by_symbol_and_chronological(self):
        dates = pd.date_range("2024-01-01", periods=80, freq="B")
        first = pd.DataFrame({"symbol": "000001.SZ", "trade_date": dates,
                              "close": np.arange(10., 90.), "volume": 100., "adjustment": "qfq"})
        second = first.copy()
        second["symbol"] = "600519.SH"
        second["close"] *= 10
        combined = FeatureBuilder().build(pd.concat([first, second], ignore_index=True))
        first_rows = combined.loc[combined.symbol == "000001.SZ"].reset_index(drop=True)
        standalone = FeatureBuilder().build(first)
        pd.testing.assert_frame_equal(first_rows, standalone)
        self.assertEqual(first_rows.loc[59, "MA_60"], np.mean(np.arange(10., 70.)))

    def test_features_ignore_future_bars(self):
        dates = pd.date_range("2024-01-01", periods=80, freq="B")
        bars = pd.DataFrame({"symbol": "000001.SZ", "trade_date": dates,
                             "close": np.arange(10., 90.), "volume": 100., "adjustment": "qfq"})
        baseline = FeatureBuilder().build(bars)
        changed = bars.copy()
        changed.loc[changed.trade_date > dates[50], "close"] *= 3
        altered = FeatureBuilder().build(changed)
        left = baseline.loc[baseline.prediction_date <= dates[50], FEATURE_COLUMNS]
        right = altered.loc[altered.prediction_date <= dates[50], FEATURE_COLUMNS]
        pd.testing.assert_frame_equal(left.reset_index(drop=True), right.reset_index(drop=True))

    def test_feature_warmup_rows_remain_missing_instead_of_being_filled(self):
        dates = pd.date_range("2024-01-01", periods=65, freq="B")
        bars = pd.DataFrame({"symbol": "000001.SZ", "trade_date": dates,
                             "close": np.arange(10., 75.), "volume": 100., "adjustment": "qfq"})
        features = FeatureBuilder().build(bars)
        self.assertTrue(features.loc[:58, FEATURE_COLUMNS].isna().any(axis=None))
        self.assertTrue(features.loc[59, FEATURE_COLUMNS].notna().all())

    def test_feature_builder_rejects_mixed_adjustment_modes(self):
        bars = pd.DataFrame({"symbol": ["000001.SZ", "000001.SZ"],
                             "trade_date": pd.date_range("2024-01-01", periods=2),
                             "close": [10., 11.], "volume": [1., 1.],
                             "adjustment": ["qfq", "hfq"]})
        with self.assertRaisesRegex(ValueError, "one adjustment"):
            FeatureBuilder().build(bars)

    def test_readiness_calendar_and_breadth_gate(self):
        dataset, coverage, config = model_fixture()
        calendar = AShareTradingCalendar("2018-01-01", "2021-12-31")
        splitter = FixedPeriodSplitter(config, calendar)
        result = evaluate_readiness(dataset, coverage, config, splitter)
        self.assertTrue(result.passed, result.report["training_blockers"])
        broken_coverage = coverage.copy()
        broken_coverage.loc[0, "missing_ratio"] = 0.5
        blocked = evaluate_readiness(dataset, broken_coverage, config, splitter)
        self.assertFalse(blocked.passed)
        self.assertIn("calendar_coverage", [item["name"] for item in blocked.report["training_blockers"]])
        insufficient = evaluate_readiness(dataset, coverage.iloc[:2], config, splitter)
        self.assertFalse(insufficient.passed)
        self.assertEqual(insufficient.report["symbols_with_data"], 2)

    def test_readiness_rejects_mixed_price_adjustment(self):
        dataset, coverage, config = model_fixture()
        dataset.loc[dataset.index[0], "adjustment"] = "hfq"
        splitter = FixedPeriodSplitter(config, AShareTradingCalendar("2018-01-01", "2021-12-31"))
        result = evaluate_readiness(dataset, coverage, config, splitter)
        self.assertFalse(result.passed)
        self.assertIn("single_adjustment_mode", [item["name"] for item in result.report["training_blockers"]])

    def test_split_purges_both_boundaries_and_uses_global_dates(self):
        data, _, config = model_fixture()
        splits = FixedPeriodSplitter(config, AShareTradingCalendar("2018-01-01", "2021-12-31")).split(data)
        self.assertLess(splits.train.label_end_date.max(), pd.Timestamp("2020-01-01"))
        self.assertLess(splits.validation.label_end_date.max(), pd.Timestamp("2021-01-01"))
        self.assertGreater(splits.metadata["purged_train_rows"], 0)
        self.assertGreater(splits.metadata["purged_validation_rows"], 0)
        self.assertEqual(splits.train.groupby("prediction_date").symbol.nunique().min(), 3)
        self.assertTrue(all(splits.metadata["boundary_checks"].values()))

    def test_duplicate_symbol_date_is_rejected_by_splitter(self):
        data, _, config = model_fixture()
        data = pd.concat([data, data.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            FixedPeriodSplitter(config, AShareTradingCalendar("2018-01-01", "2021-12-31")).split(data)

    def test_rank_ic_marks_small_and_constant_cross_sections_na(self):
        data = pd.DataFrame({
            "prediction_date": [pd.Timestamp("2024-01-02")] * 2 + [pd.Timestamp("2024-01-03")] * 3,
            "target_5d": [0.1, 0.2, 0.1, 0.1, 0.1],
            "model": [0.2, 0.1, 1., 2., 3.],
        })
        result = daily_information_coefficients(data, ["model"])
        self.assertTrue(result.pearson_ic.isna().all())
        self.assertTrue(result.spearman_rank_ic.isna().all())
        self.assertIn("fewer than 3", result.iloc[0].pearson_reason)
        self.assertIn("constant realized", result.iloc[1].pearson_reason)

    def test_rank_ic_computes_valid_daily_cross_section(self):
        data = pd.DataFrame({"prediction_date": [pd.Timestamp("2024-01-02")] * 4,
                             "target_5d": [0.1, 0.2, 0.3, 0.4],
                             "model": [1., 2., 3., 4.]})
        result = daily_information_coefficients(data, ["model"])
        self.assertAlmostEqual(result.iloc[0].pearson_ic, 1.0)
        self.assertAlmostEqual(result.iloc[0].spearman_rank_ic, 1.0)
        self.assertEqual(result.iloc[0].sample_count, 4)

    def test_xshg_calendar_resolves_weekend_and_holiday_sessions(self):
        calendar = AShareTradingCalendar("2024-01-01", "2025-02-01")
        self.assertTrue(calendar.is_session("2024-01-02"))
        self.assertFalse(calendar.is_session("2024-02-10"))
        self.assertFalse(calendar.is_session("2025-01-28"))
        self.assertTrue(calendar.is_session("2025-01-27"))

    def test_cli_keeps_existing_and_new_command_roots(self):
        parser = build_parser()
        commands = (["run"], ["news"], ["data", "status"],
                    ["research", "dataset-status"], ["model", "experiments"])
        for argv in commands:
            args = parser.parse_args(argv)
            self.assertEqual(args.command, argv[0])

    def test_lightgbm_baselines_persist_and_repeated_runs_are_immutable(self):
        with self.temporary_directory() as temp:
            root = temp
            (root / "config").mkdir()
            (root / "data" / "processed").mkdir(parents=True)
            config = research_config()
            (root / "config" / "research.json").write_text(json.dumps(config), encoding="utf-8")
            data, coverage, _ = model_fixture()
            dataset_path = root / "data" / "processed" / "ashare_research_dataset_v1.parquet"
            data.to_parquet(dataset_path, index=False)
            manifest = {
                "dataset_path": str(dataset_path), "research_start": "2018-01-01",
                "research_end": "2021-12-31", "coverage": coverage.to_dict("records"),
                "adjustment": "qfq", "source": "fixture", "target": "target_5d",
                "target_horizon_observations": 5, "point_in_time_limit": "synthetic test fixture",
                "data_version": "fixture-v1", "pool_id": "test", "pool_selection_date": "2018-01-01",
                "survivorship_bias_note": "test fixture", "feature_version": "test-v1",
                "feature_parameters": {}, "calendar": AShareTradingCalendar("2018-01-01", "2021-12-31").metadata,
            }
            dataset_path.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            first = train_lightgbm(root=root)
            second = train_lightgbm(root=root)
            self.assertEqual(first["status"], "complete")
            self.assertEqual(second["status"], "complete")
            self.assertNotEqual(first["experiment_id"], second["experiment_id"])
            self.assertTrue(first["reload_consistent"])
            report = json.loads(Path(first["report_path"]).read_text(encoding="utf-8"))
            self.assertEqual(set(report["metrics"]["test"]), {"lightgbm", "train_mean", "momentum_5d"})
            self.assertEqual(report["experiment_name"], "Baseline Real Market Experiment v1")
            self.assertIn("test_yearly", report["metrics"])
            self.assertIn("test_quarterly", report["metrics"])
            self.assertIn("test_by_symbol", report["metrics"])
            self.assertIn("test_prediction_distribution", report["metrics"])
            self.assertTrue(Path(report["feature_importance_path"]).exists())
            self.assertTrue(Path(first["model_path"]).exists())
            evaluated = evaluate_saved_experiment(first["experiment_id"], root=root)
            self.assertTrue(evaluated["model_reload_same_input_consistent"])

    def test_missing_dataset_blocks_training_without_fitting(self):
        with self.temporary_directory() as temp:
            root = temp
            (root / "config").mkdir()
            (root / "config" / "research.json").write_text(json.dumps(research_config()), encoding="utf-8")
            result = train_lightgbm(root=root)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(Path(result["readiness_report"]).exists())

    def test_research_dataset_builder_uses_local_duckdb_and_labels_actual_end(self):
        with self.temporary_directory() as temp:
            root = temp
            start, end = "2024-01-01", "2024-06-30"
            calendar = AShareTradingCalendar(start, end)
            store = MarketDataStore(root / "data")
            symbols = ["000001.SZ", "600519.SH", "000333.SZ"]
            for offset, symbol in enumerate(symbols):
                close = 10 + offset + np.arange(len(calendar.sessions)) * (0.1 + offset * 0.01)
                raw = pd.DataFrame({
                    "trade_date": calendar.sessions, "open": close, "high": close + 0.1,
                    "low": close - 0.1, "close": close, "volume": 10000 + np.arange(len(close)),
                    "amount": close * (10000 + np.arange(len(close))),
                })
                from quantlab.data.symbols import normalize_ashare_symbol
                identity = normalize_ashare_symbol(symbol)
                canonical = normalize_daily_bars(raw, market="CN_A", exchange=identity.exchange,
                    symbol=symbol, adjustment="qfq", currency="CNY", source="akshare",
                    provider_version="fixture", source_interface="fixture")
                store.merge_validated(canonical)
            pool_path = root / "pool.json"
            pool_path.write_text(json.dumps({"pool_id": "test", "selection_date": "2024-01-01",
                "research_start": start, "research_end": end, "survivorship_bias_note": "fixture",
                "members": [{"symbol": symbol, "name": symbol, "exchange": normalize_ashare_symbol(symbol).exchange}
                            for symbol in symbols]}), encoding="utf-8")
            config_path = root / "research.json"
            config = {"adjustment": "qfq", "source": "akshare", "maximum_missing_session_ratio": 0.02}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            output = root / "data" / "processed" / "test.parquet"
            result = build_research_dataset(store=store, pool_path=pool_path,
                research_config_path=config_path, output_path=output)
            self.assertEqual(result.manifest["symbols_with_data"], 3)
            self.assertEqual(result.dataset.adjustment.unique().tolist(), ["qfq"])
            self.assertEqual(result.manifest["target_version"], "target_5d_h5_v1")
            self.assertEqual(result.manifest["dataset_version"], result.manifest["data_version"])
            self.assertEqual(result.manifest["point_in_time_status"], "NOT_POINT_IN_TIME")
            self.assertTrue(result.manifest["adjustment_snapshot_time_by_symbol"])
            labeled = result.dataset["label_end_date"].notna()
            self.assertTrue(result.dataset.loc[labeled, "label_end_date"].gt(
                result.dataset.loc[labeled, "prediction_date"]
            ).all())
            self.assertTrue(result.dataset.loc[result.dataset.prediction_date == calendar.sessions[-1], "target_5d"].isna().all())
            first_symbol = result.dataset.loc[result.dataset.symbol == symbols[0]].sort_values("prediction_date")
            first_label = first_symbol.iloc[0]
            self.assertEqual(first_label.label_end_date, calendar.sessions[5])
            self.assertAlmostEqual(first_label.target_5d, 0.5 / 10.0)
            self.assertTrue(result.manifest_path.exists())
            self.assertEqual(len(pd.read_parquet(output)), len(result.dataset))


if __name__ == "__main__":
    unittest.main()
