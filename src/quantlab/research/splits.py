"""Globally dated train/validation/test splits with label purging and embargo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from quantlab.research.calendar import AShareTradingCalendar


class SplitStrategy(Protocol):
    def split(self, dataset: pd.DataFrame) -> "SplitBundle": ...


@dataclass(frozen=True)
class SplitBundle:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    metadata: dict[str, object]


class FixedPeriodSplitter:
    """One global calendar split; each sample is keyed by symbol and prediction date."""

    def __init__(self, config: dict[str, object], calendar: AShareTradingCalendar) -> None:
        self.config = config
        self.calendar = calendar
        self.split_config = config["splits"]
        self.embargo = int(config.get("embargo_sessions", 0))
        if self.embargo < 0:
            raise ValueError("embargo_sessions cannot be negative.")

    def split(self, dataset: pd.DataFrame) -> SplitBundle:
        required = {"symbol", "prediction_date", "label_end_date", "target_5d"}
        missing = required.difference(dataset.columns)
        if missing:
            raise ValueError(f"Dataset missing split fields: {', '.join(sorted(missing))}.")
        frame = dataset.copy()
        frame["prediction_date"] = pd.to_datetime(frame["prediction_date"]).dt.normalize()
        frame["label_end_date"] = pd.to_datetime(frame["label_end_date"], errors="coerce").dt.normalize()
        if frame.duplicated(["symbol", "prediction_date"]).any():
            raise ValueError("Duplicate symbol/prediction_date samples are not allowed.")
        labeled = frame.loc[frame["target_5d"].notna() & frame["label_end_date"].notna()].copy()
        dates = self.split_config
        train_start, train_end = pd.Timestamp(dates["train_start"]), pd.Timestamp(dates["train_end"])
        validation_start, validation_end = pd.Timestamp(dates["validation_start"]), pd.Timestamp(dates["validation_end"])
        test_start, test_end = pd.Timestamp(dates["test_start"]), pd.Timestamp(dates["test_end"])
        if not (train_start <= train_end < validation_start <= validation_end < test_start <= test_end):
            raise ValueError("Configured Train/Validation/Test date ranges must be strictly chronological.")
        train_cutoff = self.calendar.label_cutoff(validation_start, self.embargo)
        validation_cutoff = self.calendar.label_cutoff(test_start, self.embargo)

        train_candidates = labeled.loc[labeled["prediction_date"].between(train_start, train_end)].copy()
        validation_candidates = labeled.loc[labeled["prediction_date"].between(validation_start, validation_end)].copy()
        test = labeled.loc[labeled["prediction_date"].between(test_start, test_end)].copy()
        train = train_candidates.loc[train_candidates["label_end_date"] <= train_cutoff].copy()
        validation = validation_candidates.loc[validation_candidates["label_end_date"] <= validation_cutoff].copy()
        train = train.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
        validation = validation.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
        test = test.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)

        boundary_checks = {
            "train_labels_before_validation": bool(train.empty or train["label_end_date"].max() < validation_start),
            "validation_labels_before_test": bool(validation.empty or validation["label_end_date"].max() < test_start),
            "sample_keys_unique_across_splits": True,
        }
        keys = [set(zip(part["symbol"], part["prediction_date"])) for part in (train, validation, test)]
        boundary_checks["sample_keys_unique_across_splits"] = not (
            keys[0] & keys[1] or keys[0] & keys[2] or keys[1] & keys[2]
        )
        metadata = {
            "strategy": "fixed_global_calendar_dates",
            "train": _period_summary(train, train_start, train_end),
            "validation": _period_summary(validation, validation_start, validation_end),
            "test": _period_summary(test, test_start, test_end),
            "train_candidate_rows": len(train_candidates),
            "validation_candidate_rows": len(validation_candidates),
            "purged_train_rows": len(train_candidates) - len(train),
            "purged_validation_rows": len(validation_candidates) - len(validation),
            "train_label_cutoff": train_cutoff.date().isoformat(),
            "validation_label_cutoff": validation_cutoff.date().isoformat(),
            "embargo_sessions": self.embargo,
            "boundary_checks": boundary_checks,
        }
        return SplitBundle(train, validation, test, metadata)


def _period_summary(frame: pd.DataFrame, configured_start: pd.Timestamp, configured_end: pd.Timestamp) -> dict[str, object]:
    return {
        "configured_start": configured_start.date().isoformat(),
        "configured_end": configured_end.date().isoformat(),
        "prediction_start": frame["prediction_date"].min().date().isoformat() if len(frame) else None,
        "prediction_end": frame["prediction_date"].max().date().isoformat() if len(frame) else None,
        "label_end_max": frame["label_end_date"].max().date().isoformat() if len(frame) else None,
        "rows": len(frame),
        "symbols": int(frame["symbol"].nunique()) if len(frame) else 0,
        "dates": int(frame["prediction_date"].nunique()) if len(frame) else 0,
    }
