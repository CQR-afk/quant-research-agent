"""Offline checks for the frozen development breadth protocol."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from quantlab.research.breadth_study import (
    _metrics, _write_json_new, align_anchor_predictions, assess_breadth,
    compare_legacy_u11, compare_nested_frames, fixed_lightgbm_parameters,
    summarize_common_dates, validate_dataset_manifest, validate_frozen_universes,
    validate_trade_status,
)
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import project_root
from quantlab.research.features import FEATURE_COLUMNS
from quantlab.research.walkforward_splits import split_walk_forward, validate_development_dates


class BreadthStudyTests(unittest.TestCase):
    def setUp(self):
        self.temp = project_root() / "tests" / "_testdata" / "breadth_study" / self._testMethodName
        self.temp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        expected_root = (project_root() / "tests" / "_testdata" / "breadth_study").resolve()
        target = self.temp.resolve()
        if expected_root not in target.parents:
            raise AssertionError("Refusing to remove a test directory outside the workspace test root.")
        shutil.rmtree(target)

    def test_frozen_universes_nested_and_hashes(self):
        result = validate_frozen_universes(project_root() / "config")
        self.assertEqual([len(result["symbols"][name]) for name in ("U11", "U50", "U100")], [11, 50, 100])
        self.assertEqual(result["hashes"]["U100"], "05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9")

    def test_manifest_refuses_future_scope_and_changed_model_inputs(self):
        path = self.temp / "dataset.parquet"
        path.write_bytes(b"fixture")
        manifest = {"pool_size": 50, "provider": "baostock", "adjustment": "qfq",
                        "feature_version": "ashare_daily_v1", "target_version": "target_5d_h5_v1",
                        "target_horizon_observations": 5, "research_start": "2018-01-01",
                        "research_end": "2023-12-31", "feature_columns": FEATURE_COLUMNS,
                        "universe_hash": "frozen", "development_only": True,
                        "dataset_path": str(path.resolve())}
        validate_dataset_manifest("U50", manifest, path, "frozen")
        for key, value in (("research_end", "2024-01-01"), ("provider", "akshare"),
                           ("feature_columns", FEATURE_COLUMNS[:-1]), ("target_version", "changed")):
            invalid = dict(manifest, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_dataset_manifest("U50", invalid, path, "frozen")

    def test_nested_shared_samples_must_match(self):
        a = pd.DataFrame({"symbol": ["000001.SZ"], "prediction_date": [pd.Timestamp("2021-01-04")],
                          "target_5d": [.02], "row_hash": ["a"]})
        b = pd.concat([a, pd.DataFrame({"symbol": ["000002.SZ"],
                "prediction_date": [pd.Timestamp("2021-01-04")], "target_5d": [.01], "row_hash": ["b"]})], ignore_index=True)
        self.assertEqual(compare_nested_frames(a, b, ["target_5d", "row_hash"])["matched_keys"], 1)
        b.loc[0, "target_5d"] = .03
        with self.assertRaisesRegex(ValueError, "disagree"):
            compare_nested_frames(a, b, ["target_5d", "row_hash"])

    def test_no_trade_and_unknown_cannot_enter_current_samples(self):
        keys = pd.MultiIndex.from_tuples([("000001.SZ", pd.Timestamp("2021-01-04"))])
        metadata = pd.DataFrame({"symbol": ["000001.SZ"], "trade_date": [pd.Timestamp("2021-01-04")],
                                 "metadata_json": [json.dumps({"tradestatus": "1"})]})
        self.assertEqual(validate_trade_status(keys, metadata)["trading"], 1)
        metadata.loc[0, "metadata_json"] = json.dumps({"tradestatus": "0"})
        with self.assertRaisesRegex(ValueError, "no-trade"):
            validate_trade_status(keys, metadata)
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_trade_status(keys, metadata.iloc[0:0])

    def test_immutable_u11_difference_is_classified(self):
        old = pd.DataFrame({"symbol": ["000001.SZ"], "prediction_date": [pd.Timestamp("2021-01-04")],
                            "row_hash": ["a"], "target_5d": [.02], "label_end_date": [pd.Timestamp("2021-01-11")],
                            **{column: [1.] for column in FEATURE_COLUMNS}})
        current = old.iloc[0:0].copy()
        metadata = pd.DataFrame({"symbol": ["000001.SZ"], "trade_date": [pd.Timestamp("2021-01-04")],
                                 "metadata_json": [json.dumps({"tradestatus": "0"})]})
        result = compare_legacy_u11(old, current, metadata)
        self.assertEqual(result["old_only_provider_no_trade"], 1)

    def test_walk_forward_purges_label_boundary_without_shuffle(self):
        dates = AShareTradingCalendar("2018-01-01", "2023-12-31").sessions
        frame = pd.DataFrame({"symbol": "000001.SZ", "prediction_date": dates,
                              "label_end_date": pd.Series(dates).shift(-5), "target_5d": 0.01})
        frame[FEATURE_COLUMNS] = 1.0
        folds = split_walk_forward(frame, FEATURE_COLUMNS,
                                  AShareTradingCalendar("2018-01-01", "2023-12-31"), embargo_sessions=5)
        self.assertEqual(len(folds), 3)
        self.assertTrue(all(f.metadata["embargo_sessions"] == 5 for f in folds))
        self.assertTrue(all(all(f.metadata["boundary_checks"].values()) for f in folds))
        with self.assertRaises(ValueError):
            split_walk_forward(frame, FEATURE_COLUMNS, AShareTradingCalendar("2018-01-01", "2023-12-31"), embargo_sessions=0)

    def test_legacy_and_future_holdout_date_guard(self):
        for prediction, ending in (("2024-01-02", "2024-01-09"), ("2023-12-29", "2026-01-02")):
            with self.subTest(prediction=prediction), self.assertRaises(ValueError):
                validate_development_dates(pd.DataFrame({"prediction_date": [prediction], "label_end_date": [ending]}))

    def test_common_date_alignment_uses_only_all_valid_dates(self):
        days = pd.to_datetime(["2021-01-04", "2021-01-05"])
        frame = pd.DataFrame({"prediction_date": days, "spearman_rank_ic": [.1, .2], "sample_count": [11, 11]})
        other = frame.copy()
        other.loc[1, "spearman_rank_ic"] = np.nan
        result = summarize_common_dates({"U11": frame, "U50": other, "U100": frame})
        self.assertEqual(result["common_date_count"], 1)
        self.assertEqual(result["U11"]["dropped_dates"], 1)
        self.assertAlmostEqual(result["U11"]["common"]["rank_ic"]["mean"], .1)

    def test_anchor_alignment_requires_same_raw_target(self):
        base = pd.DataFrame({"symbol": ["000001.SZ", "000002.SZ"],
                             "prediction_date": pd.to_datetime(["2021-01-04"] * 2),
                             "target_5d": [.1, .2], "lightgbm": [.01, .02]})
        frames = {name: base.copy() for name in ("U11", "U50", "U100")}
        self.assertEqual(len(align_anchor_predictions(frames, {"000001.SZ"})["U100"]), 1)
        frames["U100"].loc[0, "target_5d"] = .5
        with self.assertRaisesRegex(ValueError, "raw target"):
            align_anchor_predictions(frames, {"000001.SZ"})

    def test_daily_ic_needs_three_samples_and_constant_is_na(self):
        rows = pd.DataFrame({"symbol": ["a", "b"], "prediction_date": pd.Timestamp("2021-01-04"),
                             "target_5d": [.1, .2], "lightgbm": [.01, .02]})
        self.assertEqual(_metrics(rows, "lightgbm")[0]["rank_ic"]["valid_days"], 0)
        rows.loc[2] = ["c", pd.Timestamp("2021-01-04"), .3, .03]
        self.assertEqual(_metrics(rows, "lightgbm")[0]["rank_ic"]["valid_days"], 1)
        rows["lightgbm"] = .01
        self.assertIsNone(_metrics(rows, "lightgbm")[0]["rank_ic"]["mean"])

    def test_fixed_params_reload_and_immutable_report(self):
        params = fixed_lightgbm_parameters(project_root() / "config" / "research.json")
        self.assertEqual(params["random_state"], 42)
        self.assertEqual(params["n_estimators"], 300)
        x = pd.DataFrame({"x": np.arange(80, dtype=float)})
        y = np.sin(x["x"].to_numpy())
        model = LGBMRegressor(**params).fit(x, y)
        path = self.temp / "model.joblib"
        joblib.dump(model, path)
        np.testing.assert_allclose(model.predict(x), joblib.load(path).predict(x), rtol=0, atol=1e-12)
        report = self.temp / "report.json"
        _write_json_new(report, {"status": "complete"})
        with self.assertRaises(FileExistsError):
            _write_json_new(report, {"status": "changed"})

    def test_preregistered_assessment_rejects_two_negative_folds(self):
        folds = [{"metrics": {"lightgbm": {"rank_ic": {"mean": value, "positive_day_ratio": .6}}}}
                 for value in (-.01, .05, -.02)]
        self.assertEqual(assess_breadth(folds, "u100_data_ready")["status"], "breadth_not_supported")


if __name__ == "__main__":
    unittest.main()
