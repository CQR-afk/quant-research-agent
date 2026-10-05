"""Development-only breadth gate for the frozen U100 research universe."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quantlab.research.features import FEATURE_VERSION, feature_spec


DEVELOPMENT_END = pd.Timestamp("2023-12-31")


def evaluate_u100_breadth(
    dataset: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    start: str = "2018-01-01",
    end: str = "2023-12-31",
) -> dict[str, object]:
    """Require 100/100 symbol coverage and fixed 80/90 cross-sectional widths."""
    research_start, research_end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if research_start < pd.Timestamp("2018-01-01") or research_end > DEVELOPMENT_END:
        raise ValueError("U100 breadth gate is scoped to 2018-01-01 through 2023-12-31.")
    if research_start > research_end:
        raise ValueError("Research start must be on or before end.")

    frame = dataset.copy()
    if len(frame):
        frame["prediction_date"] = pd.to_datetime(frame["prediction_date"], errors="coerce").dt.normalize()
        frame["label_end_date"] = pd.to_datetime(frame["label_end_date"], errors="coerce").dt.normalize()
        if frame["prediction_date"].isna().any() or frame["prediction_date"].gt(research_end).any():
            raise ValueError("U100 prediction date exceeds the development-period cap or is invalid.")
        if frame["label_end_date"].dropna().gt(research_end).any():
            raise ValueError("U100 label end date exceeds the development-period cap.")
        if frame.duplicated(["symbol", "prediction_date"]).any():
            raise ValueError("Duplicate U100 sample keys are not allowed.")
    columns, _, _ = feature_spec(FEATURE_VERSION)
    required = set(columns + ["symbol", "prediction_date", "target_5d", "label_end_date"])
    if required - set(frame.columns):
        raise ValueError(f"U100 dataset is missing required columns: {', '.join(sorted(required - set(frame.columns)))}.")

    complete_features = frame[columns].notna().all(axis=1) if len(frame) else pd.Series(dtype=bool)
    labels = pd.to_numeric(frame["target_5d"], errors="coerce") if len(frame) else pd.Series(dtype=float)
    finite_labels = labels.map(np.isfinite) if len(frame) else pd.Series(dtype=bool)
    usable_mask = complete_features & finite_labels & frame["label_end_date"].notna()
    usable = frame.loc[usable_mask].copy()
    daily = usable.groupby("prediction_date")["symbol"].nunique().astype("int64") if len(usable) else pd.Series(dtype="int64")
    active_dates = len(daily)
    ratio_80 = float((daily >= 80).mean()) if active_dates else 0.0
    ratio_90 = float((daily >= 90).mean()) if active_dates else 0.0
    rows_present = (pd.to_numeric(coverage["rows"], errors="coerce").fillna(0)
                    if len(coverage) and "rows" in coverage else pd.Series(dtype=float))
    data_symbols = int(coverage.loc[rows_present > 0, "symbol"].nunique()) if len(coverage) else 0
    feature_symbols = int(frame.loc[complete_features, "symbol"].nunique()) if len(frame) else 0
    label_symbols = int(usable["symbol"].nunique()) if len(usable) else 0
    symbols_80 = math.ceil(100 * 0.80)
    symbols_90 = math.ceil(100 * 0.90)
    checks = {
        "frozen_universe_size": len(coverage) == 100 and coverage["symbol"].nunique() == 100,
        "all_100_have_data": data_symbols == 100,
        "all_100_have_complete_features": feature_symbols == 100,
        "all_100_have_valid_targets": label_symbols == 100,
        "80pct_width_on_80pct_active_dates": ratio_80 >= 0.80,
        "90pct_width_on_80pct_active_dates": ratio_90 >= 0.80,
    }
    return {
        "status": "u100_data_ready" if all(checks.values()) else "u100_data_incomplete",
        "evaluation": "development_only",
        "research_start": research_start.date().isoformat(),
        "research_end_cap": research_end.date().isoformat(),
        "universe_size": 100,
        "symbols_with_data": data_symbols,
        "symbols_with_complete_features": feature_symbols,
        "symbols_with_valid_targets": label_symbols,
        "active_prediction_dates": active_dates,
        "daily_cross_section": {
            "minimum": int(daily.min()) if active_dates else 0,
            "p10": float(daily.quantile(0.10)) if active_dates else 0.0,
            "median": float(daily.median()) if active_dates else 0.0,
            "p90": float(daily.quantile(0.90)) if active_dates else 0.0,
            "maximum": int(daily.max()) if active_dates else 0,
            "threshold_80pct_symbols": symbols_80,
            "dates_at_or_above_80pct_width": int((daily >= symbols_80).sum()),
            "ratio_dates_at_or_above_80pct_width": ratio_80,
            "threshold_90pct_symbols": symbols_90,
            "dates_at_or_above_90pct_width": int((daily >= symbols_90).sum()),
            "ratio_dates_at_or_above_90pct_width": ratio_90,
        },
        "coverage_by_symbol": coverage.to_dict("records"),
        "checks": checks,
        "holdout_access": "not accessed; no 2024+ observations supplied to this gate",
        "model_training": "not performed",
        "backtest": "not performed",
    }
