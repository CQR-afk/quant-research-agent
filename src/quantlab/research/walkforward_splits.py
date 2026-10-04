"""Pre-2024 globally dated expanding-window splits with horizon-aware purging."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quantlab.research.calendar import AShareTradingCalendar

DEVELOPMENT_CUTOFF = pd.Timestamp("2024-01-01")
WALK_FORWARD_DEFINITIONS = (
    ("fold_1", "2018-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
    ("fold_2", "2018-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
    ("fold_3", "2018-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
)


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    train: pd.DataFrame
    validation: pd.DataFrame
    metadata: dict[str, object]


def validate_development_dates(dataset: pd.DataFrame) -> None:
    """Fail closed when a development dataset includes observed holdout dates or labels."""
    if dataset.empty:
        raise ValueError("Walk-forward development dataset is empty.")
    prediction_dates = pd.to_datetime(dataset["prediction_date"], errors="coerce")
    label_end_dates = pd.to_datetime(dataset["label_end_date"], errors="coerce")
    if prediction_dates.isna().any() or (prediction_dates >= DEVELOPMENT_CUTOFF).any():
        raise ValueError("Development walk-forward refuses prediction dates on/after 2024-01-01 (legacy observed test).")
    if (label_end_dates.dropna() >= DEVELOPMENT_CUTOFF).any():
        raise ValueError("Development walk-forward refuses labels realized on/after 2024-01-01.")


def split_walk_forward(
    dataset: pd.DataFrame, feature_columns: list[str], calendar: AShareTradingCalendar,
    *, embargo_sessions: int = 5,
) -> list[WalkForwardFold]:
    if embargo_sessions != 5:
        raise ValueError("The research protocol fixes embargo_sessions=5.")
    required = {"symbol", "prediction_date", "label_end_date", "target_5d", *feature_columns}
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(f"Walk-forward dataset is missing fields: {', '.join(sorted(missing))}.")
    frame = dataset.copy()
    frame["prediction_date"] = pd.to_datetime(frame["prediction_date"], errors="coerce").dt.normalize()
    frame["label_end_date"] = pd.to_datetime(frame["label_end_date"], errors="coerce").dt.normalize()
    if frame.duplicated(["symbol", "prediction_date"]).any():
        raise ValueError("Walk-forward dataset has duplicate symbol/prediction_date keys.")
    validate_development_dates(frame)
    usable = frame.loc[
        frame["target_5d"].notna() & frame["label_end_date"].notna()
        & frame[feature_columns].notna().all(axis=1)
        & frame[feature_columns].apply(pd.to_numeric, errors="coerce").replace([float("inf"), -float("inf")], pd.NA).notna().all(axis=1)
    ].copy()
    folds: list[WalkForwardFold] = []
    for fold_id, train_start_s, train_end_s, val_start_s, val_end_s in WALK_FORWARD_DEFINITIONS:
        train_start, train_end = pd.Timestamp(train_start_s), pd.Timestamp(train_end_s)
        val_start, val_end = pd.Timestamp(val_start_s), pd.Timestamp(val_end_s)
        next_start = val_end + pd.Timedelta(days=1)
        train_cutoff = calendar.label_cutoff(val_start, embargo_sessions)
        validation_cutoff = calendar.label_cutoff(next_start, embargo_sessions)
        train_candidates = usable.loc[usable["prediction_date"].between(train_start, train_end)].copy()
        validation_candidates = usable.loc[usable["prediction_date"].between(val_start, val_end)].copy()
        train = train_candidates.loc[train_candidates["label_end_date"] <= train_cutoff].copy()
        validation = validation_candidates.loc[validation_candidates["label_end_date"] <= validation_cutoff].copy()
        train = train.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
        validation = validation.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
        train_keys = set(zip(train["symbol"], train["prediction_date"]))
        validation_keys = set(zip(validation["symbol"], validation["prediction_date"]))
        boundary_checks = {
            "train_label_purged_before_validation": bool(train.empty or train["label_end_date"].max() < val_start),
            "validation_labels_purged_before_next_period": bool(validation.empty or validation["label_end_date"].max() < next_start),
            "no_cross_split_sample_keys": not bool(train_keys & validation_keys),
            "dates_before_legacy_test": bool(
                (train["prediction_date"] < DEVELOPMENT_CUTOFF).all()
                and (validation["prediction_date"] < DEVELOPMENT_CUTOFF).all()
                and (train["label_end_date"] < DEVELOPMENT_CUTOFF).all()
                and (validation["label_end_date"] < DEVELOPMENT_CUTOFF).all()
            ),
        }
        if not all(boundary_checks.values()):
            raise ValueError(f"{fold_id} violates the development split boundary or label embargo.")
        metadata = {
            "fold_id": fold_id, "evaluation_role": "development_walk_forward",
            "train_configured_start": train_start_s, "train_configured_end": train_end_s,
            "validation_configured_start": val_start_s, "validation_configured_end": val_end_s,
            "train_prediction_start": train["prediction_date"].min().date().isoformat() if len(train) else None,
            "train_prediction_end": train["prediction_date"].max().date().isoformat() if len(train) else None,
            "validation_prediction_start": validation["prediction_date"].min().date().isoformat() if len(validation) else None,
            "validation_prediction_end": validation["prediction_date"].max().date().isoformat() if len(validation) else None,
            "train_rows": len(train), "validation_rows": len(validation),
            "train_symbols": int(train["symbol"].nunique()), "validation_symbols": int(validation["symbol"].nunique()),
            "train_dates": int(train["prediction_date"].nunique()),
            "validation_dates": int(validation["prediction_date"].nunique()),
            "train_candidate_rows": len(train_candidates), "validation_candidate_rows": len(validation_candidates),
            "purged_train_rows": len(train_candidates) - len(train),
            "purged_validation_rows": len(validation_candidates) - len(validation),
            "train_label_cutoff": train_cutoff.date().isoformat(),
            "validation_label_cutoff": validation_cutoff.date().isoformat(),
            "embargo_sessions": embargo_sessions, "boundary_checks": boundary_checks,
        }
        folds.append(WalkForwardFold(fold_id, train, validation, metadata))
    return folds
