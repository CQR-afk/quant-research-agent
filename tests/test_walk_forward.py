from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data.schema import normalize_daily_bars
from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import build_research_dataset
from quantlab.research.features import (
    FEATURE_COLUMNS, FEATURE_V1_SCALE_DEPENDENT, FEATURE_V1_SCALE_INVARIANT,
    FEATURE_V2_COLUMNS, FEATURE_V2_VERSION, FeatureBuilder, ScaleInvariantFeatureBuilder,
)
from quantlab.research.walk_forward import (
    _candidate_assessment, _common_sample_results, _feature_scale_audit,
    _importance_stability, _read_sealed_development_parquet, fixed_lightgbm_parameters,
    newey_west_mean_diagnostic,
)
from quantlab.research.walkforward_splits import split_walk_forward, validate_development_dates


def bars_fixture(symbol: str = "000001.SZ", *, price_scale: float = 1.0, volume_scale: float = 1.0,
                 count: int = 180) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=count)
    index = np.arange(count, dtype=float)
    close = price_scale * (25 + .02 * index + np.sin(index / 5) * .6)
    volume = volume_scale * (10000 + index * 31 + (index % 7) * 100)
    return pd.DataFrame({"symbol": symbol, "trade_date": dates, "open": close, "high": close + .1 * price_scale,
                         "low": close - .1 * price_scale, "close": close, "volume": volume,
                         "amount": close * volume, "adjustment": "qfq"})


def walkforward_fixture() -> tuple[pd.DataFrame, list[str]]:
    calendar = AShareTradingCalendar("2018-01-01", "2023-12-31")
    dates = calendar.sessions
    symbols = ["000001.SZ", "600519.SH", "000333.SZ"]
    rows = []
    for security_index, symbol in enumerate(symbols):
        for index, day in enumerate(dates[:-5]):
            row = {"symbol": symbol, "prediction_date": day, "label_end_date": dates[index + 5],
                   "target_5d": float(np.sin(index / 16 + security_index) / 100)}
            for feature_index, feature in enumerate(FEATURE_COLUMNS):
                row[feature] = float(np.sin(index / (feature_index + 2) + security_index))
            rows.append(row)
    return pd.DataFrame(rows), symbols


class WalkForwardAndScaleInvariantTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_testdata" / self._testMethodName
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_walk_forward_fold_dates_purging_and_embargo(self):
        dataset, _ = walkforward_fixture()
        folds = split_walk_forward(dataset, FEATURE_COLUMNS, AShareTradingCalendar("2018-01-01", "2023-12-31"))
        self.assertEqual([fold.fold_id for fold in folds], ["fold_1", "fold_2", "fold_3"])
        self.assertEqual([fold.metadata["validation_configured_start"][:4] for fold in folds], ["2021", "2022", "2023"])
        for fold in folds:
            self.assertEqual(fold.metadata["embargo_sessions"], 5)
            self.assertGreater(fold.metadata["purged_train_rows"], 0)
            self.assertGreater(fold.metadata["purged_validation_rows"], 0)
            self.assertTrue(all(fold.metadata["boundary_checks"].values()))
            self.assertLess(fold.train.label_end_date.max(), pd.Timestamp(fold.metadata["validation_configured_start"]))
            next_start = pd.Timestamp(fold.metadata["validation_configured_end"]) + pd.Timedelta(days=1)
            self.assertLess(fold.validation.label_end_date.max(), next_start)
            self.assertTrue((fold.validation.prediction_date < "2024-01-01").all())

    def test_validation_samples_do_not_overlap_between_folds(self):
        dataset, _ = walkforward_fixture()
        folds = split_walk_forward(dataset, FEATURE_COLUMNS, AShareTradingCalendar("2018-01-01", "2023-12-31"))
        keys = [set(zip(f.validation.symbol, f.validation.prediction_date)) for f in folds]
        self.assertFalse(keys[0] & keys[1] or keys[0] & keys[2] or keys[1] & keys[2])

    def test_walk_forward_rejects_legacy_test_prediction_dates(self):
        frame = pd.DataFrame({"prediction_date": [pd.Timestamp("2024-01-02")],
                              "label_end_date": [pd.Timestamp("2024-01-09")]})
        with self.assertRaisesRegex(ValueError, "legacy observed test"):
            validate_development_dates(frame)

    def test_walk_forward_rejects_legacy_test_label_dates(self):
        frame = pd.DataFrame({"prediction_date": [pd.Timestamp("2023-12-20")],
                              "label_end_date": [pd.Timestamp("2024-01-03")]})
        with self.assertRaisesRegex(ValueError, "labels realized"):
            validate_development_dates(frame)

    def test_v2_feature_representation_is_scale_invariant(self):
        base = bars_fixture(count=180)
        scaled = bars_fixture(price_scale=100., volume_scale=1000., count=180)
        first = ScaleInvariantFeatureBuilder().build(base)
        second = ScaleInvariantFeatureBuilder().build(scaled)
        np.testing.assert_allclose(first.loc[70:, FEATURE_V2_COLUMNS], second.loc[70:, FEATURE_V2_COLUMNS],
                                   rtol=1e-10, atol=1e-10, equal_nan=True)

    def test_v2_rolling_features_are_isolated_per_symbol(self):
        first = bars_fixture("000001.SZ", count=100)
        second = bars_fixture("600519.SH", price_scale=100., volume_scale=20., count=100)
        combined = ScaleInvariantFeatureBuilder().build(pd.concat([first, second], ignore_index=True))
        isolated = ScaleInvariantFeatureBuilder().build(first)
        left = combined.loc[combined.symbol == "000001.SZ", FEATURE_V2_COLUMNS].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, isolated[FEATURE_V2_COLUMNS].reset_index(drop=True))

    def test_v2_zero_denominators_nan_inf_and_zero_volume(self):
        bars = bars_fixture(count=90)
        bars.loc[:64, "close"] = 0.0
        bars.loc[:64, "volume"] = 0.0
        bars.loc[65, "volume"] = 100.0
        bars.loc[66, "volume"] = 0.0
        features = ScaleInvariantFeatureBuilder().build(bars)
        values = features[FEATURE_V2_COLUMNS].to_numpy(dtype=float)
        self.assertFalse(np.isinf(values).any())
        self.assertTrue(features.loc[:63, ["Close_to_MA5", "Close_to_MA60", "MACD_to_Close", "Volume_to_MA5"]].isna().all(axis=None))
        self.assertEqual(features.loc[66, "Volume_to_MA5"], 0.0)
        self.assertAlmostEqual(features.loc[66, "Volume_Change_1d"], -1.0)
        self.assertTrue(features.loc[65, "Volume_Change_1d"] != np.inf)

    def test_v2_has_no_absolute_price_or_volume_level_feature(self):
        self.assertEqual(set(FEATURE_V2_COLUMNS) & set(FEATURE_V1_SCALE_DEPENDENT), set())
        self.assertNotIn("symbol", FEATURE_V2_COLUMNS)
        self.assertIn("Close_to_MA60", FEATURE_V2_COLUMNS)
        self.assertIn("Volume_Change_1d", FEATURE_V2_COLUMNS)

    def test_v1_scale_audit_classifies_expected_features(self):
        self.assertIn("MA_60", FEATURE_V1_SCALE_DEPENDENT)
        self.assertIn("MACD", FEATURE_V1_SCALE_DEPENDENT)
        self.assertIn("Volume_MA20", FEATURE_V1_SCALE_DEPENDENT)
        self.assertIn("Return_20d", FEATURE_V1_SCALE_INVARIANT)
        self.assertIn("Volume_Ratio", FEATURE_V1_SCALE_INVARIANT)

    def test_v2_builder_refuses_feature_dataset_after_2023(self):
        (self.root / "config.json").write_text(json.dumps({"adjustment": "qfq", "market": "CN_A",
            "date_range": {"start": "2018-01-01", "end": "2025-12-31"},
            "maximum_missing_session_ratio": .02}), encoding="utf-8")
        (self.root / "pool.json").write_text(json.dumps({"pool_id": "test", "selection_date": "2023-01-01",
            "research_start": "2018-01-01", "research_end": "2025-12-31", "survivorship_bias_note": "fixture",
            "members": []}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sealed at 2023-12-31"):
            build_research_dataset(pool_path=self.root / "pool.json", research_config_path=self.root / "config.json",
                                   start="2018-01-01", end="2024-01-01", provider="baostock",
                                   output_path=self.root / "v2.parquet", feature_version=FEATURE_V2_VERSION)

    def test_arrow_sealed_reader_filters_legacy_observed_rows(self):
        path = self.root / "fixture.parquet"
        pd.DataFrame({"prediction_date": pd.to_datetime(["2023-12-01", "2024-01-02"]),
                      "label_end_date": pd.to_datetime(["2023-12-08", "2024-01-09"]),
                      "symbol": ["000001.SZ", "000001.SZ"]}).to_parquet(path, index=False)
        frame = _read_sealed_development_parquet(path)
        self.assertEqual(len(frame), 1)
        self.assertLess(frame.prediction_date.max(), pd.Timestamp("2024-01-01"))
        self.assertLess(frame.label_end_date.max(), pd.Timestamp("2024-01-01"))

    def test_common_sample_alignment_and_same_target(self):
        dates = pd.to_datetime(["2021-02-01"] * 4)
        symbols = ["000001.SZ", "600519.SH", "000333.SZ", "000651.SZ"]
        label_end = pd.to_datetime(["2021-02-08"] * 4)
        left = pd.DataFrame({"symbol": symbols, "prediction_date": dates, "label_end_date": label_end,
                             "target_5d": [.01, .02, .03, .04], "lightgbm": [.01, .03, .02, .05]})
        right = left.iloc[:3].copy().assign(lightgbm=[.02, .01, .04])
        fold1 = {"fold_id": "fold_1", "native_predictions": left.to_dict("records"),
                 "common_output_path": str(self.root / "common.parquet"),
                 "common_daily_output_path": str(self.root / "common.csv")}
        fold2 = {"fold_id": "fold_1", "native_predictions": right.to_dict("records"),
                 "common_output_path": str(self.root / "common2.parquet"),
                 "common_daily_output_path": str(self.root / "common2.csv")}
        result = _common_sample_results([fold1], [fold2])["fold_1"]
        self.assertTrue(result["same_sample_keys"])
        self.assertEqual(result["common_validation_rows"], 3)
        self.assertTrue(Path(fold1["common_output_path"]).exists())

    def test_common_sample_rejects_mismatched_targets(self):
        common = {"symbol": ["000001.SZ", "600519.SH", "000333.SZ"], "prediction_date": pd.to_datetime(["2021-02-01"] * 3),
                  "label_end_date": pd.to_datetime(["2021-02-08"] * 3), "target_5d": [.01, .02, .03],
                  "lightgbm": [.01, .02, .03]}
        left = pd.DataFrame(common)
        right = left.copy()
        right.loc[0, "target_5d"] = .99
        folds = []
        for index, frame in enumerate((left, right)):
            folds.append({"fold_id": "fold_1", "native_predictions": frame.to_dict("records"),
                          "common_output_path": str(self.root / f"common{index}.parquet"),
                          "common_daily_output_path": str(self.root / f"common{index}.csv")})
        with self.assertRaisesRegex(ValueError, "targets differ"):
            _common_sample_results([folds[0]], [folds[1]])

    def test_feature_importance_is_saved_and_fold_stability_computed(self):
        folds = [
            {"fold_id": f"fold_{index}", "feature_importance": [
                {"feature": "Return_1d", "gain": 10.0 if index != 2 else 1.0, "split": 5},
                {"feature": "MA_20", "gain": 2.0 if index != 2 else 11.0, "split": 2},
                {"feature": "RSI_14", "gain": 1.0, "split": 1},
            ]} for index in (1, 2, 3)
        ]
        result = _importance_stability(folds)
        self.assertEqual(len(result["folds"]), 3)
        self.assertIn("fold_1__fold_2", result["pairwise_rank_correlations"])
        ma = next(row for row in result["aggregate"] if row["feature"] == "MA_20")
        self.assertTrue(ma["v1_scale_dependent"])
        self.assertGreaterEqual(ma["top5_fold_count"], 1)

    def test_newey_west_lag5_diagnostic_is_finite_and_labeled(self):
        result = newey_west_mean_diagnostic(pd.Series([.1, .2, -.05, .03, .07, -.01, .02, .09]), lag=5)
        self.assertEqual(result["lag"], 5)
        self.assertGreater(result["standard_error"], 0)
        self.assertIsNotNone(result["t_diagnostic"])
        with self.assertRaises(ValueError):
            newey_west_mean_diagnostic([.1, .2], lag=-1)

    def test_fixed_lightgbm_params_are_config_values_without_search(self):
        params = fixed_lightgbm_parameters(Path(__file__).parents[1] / "config" / "research.json")
        config = json.loads((Path(__file__).parents[1] / "config" / "research.json").read_text(encoding="utf-8"))
        for key, value in config["lightgbm"].items():
            self.assertEqual(params[key], value)
        self.assertEqual(params["n_estimators"], 300)
        self.assertNotIn("early_stopping_rounds", params)

    def test_scale_diagnostic_reports_per_symbol_ranges_and_correlations(self):
        store = MarketDataStore(self.root / "data")
        symbols = ["000001.SZ", "600519.SH", "000333.SZ"]
        rows = []
        for index, symbol in enumerate(symbols):
            raw = bars_fixture(symbol, price_scale=10 ** index, volume_scale=10 ** index, count=90)
            identity = normalize_ashare_symbol(symbol)
            canonical = normalize_daily_bars(raw, market="CN_A", exchange=identity.exchange, symbol=symbol,
                adjustment="qfq", currency="CNY", source="baostock", provider_version="fixture",
                source_interface="fixture")
            store.merge_validated(canonical)
            f = FeatureBuilder().build(raw)
            f["target_5d"] = .01
            f["label_end_date"] = pd.to_datetime(f["prediction_date"]) + pd.Timedelta(days=7)
            rows.append(f)
        audit = _feature_scale_audit(pd.concat(rows, ignore_index=True), root=self.root)
        self.assertEqual(len(audit["per_symbol_feature_median_and_range"]), 3)
        self.assertFalse(audit["symbol_feature_added"])
        self.assertIn("MA_20", audit["absolute_feature_correlations"])

    def test_manifest_governance_marks_baseline_legacy_and_holdout_sealed(self):
        governance = json.loads((Path(__file__).parents[1] / "config" / "research_governance.json").read_text(encoding="utf-8"))
        baseline = governance["experiments"]["20261004T120849Z_2cb6448746"]
        self.assertEqual(baseline["evaluation_role"], "legacy_observed_test")
        self.assertTrue(baseline["immutable"])
        self.assertEqual(governance["sealed_holdout"]["role"], "future_sealed_holdout")
        self.assertFalse(governance["sealed_holdout"]["used"])

    def test_candidate_assessment_has_no_fixed_ic_magnitude_threshold(self):
        def metrics(rank_value, pos_day):
            return {"rank_ic": {"mean": rank_value, "positive_day_ratio": pos_day}}
        common = {"lightgbm_v1": {
            "rank_ic_mean": {"sign_consistency": 2 / 3, "std": .02},
            "positive_rank_ic_day_ratio": {"mean": .5}}, "lightgbm_v2": {
            "rank_ic_mean": {"sign_consistency": 1., "std": .001},
            "positive_rank_ic_day_ratio": {"mean": .6}}}
        ranks = {f"fold_{i}": {"metrics": {"lightgbm_v1": metrics(v, .5), "lightgbm_v2": metrics(v2, .6)}}
                 for i, (v, v2) in enumerate(zip([-.01, .02, .01], [.001, .002, .003]), 1)}
        importance = {"pairwise_rank_correlations": {"a": .5}}
        importance2 = {"pairwise_rank_correlations": {"a": .9}}
        prediction = {"prediction_to_target_std_ratio": .5}
        v1 = {"feature_importance_stability": importance, "folds": [{"metrics": prediction}] * 3}
        v2 = {"feature_importance_stability": importance2, "folds": [{"metrics": prediction}] * 3}
        result = _candidate_assessment(v1, v2, common, ranks)
        self.assertEqual(result["status"], "candidate_for_larger_universe")
        self.assertTrue(result["conditions"]["positive_rank_ic_in_at_least_two_folds"])


if __name__ == "__main__":
    unittest.main()
