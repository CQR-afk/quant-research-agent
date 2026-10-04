"""Target-only transformations for the frozen A-share feature dataset."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RAW_TARGET_VERSION = "target_5d_h5_v1"
POOL_EXCESS_TARGET_VERSION = "target_5d_pool_excess_v1"
CS_RANK_TARGET_VERSION = "target_5d_cs_rank_v1"
TARGET_VERSIONS = (RAW_TARGET_VERSION, POOL_EXCESS_TARGET_VERSION, CS_RANK_TARGET_VERSION)
TARGET_COLUMNS = {
    RAW_TARGET_VERSION: "target_5d_h5_v1",
    POOL_EXCESS_TARGET_VERSION: "target_5d_pool_excess_v1",
    CS_RANK_TARGET_VERSION: "target_5d_cs_rank_v1",
}
DEFAULT_MINIMUM_TARGET_CROSS_SECTION = 8


@dataclass(frozen=True)
class TargetBuildResult:
    dataset: pd.DataFrame
    daily_diagnostics: pd.DataFrame
    target_definitions: dict[str, dict[str, object]]


def target_definitions(minimum_cross_section: int = DEFAULT_MINIMUM_TARGET_CROSS_SECTION) -> dict[str, dict[str, object]]:
    if minimum_cross_section < 2:
        raise ValueError("minimum target cross-section must be at least two.")
    return {
        RAW_TARGET_VERSION: {
            "column": TARGET_COLUMNS[RAW_TARGET_VERSION],
            "definition": "close.shift(-5) / close - 1, on each symbol's ordered valid observations",
            "target_horizon_observations": 5,
            "target_cross_section_method": None,
            "minimum_target_cross_section": None,
        },
        POOL_EXCESS_TARGET_VERSION: {
            "column": TARGET_COLUMNS[POOL_EXCESS_TARGET_VERSION],
            "definition": "raw target_5d minus same prediction_date mean across valid members of the frozen engineering pool",
            "target_horizon_observations": 5,
            "target_cross_section_method": "subtract prediction-date pool mean (pool_excess; not market_excess)",
            "cross_section_center": "mean",
            "minimum_target_cross_section": minimum_cross_section,
        },
        CS_RANK_TARGET_VERSION: {
            "column": TARGET_COLUMNS[CS_RANK_TARGET_VERSION],
            "definition": "2 * rank(method='average', pct=True) - 1 across valid same-date pool raw target_5d",
            "target_horizon_observations": 5,
            "target_cross_section_method": "ascending percentile rank within prediction_date",
            "rank_method": "average",
            "rank_pct": True,
            "rank_scaling": "2 * rank_pct - 1; nominal scale [-1, 1], realized endpoints depend on breadth and ties",
            "minimum_target_cross_section": minimum_cross_section,
        },
    }


def build_cross_sectional_targets(
    dataset: pd.DataFrame,
    *,
    feature_columns: list[str],
    minimum_cross_section: int = DEFAULT_MINIMUM_TARGET_CROSS_SECTION,
) -> TargetBuildResult:
    """Derive target B/C by prediction date without altering raw target or features."""
    if minimum_cross_section < 2:
        raise ValueError("minimum target cross-section must be at least two.")
    target_names = set(TARGET_COLUMNS.values())
    if target_names & set(feature_columns):
        raise ValueError("Targets may not be included among model feature columns.")
    required = {"symbol", "prediction_date", "label_end_date", "target_5d", *feature_columns}
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(f"Target source dataset missing columns: {', '.join(sorted(missing))}.")
    if dataset.duplicated(["symbol", "prediction_date"]).any():
        raise ValueError("Target source contains duplicate symbol/prediction_date keys.")
    output = dataset.copy()
    output["prediction_date"] = pd.to_datetime(output["prediction_date"], errors="coerce").dt.normalize()
    output["label_end_date"] = pd.to_datetime(output["label_end_date"], errors="coerce").dt.normalize()
    if output["prediction_date"].isna().any():
        raise ValueError("Target source contains invalid prediction dates.")
    raw = pd.to_numeric(output["target_5d"], errors="coerce")
    if np.isinf(raw.to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError("Raw target contains infinite values; refusing to silently alter the control label.")
    # Preserve the control values byte-for-value as pandas float values, including missingness.
    output[TARGET_COLUMNS[RAW_TARGET_VERSION]] = raw
    output[TARGET_COLUMNS[POOL_EXCESS_TARGET_VERSION]] = np.nan
    output[TARGET_COLUMNS[CS_RANK_TARGET_VERSION]] = np.nan

    daily_records: list[dict[str, object]] = []
    for day, indices in output.groupby("prediction_date", sort=True).groups.items():
        valid = raw.loc[indices].dropna()
        count = int(len(valid))
        available = count >= minimum_cross_section
        if available:
            mean_return = float(valid.mean())
            excess = valid - mean_return
            ranks = 2.0 * valid.rank(method="average", pct=True, ascending=True) - 1.0
            output.loc[valid.index, TARGET_COLUMNS[POOL_EXCESS_TARGET_VERSION]] = excess
            output.loc[valid.index, TARGET_COLUMNS[CS_RANK_TARGET_VERSION]] = ranks
            max_abs_excess_mean = abs(float(excess.mean()))
            # Verify score ordering; tied raw outcomes must receive equal average ranks.
            ordered = pd.DataFrame({"raw": valid, "rank": ranks}).sort_values("raw", kind="stable")
            monotonic = bool(ordered["rank"].is_monotonic_increasing)
        else:
            mean_return = None
            max_abs_excess_mean = None
            monotonic = None
        daily_records.append({
            "prediction_date": day,
            "valid_raw_target_symbols": count,
            "minimum_target_cross_section": minimum_cross_section,
            "cross_section_target_available": available,
            "raw_target_mean": mean_return,
            "pool_excess_target_mean": float(output.loc[valid.index, TARGET_COLUMNS[POOL_EXCESS_TARGET_VERSION]].mean()) if available else None,
            "pool_excess_abs_mean": max_abs_excess_mean,
            "rank_target_monotonic_with_raw": monotonic,
        })

    diagnostics = pd.DataFrame(daily_records)
    definitions = target_definitions(minimum_cross_section)
    # Target construction must leave every feature and the original raw control untouched.
    if not output[feature_columns].equals(dataset[feature_columns]):
        raise AssertionError("Target construction modified model features.")
    if not output[TARGET_COLUMNS[RAW_TARGET_VERSION]].equals(pd.to_numeric(dataset["target_5d"], errors="coerce")):
        raise AssertionError("Raw return control changed during target construction.")
    return TargetBuildResult(output, diagnostics, definitions)
