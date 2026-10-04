"""Leakage-aware daily price features and next-session direction labels."""

from __future__ import annotations

import pandas as pd


FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "return_20d",
    "volatility_20d",
    "close_to_sma_5",
    "close_to_sma_20",
    "close_to_sma_50",
    "volume_change_1d",
    "volume_to_sma_20",
]


def make_forward_target(prices: pd.DataFrame, horizon: int = 5, close_column: str = "Close") -> pd.DataFrame:
    """Create a forward return and its actual label end date for one security."""
    if horizon < 1:
        raise ValueError("horizon must be at least one trading observation.")
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise TypeError("prices must use a DatetimeIndex for chronological labels.")
    if prices.index.has_duplicates:
        raise ValueError("prices must have unique trading dates.")
    if "symbol" in prices and prices["symbol"].nunique(dropna=False) != 1:
        raise ValueError("Make forward labels separately for each symbol.")
    ordered = prices.sort_index()
    close = ordered[close_column].astype(float)
    return pd.DataFrame(
        {
            f"target_{horizon}d": close.shift(-horizon) / close - 1,
            "label_end_date": pd.Series(ordered.index, index=ordered.index).shift(-horizon),
        },
        index=ordered.index,
    )


def make_feature_frame(prices: pd.DataFrame) -> pd.DataFrame:
    """Create features known after today's close and a target for the next close."""
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise TypeError("prices must use a DatetimeIndex for chronological labels.")
    if prices.index.has_duplicates:
        raise ValueError("prices must have unique trading dates.")
    prices = prices.sort_index()
    close = prices["Close"].astype(float)
    volume = prices["Volume"].astype(float)
    daily_return = close.pct_change()

    features = pd.DataFrame(index=prices.index)
    features["return_1d"] = daily_return
    features["return_5d"] = close.pct_change(5)
    features["return_20d"] = close.pct_change(20)
    features["volatility_20d"] = daily_return.rolling(20, min_periods=20).std()
    for window in (5, 20, 50):
        features[f"close_to_sma_{window}"] = close / close.rolling(window, min_periods=window).mean() - 1
    features["volume_change_1d"] = volume.pct_change().replace([float("inf"), -float("inf")], float("nan"))
    volume_average = volume.rolling(20, min_periods=20).mean()
    features["volume_to_sma_20"] = volume / volume_average - 1

    target = make_forward_target(prices, horizon=1)
    next_return = target["target_1d"]
    features["next_return"] = next_return
    features["target_up"] = (next_return > 0).astype("float")
    features.loc[next_return.isna(), "target_up"] = float("nan")
    features["label_end_date"] = target["label_end_date"]
    return features.replace([float("inf"), -float("inf")], float("nan"))

