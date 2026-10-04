"""AKShare adapter for A-share daily history from stock_zh_a_hist."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from importlib.metadata import version
from typing import Callable

import akshare as ak
import pandas as pd
import requests

from quantlab.data.schema import normalize_daily_bars
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.providers.base import MarketDataProvider


AKSHARE_INTERFACE = "stock_zh_a_hist"
AKSHARE_VOLUME_UNIT = "lot"
AKSHARE_SHARES_PER_LOT = 100


class AKShareProvider(MarketDataProvider):
    """Fetch daily A-share bars with bounded retries and a request timeout."""

    name = "akshare"
    interface = AKSHARE_INTERFACE

    def __init__(self, timeout: float = 20.0, retries: int = 3, retry_delay: float = 1.0,
                 max_retry_delay: float = 30.0, sleeper: Callable[[float], None] = time.sleep) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive.")
        if retries < 1:
            raise ValueError("retries must be at least one.")
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = max(0.0, retry_delay)
        self.max_retry_delay = max(self.retry_delay, max_retry_delay)
        self.sleeper = sleeper

    @property
    def version(self) -> str:
        return version("akshare")

    def fetch_raw_daily_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "qfq",
    ) -> pd.DataFrame:
        normalized = normalize_ashare_symbol(symbol)
        if adjustment not in {"none", "qfq", "hfq"}:
            raise ValueError("AKShare adjustment must be one of: none, qfq, hfq.")
        start_text = _format_date(start or date(1990, 1, 1))
        end_text = _format_date(end or date.today())
        if start_text > end_text:
            raise ValueError("start date must be on or before end date.")

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                raw = ak.stock_zh_a_hist(
                    symbol=normalized.code,
                    period="daily",
                    start_date=start_text,
                    end_date=end_text,
                    adjust="" if adjustment == "none" else adjustment,
                    timeout=self.timeout,
                )
                if raw is None or raw.empty:
                    raise AKShareFetchError(
                        f"{self.interface} returned no rows for {normalized.symbol} ({adjustment}) "
                        f"from {start_text} to {end_text}."
                    )
                return raw.copy(deep=True)
            except AKShareFetchError:
                raise
            except Exception as exc:  # Upstream can raise requests, parsing, and schema errors.
                last_error = exc
                if attempt >= self.retries or not _is_retryable_error(exc):
                    break
                retry_after = _retry_after_seconds(exc)
                delay = min(self.retry_delay * (2 ** (attempt - 1)), self.max_retry_delay)
                if retry_after is not None:
                    delay = min(max(delay, retry_after), self.max_retry_delay)
                self.sleeper(delay)

        assert last_error is not None
        raise AKShareFetchError(
            f"{self.interface} failed for {normalized.symbol} ({adjustment}) after "
            f"{self.retries} attempts: {type(last_error).__name__}: {last_error}"
        ) from last_error

    def normalize_raw_daily_bars(
        self, raw: pd.DataFrame, symbol: str, adjustment: str, *, strict: bool = False
    ) -> pd.DataFrame:
        normalized = normalize_ashare_symbol(symbol)
        column_map = {
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        mapped = raw.rename(columns=column_map).copy()
        available = [name for name in ("trade_date", "open", "high", "low", "close", "volume", "amount")
                     if name in mapped.columns]
        mapped = mapped.loc[:, available].copy()
        if "volume" in mapped:
            # AKShare documents stock_zh_a_hist volume in lots (手); the canonical unit is shares.
            mapped["volume"] = pd.to_numeric(mapped["volume"], errors="coerce") * AKSHARE_SHARES_PER_LOT
        return normalize_daily_bars(
            mapped,
            market="CN_A",
            exchange=normalized.exchange,
            symbol=normalized.symbol,
            adjustment=adjustment,
            currency="CNY",
            source=self.name,
            provider_version=self.version,
            source_interface=self.interface,
            strict=strict,
        )


class AKShareFetchError(RuntimeError):
    """A bounded AKShare request failed or returned no records."""


def _format_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    digits = value.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError(f"Invalid date {value!r}; expected YYYYMMDD.")
    try:
        return datetime.strptime(digits, "%Y%m%d").strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected a real calendar date.") from exc


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError, TimeoutError, ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status == 429 or (isinstance(status, int) and 500 <= status < 600)


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
