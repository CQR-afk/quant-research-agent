"""Row-level quality checks that retain rejected records and their issue codes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from quantlab.data.schema import DAILY_BAR_COLUMNS
from quantlab.data.symbols import normalize_ashare_symbol


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    accepted: pd.DataFrame
    rejected: pd.DataFrame
    issues: pd.DataFrame
    suspension_status: str


def validate_daily_bars(
    bars: pd.DataFrame,
    *,
    expected_market: str | None = None,
    expected_exchange: str | None = None,
    expected_symbol: str | None = None,
    expected_adjustment: str | None = None,
    expected_trade_dates: Iterable[pd.Timestamp] | None = None,
    require_amount: bool = False,
) -> ValidationResult:
    """Validate standardized bars without silently sorting or dropping bad rows."""
    missing_columns = set(DAILY_BAR_COLUMNS).difference(bars.columns)
    required_quality = {"trade_date", "open", "high", "low", "close", "volume"}
    if require_amount:
        required_quality.add("amount")
    row_errors: dict[int, list[str]] = {index: [] for index in range(len(bars))}
    issues: list[dict[str, object]] = []

    def add(code: str, detail: str, row_index: int | None = None) -> None:
        issues.append({"row_index": row_index, "issue_type": code, "detail": detail})
        if row_index is not None and row_index in row_errors and code not in row_errors[row_index]:
            row_errors[row_index].append(code)

    if bars.empty:
        add("empty_dataset", "Provider returned no daily records.")
    for column in sorted(missing_columns):
        add("missing_field", f"Canonical field is missing: {column}.")
    for column in sorted(set(bars.attrs.get("missing_input_columns", ())) & required_quality):
        add("missing_field", f"Provider response did not contain required field: {column}.")

    if {"trade_date", "symbol"}.issubset(bars.columns):
        duplicate_mask = bars.duplicated(["symbol", "trade_date"], keep=False)
        for index in np.flatnonzero(duplicate_mask.to_numpy()):
            add("duplicate_date", "Duplicate trade date for this symbol and dataset version.", int(index))

    if "trade_date" in bars:
        dates = pd.to_datetime(bars["trade_date"], errors="coerce")
        invalid_dates = dates.isna()
        for index in np.flatnonzero(invalid_dates.to_numpy()):
            add("invalid_trade_date", "Trade date could not be parsed.", int(index))
        valid_dates = dates.dropna()
        for position in range(1, len(valid_dates)):
            if valid_dates.iloc[position] < valid_dates.iloc[position - 1]:
                row_index = int(valid_dates.index[position])
                add("out_of_order_date", "Trade dates must arrive in ascending order.", row_index)
        weekend = dates.notna() & (dates.dt.dayofweek >= 5)
        for index in np.flatnonzero(weekend.to_numpy()):
            add("anomalous_trade_date", "Weekend records are not valid A-share sessions.", int(index))

    if require_amount and "amount" in bars:
        if bars["amount"].isna().any():
            for index in np.flatnonzero(bars["amount"].isna().to_numpy()):
                add("missing_field", "A-share amount is required and must not be synthesized.", int(index))

    for column in ("open", "high", "low", "close"):
        if column not in bars:
            continue
        values = pd.to_numeric(bars[column], errors="coerce")
        negative = values < 0
        nonpositive = values.notna() & (values == 0)
        invalid = values.isna() | ~np.isfinite(values.fillna(0))
        for index in np.flatnonzero(negative.to_numpy()):
            add("negative_price", f"{column} is negative.", int(index))
        for index in np.flatnonzero(nonpositive.to_numpy()):
            add("invalid_price", f"{column} must be greater than zero.", int(index))
        for index in np.flatnonzero(invalid.to_numpy()):
            add("missing_or_nonfinite_price", f"{column} is missing or non-finite.", int(index))

    ohlc_columns = {"open", "high", "low", "close"}
    if ohlc_columns.issubset(bars.columns):
        ohlc = bars.loc[:, ["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        inconsistent = (
            (ohlc["high"] < ohlc[["open", "close"]].max(axis=1))
            | (ohlc["low"] > ohlc[["open", "close"]].min(axis=1))
            | (ohlc["high"] < ohlc["low"])
        )
        for index in np.flatnonzero(inconsistent.fillna(False).to_numpy()):
            add("invalid_ohlc", "OHLC high/low values do not enclose open and close.", int(index))

    if "volume" in bars:
        volume = pd.to_numeric(bars["volume"], errors="coerce")
        for index in np.flatnonzero((volume < 0).fillna(False).to_numpy()):
            add("negative_volume", "Volume is negative.", int(index))
        bad_volume = volume.isna() | ~np.isfinite(volume.fillna(0))
        for index in np.flatnonzero(bad_volume.to_numpy()):
            add("missing_field", "Volume is missing, non-numeric, or non-finite.", int(index))

    if "amount" in bars:
        amount = pd.to_numeric(bars["amount"], errors="coerce")
        for index in np.flatnonzero((amount < 0).fillna(False).to_numpy()):
            add("negative_amount", "Amount is negative.", int(index))
        bad_amount = amount.notna() & ~np.isfinite(amount.fillna(0))
        for index in np.flatnonzero(bad_amount.to_numpy()):
            add("invalid_amount", "Amount is non-finite.", int(index))

    if "adjustment" in bars:
        if bars["adjustment"].nunique(dropna=False) > 1:
            for index in range(len(bars)):
                add("mixed_adjustment", "A dataset must contain exactly one adjustment mode.", index)
        if expected_adjustment is not None:
            wrong = bars["adjustment"].astype(str) != expected_adjustment
            for index in np.flatnonzero(wrong.to_numpy()):
                add("adjustment_mismatch", f"Expected {expected_adjustment}, received {bars['adjustment'].iloc[index]}.", int(index))

    if "symbol" in bars:
        if expected_symbol is not None:
            try:
                canonical_expected = normalize_ashare_symbol(expected_symbol).symbol
            except ValueError:
                canonical_expected = expected_symbol
        else:
            canonical_expected = None
        for index, symbol in enumerate(bars["symbol"]):
            market = str(bars["market"].iloc[index]) if "market" in bars else "CN_A"
            try:
                canonical = normalize_ashare_symbol(str(symbol)).symbol
            except ValueError as exc:
                if market in {"US", "HK"}:
                    continue
                add("invalid_symbol", str(exc), index)
                continue
            if canonical != symbol and not (str(symbol).isdigit() and len(str(symbol)) == 6):
                add("noncanonical_symbol", f"Symbol must be stored as {canonical}.", index)
            if canonical_expected is not None and canonical != canonical_expected:
                add("symbol_mismatch", f"Expected {canonical_expected}, received {canonical}.", index)
            if "market" in bars and bars["market"].iloc[index] != "CN_A":
                add("wrong_market_mapping", "SSE/SZSE symbol must use market CN_A.", index)
            if "exchange" in bars:
                expected_code = normalize_ashare_symbol(canonical).exchange
                if bars["exchange"].iloc[index] != expected_code:
                    add("wrong_exchange_mapping", f"{canonical} maps to {expected_code}.", index)

    for name, expected in (("market", expected_market), ("exchange", expected_exchange)):
        if expected is not None and name in bars:
            for index in np.flatnonzero((bars[name].astype(str) != expected).to_numpy()):
                add("wrong_market_mapping", f"Expected {name}={expected}.", int(index))

    suspension_status = "Unknown"
    if expected_trade_dates is not None and "trade_date" in bars:
        actual = set(pd.to_datetime(bars["trade_date"], errors="coerce").dropna().dt.normalize())
        expected_dates = {pd.Timestamp(value).normalize() for value in expected_trade_dates}
        missing_dates = sorted(expected_dates.difference(actual))
        if missing_dates:
            add(
                "missing_expected_session_unknown",
                f"{len(missing_dates)} expected sessions have no record; halt versus source gap is Unknown.",
            )

    annotated = bars.copy()
    annotated["validation_errors"] = [";".join(row_errors[index]) for index in range(len(annotated))]
    rejected_mask = annotated["validation_errors"] != ""
    rejected = annotated.loc[rejected_mask].copy()
    accepted = annotated.loc[~rejected_mask].drop(columns="validation_errors").copy()
    issue_frame = pd.DataFrame(issues, columns=["row_index", "issue_type", "detail"])
    return ValidationResult(not issues, accepted, rejected, issue_frame, suspension_status)
