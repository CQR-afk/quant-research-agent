"""Yahoo Finance compatibility adapter; no A-share coverage is guaranteed."""

from __future__ import annotations

from datetime import date
from importlib.metadata import version

import pandas as pd
import yfinance as yf

from quantlab.data.schema import normalize_daily_bars
from quantlab.providers.base import MarketDataProvider


class YFinanceProvider(MarketDataProvider):
    name = "yahoo"
    interface = "yfinance.download"

    def __init__(self, period: str = "5y") -> None:
        self.period = period

    @property
    def version(self) -> str:
        return version("yfinance")

    def fetch_raw_daily_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "vendor_adjusted",
    ) -> pd.DataFrame:
        if adjustment not in {"none", "vendor_adjusted"}:
            raise ValueError("Yahoo Finance supports only none or vendor_adjusted in this adapter.")
        raw = yf.download(
            symbol,
            start=start,
            end=end,
            period=self.period if start is None and end is None else None,
            interval="1d",
            auto_adjust=adjustment == "vendor_adjusted",
            progress=False,
            threads=False,
        )
        if raw.empty:
            raise ValueError(f"No price history returned for {symbol!r}.")
        return raw

    def normalize_raw_daily_bars(
        self, raw: pd.DataFrame, symbol: str, adjustment: str, *, strict: bool = False
    ) -> pd.DataFrame:
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame.columns = [str(column).strip().lower() for column in frame.columns]
        if "trade_date" not in frame.columns:
            frame = frame.rename_axis("trade_date").reset_index()
        if "date" in frame.columns and "trade_date" not in frame:
            frame = frame.rename(columns={"date": "trade_date"})
        required = {"trade_date", "open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if strict and missing:
            raise ValueError(f"Price data for {symbol!r} is missing: {', '.join(sorted(missing))}.")

        market, exchange, currency = _market_metadata(symbol)
        result = normalize_daily_bars(
            frame,
            market=market,
            exchange=exchange,
            symbol=symbol,
            adjustment=adjustment,
            currency=currency,
            source=self.name,
            provider_version=self.version,
            source_interface=self.interface,
            strict=strict,
        )
        return result


def _market_metadata(symbol: str) -> tuple[str, str, str]:
    code = symbol.upper()
    if code.endswith((".SS", ".SH")):
        return "CN_A", "SSE", "CNY"
    if code.endswith(".SZ"):
        return "CN_A", "SZSE", "CNY"
    if code.endswith(".HK"):
        return "HK", "HKEX", "HKD"
    return "US", "YAHOO", "USD"
