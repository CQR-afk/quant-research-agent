"""Development-only coverage and cross-sectional breadth gate for frozen U50."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.features import FEATURE_VERSION, feature_spec


DEVELOPMENT_END = pd.Timestamp("2023-12-31")


def evaluate_u50_breadth(
    dataset: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    universe_size: int = 50,
    start: str = "2018-01-01",
    end: str = "2023-12-31",
    train_end: str = "2022-12-31",
    validation_start: str = "2023-01-01",
    minimum_symbols: int = 50,
    minimum_rows_per_split: int = 200,
    maximum_missing_session_ratio: float = 0.02,
    embargo_sessions: int = 5,
) -> dict[str, object]:
    """Assess U50 sample width without reading any post-development observations."""
    research_start, research_end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
    if research_end > DEVELOPMENT_END:
        raise ValueError("U50 data gate is sealed at 2023-12-31; later dates are unavailable.")
    if research_start > research_end:
        raise ValueError("Research start must be on or before end.")
    if universe_size < 1 or not 0 <= maximum_missing_session_ratio <= 1:
        raise ValueError("Invalid breadth gate configuration.")

    frame = dataset.copy()
    if len(frame):
        frame["prediction_date"] = pd.to_datetime(frame["prediction_date"], errors="coerce").dt.normalize()
        frame["label_end_date"] = pd.to_datetime(frame["label_end_date"], errors="coerce").dt.normalize()
        if frame["prediction_date"].isna().any() or frame["prediction_date"].gt(research_end).any():
            raise ValueError("Prediction date exceeds the development-period cap or is invalid.")
        realized = frame["label_end_date"].dropna()
        if realized.gt(research_end).any():
            raise ValueError("Label end date exceeds the development-period cap.")
        if frame.duplicated(["symbol", "prediction_date"]).any():
            raise ValueError("Duplicate U50 sample keys are not allowed.")
    columns, _, _ = feature_spec(FEATURE_VERSION)
    missing_columns = set(columns + ["symbol", "prediction_date", "target_5d", "label_end_date"]) - set(frame.columns)
    if missing_columns:
        raise ValueError(f"U50 research dataset is missing required columns: {', '.join(sorted(missing_columns))}.")

    feature_complete = frame[columns].notna().all(axis=1) if len(frame) else pd.Series(dtype=bool)
    labels_finite = (pd.to_numeric(frame["target_5d"], errors="coerce").map(np.isfinite)
                     if len(frame) else pd.Series(dtype=bool))
    usable = frame.loc[feature_complete & labels_finite & frame["label_end_date"].notna()].copy()
    calendar = AShareTradingCalendar(research_start, research_end)
    if usable.empty:
        active_daily = pd.Series(dtype="int64")
    else:
        active_daily = usable.groupby("prediction_date")["symbol"].nunique().astype("int64")
    active_dates = active_daily.index
    daily = active_daily.reindex(active_dates, fill_value=0)
    threshold_80 = math.ceil(universe_size * 0.80)
    threshold_90 = math.ceil(universe_size * 0.90)
    active_date_count = int(len(daily))
    ratio_80 = float((daily >= threshold_80).mean()) if active_date_count else 0.0
    ratio_90 = float((daily >= threshold_90).mean()) if active_date_count else 0.0

    train_end_date = pd.Timestamp(train_end).normalize()
    validation_start_date = pd.Timestamp(validation_start).normalize()
    train_cutoff = calendar.label_cutoff(validation_start_date, embargo_sessions)
    train_candidates = usable.loc[usable["prediction_date"].between(research_start, train_end_date)]
    train = train_candidates.loc[train_candidates["label_end_date"] <= train_cutoff]
    validation = usable.loc[usable["prediction_date"].between(validation_start_date, research_end)]
    train_purged = len(train_candidates) - len(train)
    train_purge_ok = bool(train.empty or train["label_end_date"].max() <= train_cutoff)
    validation_cap_ok = bool(validation.empty or validation["label_end_date"].max() <= research_end)

    rows_present = pd.to_numeric(coverage.get("rows", pd.Series(dtype=float)), errors="coerce").fillna(0)
    symbols_with_data = int(coverage.loc[rows_present > 0, "symbol"].nunique()) if not coverage.empty else 0
    feature_symbols = int(frame.loc[feature_complete, "symbol"].nunique()) if len(frame) else 0
    labeled_symbols = int(usable["symbol"].nunique()) if len(usable) else 0
    missing_ratios = pd.to_numeric(coverage.get("missing_ratio", pd.Series(dtype=float)), errors="coerce")
    observed_ratios = missing_ratios[missing_ratios.notna()]
    coverage_ok = bool(len(coverage) == universe_size and len(observed_ratios) == universe_size
                       and (observed_ratios <= maximum_missing_session_ratio).all())

    checks = {
        "frozen_universe_size": len(coverage) == universe_size,
        "all_symbols_have_data": symbols_with_data >= minimum_symbols,
        "all_symbols_have_complete_features": feature_symbols >= minimum_symbols,
        "all_symbols_have_valid_labels": labeled_symbols >= minimum_symbols,
        "calendar_coverage": coverage_ok,
        "train_sample_floor": len(train) >= minimum_rows_per_split,
        "validation_sample_floor": len(validation) >= minimum_rows_per_split,
        "breadth_80pct_width": ratio_80 >= 0.80,
        "breadth_90pct_width": ratio_90 >= 0.80,
        "train_purge_and_embargo": train_purge_ok,
        "development_end_cap": validation_cap_ok,
    }
    result = {
        "status": "u50_data_ready" if all(checks.values()) else "u50_data_incomplete",
        "development_only": True,
        "research_start": research_start.date().isoformat(),
        "research_end_cap": research_end.date().isoformat(),
        "universe_size": universe_size,
        "symbols_with_data": symbols_with_data,
        "symbols_with_complete_features": feature_symbols,
        "symbols_with_valid_labels": labeled_symbols,
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "train_candidates_before_purge": int(len(train_candidates)),
        "train_purged_rows": int(train_purged),
        "train_label_cutoff": train_cutoff.date().isoformat(),
        "embargo_sessions": embargo_sessions,
        "daily_cross_section": {
            "active_prediction_dates": active_date_count,
            "minimum": int(daily.min()) if active_date_count else 0,
            "p10": float(daily.quantile(0.10)) if active_date_count else 0.0,
            "median": float(daily.median()) if active_date_count else 0.0,
            "p90": float(daily.quantile(0.90)) if active_date_count else 0.0,
            "maximum": int(daily.max()) if active_date_count else 0,
            "threshold_80pct_symbols": threshold_80,
            "dates_at_or_above_80pct_width": int((daily >= threshold_80).sum()),
            "ratio_dates_at_or_above_80pct_width": ratio_80,
            "threshold_90pct_symbols": threshold_90,
            "dates_at_or_above_90pct_width": int((daily >= threshold_90).sum()),
            "ratio_dates_at_or_above_90pct_width": ratio_90,
        },
        "coverage_by_symbol": coverage.to_dict("records"),
        "checks": checks,
        "holdout_access": "not accessed; no 2024+ data supplied to this gate",
        "model_training": "not performed",
    }
    return result
