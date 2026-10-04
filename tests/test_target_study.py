from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.research.target_study import (
    _align_common_validation_samples,
    _candidate_assessment,
    _daily_summary,
    _native_fit_metrics,
    _verify_unused_holdout,
    fixed_lightgbm_parameters,
    materialize_target_datasets,
)
from quantlab.research.targets import (
    CS_RANK_TARGET_VERSION,
    POOL_EXCESS_TARGET_VERSION,
    RAW_TARGET_VERSION,
    TARGET_COLUMNS,
    build_cross_sectional_targets,
    target_definitions,
)
from quantlab.research.features import FEATURE_COLUMNS


def make_panel(*, dates: int = 2, symbols: int = 9) -> pd.DataFrame:
    records = []
    start = pd.Timestamp("2023-03-01")
    for day_index in range(dates):
        day = start + pd.offsets.BDay(day_index)
        for symbol_index in range(symbols):
            records.append({
                "symbol": f"{symbol_index:06d}.SZ",
                "prediction_date": day,
                "label_end_date": day + pd.offsets.BDay(5),
                "target_5d": (symbol_index // 2) * 0.01 if symbol_index != symbols - 1 or day_index == 0 else np.nan,
                "Return_1d": symbol_index / 100,
            })
    return pd.DataFrame(records)


class CrossSectionalTargetTests(unittest.TestCase):
    def test_raw_target_unchanged_pool_excess_definition_and_neutrality(self) -> None:
        source = make_panel()
        original = source["target_5d"].copy()
        result = build_cross_sectional_targets(source, feature_columns=["Return_1d"], minimum_cross_section=8)
        frame = result.dataset
        self.assertTrue(frame[TARGET_COLUMNS[RAW_TARGET_VERSION]].equals(original))
        day = frame.loc[frame["prediction_date"] == frame["prediction_date"].min()]
        expected_mean = day["target_5d"].mean()
        np.testing.assert_allclose(
            day[TARGET_COLUMNS[POOL_EXCESS_TARGET_VERSION]], day["target_5d"] - expected_mean,
        )
        self.assertLess(float(result.daily_diagnostics["pool_excess_abs_mean"].max()), 1e-12)

    def test_rank_target_average_ties_are_monotonic_and_centered(self) -> None:
        source = make_panel(symbols=8)
        result = build_cross_sectional_targets(source, feature_columns=["Return_1d"], minimum_cross_section=8)
        frame = result.dataset
        rank = TARGET_COLUMNS[CS_RANK_TARGET_VERSION]
        tied = frame.loc[frame["target_5d"] == frame["target_5d"].iloc[0], rank].dropna().unique()
        self.assertEqual(len(tied), 1)
        ordered = frame.dropna(subset=["target_5d", rank]).sort_values("target_5d")
        self.assertTrue(ordered[rank].is_monotonic_increasing)
        self.assertTrue(result.daily_diagnostics["rank_target_monotonic_with_raw"].all())
        self.assertEqual(target_definitions(8)[CS_RANK_TARGET_VERSION]["rank_method"], "average")

    def test_minimum_cross_section_makes_entire_day_unavailable(self) -> None:
        source = make_panel(symbols=9)
        second = source["prediction_date"].max()
        day_mask = source["prediction_date"] == second
        source.loc[source.index[day_mask][-2:], "target_5d"] = np.nan
        result = build_cross_sectional_targets(source, feature_columns=["Return_1d"], minimum_cross_section=8)
        unavailable = result.dataset.loc[day_mask]
        self.assertTrue(unavailable[TARGET_COLUMNS[POOL_EXCESS_TARGET_VERSION]].isna().all())
        self.assertTrue(unavailable[TARGET_COLUMNS[CS_RANK_TARGET_VERSION]].isna().all())
        row = result.daily_diagnostics.loc[result.daily_diagnostics["prediction_date"] == second].iloc[0]
        self.assertEqual(row["valid_raw_target_symbols"], 7)
        self.assertFalse(row["cross_section_target_available"])

    def test_target_construction_preserves_features_and_label_end(self) -> None:
        source = make_panel()
        original_features = source[["Return_1d"]].copy()
        original_end = source["label_end_date"].copy()
        result = build_cross_sectional_targets(source, feature_columns=["Return_1d"])
        pd.testing.assert_frame_equal(result.dataset[["Return_1d"]], original_features)
        pd.testing.assert_series_equal(result.dataset["label_end_date"], original_end)
        self.assertTrue(set(TARGET_COLUMNS.values()).isdisjoint({"Return_1d"}))

    def test_duplicate_keys_and_target_feature_overlap_are_rejected(self) -> None:
        source = make_panel(symbols=8)
        duplicate = pd.concat([source, source.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_cross_sectional_targets(duplicate, feature_columns=["Return_1d"])
        with self.assertRaisesRegex(ValueError, "may not be included"):
            build_cross_sectional_targets(source, feature_columns=["Return_1d", TARGET_COLUMNS[CS_RANK_TARGET_VERSION]])

    def test_2024_rows_filtered_and_target_datasets_are_versioned_and_idempotent(self) -> None:
        source = make_panel(dates=4, symbols=8)
        for column in FEATURE_COLUMNS:
            if column not in source.columns:
                source[column] = 0.0
        future = source.iloc[-8:].copy()
        future["prediction_date"] = pd.Timestamp("2024-01-02")
        future["label_end_date"] = pd.Timestamp("2024-01-09")
        source = pd.concat([source, future], ignore_index=True)
        root = Path(__file__).parent / "_testdata"
        in_data = root / "target_study_input_fixture.parquet"
        in_manifest = root / "target_study_input_fixture.manifest.json"
        output_dir = root / "target_study_outputs"
        output_dir.mkdir(exist_ok=True)
        outputs = [output_dir / f"{stem}.{suffix}" for stem in (
            "ashare_research_v1_target_raw_dev", "ashare_research_v1_target_pool_excess_dev",
            "ashare_research_v1_target_cs_rank_dev",
        ) for suffix in ("parquet", "manifest.json")]
        try:
            source.to_parquet(in_data, index=False)
            in_manifest.write_text(json.dumps({
                "feature_version": "ashare_daily_v1", "target_version": RAW_TARGET_VERSION,
                "evaluation_role": "development_walk_forward", "legacy_observed_test_rows_materialized": False,
                "provider": "baostock", "adjustment": "qfq", "research_end": "2023-12-31",
                "dataset_version": "fixture-source-v1", "feature_columns": FEATURE_COLUMNS,
                "market": "CN_A", "price_basis": "vendor_snapshot_adjusted", "pool_id": "fixture-pool",
                "pool_size": 8, "point_in_time_limit": "fixture only",
            }), encoding="utf-8")
            first = materialize_target_datasets(dataset_path=in_data, manifest_path=in_manifest, root=root,
                                                output_dir=output_dir)
            second = materialize_target_datasets(dataset_path=in_data, manifest_path=in_manifest, root=root,
                                                 output_dir=output_dir)
            for version in (RAW_TARGET_VERSION, POOL_EXCESS_TARGET_VERSION, CS_RANK_TARGET_VERSION):
                record = first["datasets"][version]
                self.assertLess(pd.to_datetime(record["dataset"]["prediction_date"]).max(), pd.Timestamp("2024-01-01"))
                self.assertEqual(record["manifest"]["target_version"], version)
                self.assertEqual(record["manifest"]["evaluation_role"], "development_walk_forward")
                self.assertEqual(record["manifest"]["development_date_cap"], "2023-12-31")
                self.assertEqual(record["manifest"]["dataset_version"], second["datasets"][version]["manifest"]["dataset_version"])
                self.assertTrue(record["manifest"]["label_end_date_invariant"])
        finally:
            for path in [in_data, in_manifest, *outputs]:
                path.unlink(missing_ok=True)
            output_dir.rmdir()

    def test_candidate_screening_retains_raw_when_both_candidates_fail(self) -> None:
        def metrics(rank_means: list[float], positive_ratio: float) -> dict[str, dict[str, object]]:
            return {
                f"fold_{index}": {
                    "rank_ic": {"mean": value, "icir": value, "positive_day_ratio": positive_ratio},
                    "pearson_ic": {"mean": value, "icir": value, "positive_day_ratio": positive_ratio},
                }
                for index, value in enumerate(rank_means, 1)
            }
        assessment = _candidate_assessment({
            RAW_TARGET_VERSION: metrics([.05, .04, .02], .53),
            POOL_EXCESS_TARGET_VERSION: metrics([.03, -.01, -.01], .50),
            CS_RANK_TARGET_VERSION: metrics([.04, .01, -.02], .51),
        })
        self.assertEqual(assessment["status"], "no_target_upgrade")
        self.assertEqual(assessment["selected_target"], RAW_TARGET_VERSION)
        self.assertFalse(assessment["candidate_checks"][POOL_EXCESS_TARGET_VERSION]["eligible"])
        self.assertFalse(assessment["candidate_checks"][CS_RANK_TARGET_VERSION]["eligible"])

    def test_common_alignment_requires_same_keys_end_dates_and_raw_targets(self) -> None:
        left = pd.DataFrame({
            "symbol": ["000001.SZ", "000002.SZ"],
            "prediction_date": pd.to_datetime(["2023-01-03", "2023-01-03"]),
            "label_end_date": pd.to_datetime(["2023-01-10", "2023-01-10"]),
            "raw_target_5d": [.01, .02], "model_score": [1., 2.],
        })
        right = left.iloc[:1].copy()
        aligned = _align_common_validation_samples({"a": left, "b": right, "c": left})
        self.assertEqual(aligned["symbol"].tolist(), ["000001.SZ"])
        bad = right.copy()
        bad["raw_target_5d"] = .9
        with self.assertRaisesRegex(ValueError, "different raw target"):
            _align_common_validation_samples({"a": left, "bad": bad})

    def test_investment_metric_uses_score_against_raw_return_not_native_rank_label(self) -> None:
        frame = pd.DataFrame({
            "symbol": [f"{n:06d}.SZ" for n in range(8)],
            "prediction_date": pd.Timestamp("2023-01-03"),
            "raw_target_5d": np.arange(8, dtype=float),
            "native_target": np.arange(8, dtype=float)[::-1],
            "model_score": np.arange(8, dtype=float),
        })
        _, metrics = _daily_summary(frame)
        self.assertAlmostEqual(metrics["rank_ic"]["mean"], 1.0)

    def test_native_mae_rmse_are_labeled_not_comparable_across_targets(self) -> None:
        result = _native_fit_metrics(pd.Series([-.5, 0, .5]), pd.Series([-.4, .1, .4]), CS_RANK_TARGET_VERSION)
        self.assertEqual(result["comparability"], "NOT CROSS-TARGET COMPARABLE")
        self.assertGreaterEqual(result["mae"], 0)
        self.assertGreaterEqual(result["rmse"], 0)

    def test_all_targets_use_same_frozen_model_parameters_and_unused_holdout(self) -> None:
        params = fixed_lightgbm_parameters(Path(__file__).parents[1] / "config" / "research.json")
        self.assertEqual(params, fixed_lightgbm_parameters(Path(__file__).parents[1] / "config" / "research.json"))
        self.assertEqual(params["random_state"], 42)
        _verify_unused_holdout({"sealed_holdout": {"role": "future_sealed_holdout", "used": False, "data_requested": False}})
        with self.assertRaisesRegex(ValueError, "remain unused"):
            _verify_unused_holdout({"sealed_holdout": {"role": "future_sealed_holdout", "used": True, "data_requested": False}})


if __name__ == "__main__":
    unittest.main()
