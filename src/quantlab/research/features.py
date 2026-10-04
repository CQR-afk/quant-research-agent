"""Causal per-security daily features for A-share bars."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

FEATURE_VERSION = "ashare_daily_v1"
FEATURE_COLUMNS = [
    "Return_1d", "Return_5d", "Return_10d", "Return_20d",
    "MA_5", "MA_10", "MA_20", "MA_60", "Price_to_MA20",
    "RSI_14", "MACD", "MACD_Signal",
    "Volatility_5d", "Volatility_20d",
    "Volume_MA5", "Volume_MA20", "Volume_Ratio",
]
FEATURE_PARAMETERS = {
    "returns_periods": [1, 5, 10, 20],
    "moving_average_windows": [5, 10, 20, 60],
    "rsi_period": 14,
    "macd_ema_spans": [12, 26, 9],
    "volatility_windows": [5, 20],
    "volume_average_windows": [5, 20],
    "rolling_min_periods": "window length",
    "price_field": "close; one explicit adjustment mode per build",
    "volume_field": "volume in shares",
}

FEATURE_V1_SCALE_INVARIANT = [
    "Return_1d", "Return_5d", "Return_10d", "Return_20d", "Price_to_MA20", "RSI_14",
    "Volatility_5d", "Volatility_20d", "Volume_Ratio",
]
FEATURE_V1_SCALE_DEPENDENT = [
    "MA_5", "MA_10", "MA_20", "MA_60", "MACD", "MACD_Signal", "Volume_MA5", "Volume_MA20",
]

FEATURE_V2_VERSION = "ashare_daily_v2_scale_invariant"
FEATURE_V2_COLUMNS = [
    "Return_1d", "Return_5d", "Return_10d", "Return_20d",
    "Close_to_MA5", "Close_to_MA10", "Close_to_MA20", "Close_to_MA60",
    "RSI_14", "MACD_to_Close", "MACD_Signal_to_Close",
    "Volatility_5d", "Volatility_20d", "Volume_to_MA5", "Volume_to_MA20",
    "Volume_Change_1d",
]
FEATURE_V2_PARAMETERS = {
    **FEATURE_PARAMETERS,
    "version_note": "Every model feature is a ratio, return, oscillator, or normalized volatility; raw price and volume levels are excluded.",
    "close_to_ma": "close / rolling_mean(close, window) - 1; zero denominator -> NaN",
    "macd_scale": "MACD / close; zero close -> NaN",
    "volume_to_ma": "volume / rolling_mean(volume, window); zero denominator -> NaN",
    "volume_change": "volume / previous_volume - 1; zero previous volume -> NaN",
    "zero_volume": "Valid zero numerator is retained where denominator is positive; zero rolling denominator -> NaN",
    "missing_values": "NaN retained; no forward fill, backward fill, or cross-security rolling",
}


class FeatureBuilder:
    version = FEATURE_VERSION
    columns = FEATURE_COLUMNS
    parameters = FEATURE_PARAMETERS

    def build(self, bars: pd.DataFrame) -> pd.DataFrame:
        required = {"symbol", "trade_date", "close", "volume", "adjustment"}
        missing = required.difference(bars.columns)
        if missing:
            raise ValueError(f"Bars missing required feature inputs: {', '.join(sorted(missing))}.")
        if bars["adjustment"].nunique(dropna=False) != 1:
            raise ValueError("Feature inputs must use exactly one adjustment mode.")
        if bars.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("Feature inputs contain duplicate symbol/trade_date rows.")

        frames: list[pd.DataFrame] = []
        for symbol, group in bars.groupby("symbol", sort=False):
            ordered = group.sort_values("trade_date", kind="stable").copy()
            close = pd.to_numeric(ordered["close"], errors="coerce").astype(float)
            volume = pd.to_numeric(ordered["volume"], errors="coerce").astype(float)
            returns_1d = close.pct_change(fill_method=None)
            frame = pd.DataFrame(index=ordered.index)
            for period in (1, 5, 10, 20):
                frame[f"Return_{period}d"] = close.pct_change(period, fill_method=None)
            for window in (5, 10, 20, 60):
                frame[f"MA_{window}"] = close.rolling(window, min_periods=window).mean()
            frame["Price_to_MA20"] = close.div(frame["MA_20"].where(frame["MA_20"] != 0)).sub(1)

            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            relative_strength = gain.div(loss.where(loss != 0))
            rsi = 100 - (100 / (1 + relative_strength))
            rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss > 0), 0)
            frame["RSI_14"] = rsi
            ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
            ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
            frame["MACD"] = ema_12 - ema_26
            frame["MACD_Signal"] = frame["MACD"].ewm(span=9, adjust=False, min_periods=9).mean()
            for window in (5, 20):
                frame[f"Volatility_{window}d"] = returns_1d.rolling(window, min_periods=window).std()
            frame["Volume_MA5"] = volume.rolling(5, min_periods=5).mean()
            frame["Volume_MA20"] = volume.rolling(20, min_periods=20).mean()
            frame["Volume_Ratio"] = volume.div(frame["Volume_MA20"].where(frame["Volume_MA20"] != 0))
            frame["symbol"] = symbol
            frame["prediction_date"] = pd.to_datetime(ordered["trade_date"]).values
            frames.append(frame.reset_index(drop=True))

        if not frames:
            return pd.DataFrame(columns=["symbol", "prediction_date", *FEATURE_COLUMNS])
        output = pd.concat(frames, ignore_index=True)
        output[FEATURE_COLUMNS] = output[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
        output["feature_version"] = FEATURE_VERSION
        output["feature_parameters"] = json.dumps(FEATURE_PARAMETERS, sort_keys=True)
        return output.sort_values(["symbol", "prediction_date"], kind="stable").reset_index(drop=True)


class ScaleInvariantFeatureBuilder:
    """Scale-normalized features; each rolling calculation stays within a symbol."""

    version = FEATURE_V2_VERSION
    columns = FEATURE_V2_COLUMNS
    parameters = FEATURE_V2_PARAMETERS

    def build(self, bars: pd.DataFrame) -> pd.DataFrame:
        required = {"symbol", "trade_date", "close", "volume", "adjustment"}
        missing = required.difference(bars.columns)
        if missing:
            raise ValueError(f"Bars missing required feature inputs: {', '.join(sorted(missing))}.")
        if bars["adjustment"].nunique(dropna=False) != 1:
            raise ValueError("Feature inputs must use exactly one adjustment mode.")
        if bars.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("Feature inputs contain duplicate symbol/trade_date rows.")

        frames: list[pd.DataFrame] = []
        for symbol, group in bars.groupby("symbol", sort=False):
            ordered = group.sort_values("trade_date", kind="stable")
            close = pd.to_numeric(ordered["close"], errors="coerce").astype(float)
            volume = pd.to_numeric(ordered["volume"], errors="coerce").astype(float)
            returns_1d = close.pct_change(fill_method=None)
            frame = pd.DataFrame(index=ordered.index)
            for period in (1, 5, 10, 20):
                frame[f"Return_{period}d"] = close.pct_change(period, fill_method=None)
            for window in (5, 10, 20, 60):
                moving = close.rolling(window, min_periods=window).mean()
                frame[f"Close_to_MA{window}"] = close.div(moving.where(moving != 0)).sub(1)

            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            relative_strength = gain.div(loss.where(loss != 0))
            rsi = 100 - (100 / (1 + relative_strength))
            frame["RSI_14"] = rsi.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss > 0), 0)
            ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
            ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
            macd = ema_12 - ema_26
            signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
            frame["MACD_to_Close"] = macd.div(close.where(close != 0))
            frame["MACD_Signal_to_Close"] = signal.div(close.where(close != 0))
            for window in (5, 20):
                frame[f"Volatility_{window}d"] = returns_1d.rolling(window, min_periods=window).std()
            for window in (5, 20):
                volume_mean = volume.rolling(window, min_periods=window).mean()
                frame[f"Volume_to_MA{window}"] = volume.div(volume_mean.where(volume_mean != 0))
            frame["Volume_Change_1d"] = volume.div(volume.shift(1).where(volume.shift(1) != 0)).sub(1)
            frame["symbol"] = symbol
            frame["prediction_date"] = pd.to_datetime(ordered["trade_date"]).values
            frames.append(frame.reset_index(drop=True))

        if not frames:
            return pd.DataFrame(columns=["symbol", "prediction_date", *FEATURE_V2_COLUMNS])
        output = pd.concat(frames, ignore_index=True)
        output[FEATURE_V2_COLUMNS] = output[FEATURE_V2_COLUMNS].replace([np.inf, -np.inf], np.nan)
        output["feature_version"] = FEATURE_V2_VERSION
        output["feature_parameters"] = json.dumps(FEATURE_V2_PARAMETERS, sort_keys=True)
        return output.sort_values(["symbol", "prediction_date"], kind="stable").reset_index(drop=True)


def feature_spec(feature_version: str) -> tuple[list[str], dict[str, object], type[FeatureBuilder] | type[ScaleInvariantFeatureBuilder]]:
    if feature_version == FEATURE_VERSION:
        return FEATURE_COLUMNS, FEATURE_PARAMETERS, FeatureBuilder
    if feature_version == FEATURE_V2_VERSION:
        return FEATURE_V2_COLUMNS, FEATURE_V2_PARAMETERS, ScaleInvariantFeatureBuilder
    raise ValueError(f"Unsupported feature version: {feature_version!r}.")
