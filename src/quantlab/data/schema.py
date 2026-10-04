"""Canonical daily bar schema and validation shared by market providers."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


DAILY_BAR_COLUMNS = (
    "market",
    "exchange",
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adjustment",
    "currency",
    "source",
    "downloaded_at",
    "provider_version",
    "source_interface",
    "ingest_id",
    "revision_id",
    "record_version",
    "first_seen_at",
    "recorded_at",
    "row_hash",
)
ADJUSTMENT_MODES = {"none", "qfq", "hfq", "vendor_adjusted"}


def normalize_daily_bars(
    bars: pd.DataFrame,
    *,
    market: str,
    exchange: str,
    symbol: str,
    adjustment: str,
    currency: str,
    source: str,
    downloaded_at: pd.Timestamp | None = None,
    provider_version: str = "unknown",
    source_interface: str = "unknown",
    ingest_id: str | None = None,
    strict: bool = True,
) -> pd.DataFrame:
    """Map provider field names to the canonical schema; optionally reject bad rows."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string; pass codes as strings to preserve leading zeros.")
    if adjustment not in ADJUSTMENT_MODES:
        raise ValueError(f"adjustment must be one of {sorted(ADJUSTMENT_MODES)}.")
    for field, value in (("market", market), ("exchange", exchange), ("currency", currency), ("source", source)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")

    required = ("trade_date", "open", "high", "low", "close", "volume")
    missing = set(required).difference(bars.columns)
    if missing and strict:
        raise ValueError(f"Provider data is missing required fields: {', '.join(sorted(missing))}.")

    frame = bars.reindex(columns=list(required) + (["amount"] if "amount" in bars.columns else [])).copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    if isinstance(frame["trade_date"].dtype, pd.DatetimeTZDtype):
        frame["trade_date"] = frame["trade_date"].dt.tz_localize(None)
    frame["trade_date"] = frame["trade_date"].dt.normalize()
    numeric = ("open", "high", "low", "close", "volume", "amount")
    if "amount" not in frame:
        frame["amount"] = np.nan
    for field in numeric:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")

    if frame["trade_date"].isna().any():
        raise ValueError("Provider data contains invalid trade dates.")
    if frame["trade_date"].duplicated().any():
        raise ValueError("Provider data contains duplicate trade dates for one symbol.")
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC prices must be finite and positive.")
    if ((frame["high"] < frame[["open", "close"]].max(axis=1)) | (frame["low"] > frame[["open", "close"]].min(axis=1)) | (frame["high"] < frame["low"])).any():
        raise ValueError("OHLC values are inconsistent.")
    if not np.isfinite(frame["volume"].to_numpy(dtype=float)).all() or (frame["volume"] < 0).any():
        raise ValueError("volume must be finite and non-negative, in shares.")
    valid_amount = frame["amount"].dropna().to_numpy(dtype=float)
    if not np.isfinite(valid_amount).all() or (valid_amount < 0).any():
        raise ValueError("amount must be missing or finite and non-negative, in currency units.")

    frame["market"] = market
    frame["exchange"] = exchange
    frame["symbol"] = symbol.strip()
    frame["adjustment"] = adjustment
    frame["currency"] = currency
    frame["source"] = source
    frame["provider_version"] = provider_version
    frame["source_interface"] = source_interface
    frame["ingest_id"] = ingest_id
    timestamp = pd.Timestamp.now(tz="UTC") if downloaded_at is None else pd.Timestamp(downloaded_at)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    frame["downloaded_at"] = timestamp
    frame["revision_id"] = None
    frame["record_version"] = 1
    frame["first_seen_at"] = timestamp
    frame["recorded_at"] = timestamp
    frame["row_hash"] = None
    result = frame.loc[:, DAILY_BAR_COLUMNS].reset_index(drop=True)
    result.attrs["missing_input_columns"] = sorted(missing)

    if strict:
        from quantlab.data.validation import validate_daily_bars

        validation = validate_daily_bars(result)
        if not validation.is_valid:
            first = validation.issues.iloc[0]
            raise ValueError(str(first["detail"]))
        return result.sort_values("trade_date", kind="stable").reset_index(drop=True)
    return result


def missing_expected_sessions(
    bars: pd.DataFrame, expected_trade_dates: Iterable[pd.Timestamp]
) -> list[pd.Timestamp]:
    """Return absent expected sessions without manufacturing bars for suspensions."""
    actual = set(pd.to_datetime(bars["trade_date"]).dt.normalize())
    expected = {pd.Timestamp(value).normalize() for value in expected_trade_dates}
    return sorted(expected.difference(actual))

