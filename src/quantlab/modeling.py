"""Chronological model fitting and diagnostic metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantlab.features import FEATURE_COLUMNS, make_feature_frame


@dataclass
class ResearchResult:
    metrics: dict[str, float | int | str]
    holdout_predictions: pd.DataFrame
    next_session_probability_up: float


def _estimator() -> object:
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced"))


def train_and_evaluate(prices: pd.DataFrame, ticker: str, test_fraction: float = 0.2) -> ResearchResult:
    """Evaluate with a chronological holdout, then refit using all labeled rows."""
    if not 0.05 <= test_fraction <= 0.4:
        raise ValueError("test_fraction must be between 0.05 and 0.4.")

    frame = make_feature_frame(prices)
    labeled = frame.dropna(subset=FEATURE_COLUMNS + ["target_up", "next_return", "label_end_date"])
    if len(labeled) < 100:
        raise ValueError(f"Only {len(labeled)} usable rows; at least 100 are required.")
    split_at = int(len(labeled) * (1 - test_fraction))
    train_candidate = labeled.iloc[:split_at]
    holdout = labeled.iloc[split_at:]
    # Purge any training label whose realization date reaches the holdout period.
    train = train_candidate.loc[pd.to_datetime(train_candidate["label_end_date"]) < holdout.index[0]]
    if train.empty:
        raise ValueError("No training rows remain after purging labels that overlap the holdout period.")
    if train["target_up"].nunique() < 2 or holdout["target_up"].nunique() < 2:
        raise ValueError("The chronological train and holdout sets must each contain both classes.")

    evaluation_model = _estimator()
    evaluation_model.fit(train[FEATURE_COLUMNS], train["target_up"].astype(int))
    probability = evaluation_model.predict_proba(holdout[FEATURE_COLUMNS])[:, 1]
    prediction = (probability >= 0.5).astype(int)
    actual = holdout["target_up"].astype(int).to_numpy()
    realized = holdout["next_return"].to_numpy(dtype=float)
    strategy_returns = np.where(prediction == 1, realized, -realized)
    metrics: dict[str, float | int | str] = {
        "ticker": ticker,
        "rows": int(len(labeled)),
        "train_rows": int(len(train)),
        "purged_boundary_rows": int(len(train_candidate) - len(train)),
        "holdout_rows": int(len(holdout)),
        "holdout_start": holdout.index[0].date().isoformat(),
        "holdout_end": holdout.index[-1].date().isoformat(),
        "train_feature_end": train.index[-1].date().isoformat(),
        "train_label_end": pd.Timestamp(train["label_end_date"].max()).date().isoformat(),
        "accuracy": float(accuracy_score(actual, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(actual, prediction)),
        "brier_score": float(brier_score_loss(actual, probability)),
        "mean_directional_return_before_costs": float(np.mean(strategy_returns)),
    }
    holdout_predictions = pd.DataFrame(
        {
            "actual_up": actual,
            "probability_up": probability,
            "predicted_up": prediction,
            "next_return": realized,
            "label_end_date": holdout["label_end_date"].to_numpy(),
        },
        index=holdout.index,
    )
    holdout_predictions.index.name = "Date"

    final_model = _estimator()
    final_model.fit(labeled[FEATURE_COLUMNS], labeled["target_up"].astype(int))
    latest_features = frame.dropna(subset=FEATURE_COLUMNS).iloc[[-1]][FEATURE_COLUMNS]
    next_probability = float(final_model.predict_proba(latest_features)[0, 1])
    return ResearchResult(metrics, holdout_predictions, next_probability)

