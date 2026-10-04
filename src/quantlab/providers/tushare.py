"""Tushare Pro adapter for unadjusted A-share daily prices and factors."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import time
from datetime import date, datetime
from typing import Callable

import pandas as pd
import requests

from quantlab.data.schema import normalize_daily_bars
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.config import PROJECT_ROOT as _PROJECT_ROOT  # loads the optional local .env without exposing values
from quantlab.providers.base import MarketDataProvider
from quantlab.providers.capabilities import ProviderCapabilities


class TushareProvider(MarketDataProvider):
    name = "tushare"
    interface = "daily"
    factor_interface = "adj_factor"
    fields = "ts_code,trade_date,open,high,low,close,vol,amount"

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 20.0,
        retries: int = 3,
        retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
        sdk=None,
        pro_factory=None,
    ) -> None:
        if timeout <= 0 or retries < 1:
            raise ValueError("timeout must be positive and retries must be at least one.")
        self._token = token if token is not None else os.getenv("TUSHARE_TOKEN", "")
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = max(0.0, retry_delay)
        self.max_retry_delay = max(self.retry_delay, max_retry_delay)
        self.sleeper = sleeper
        self._sdk = sdk
        self._pro_factory = pro_factory

    @property
    def version(self) -> str:
        try:
            return importlib.metadata.version("tushare")
        except importlib.metadata.PackageNotFoundError:
            return "not-installed"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            daily_bars=True,
            raw_prices=True,
            markets=("CN_A",),
            adjustments=("none",),
            adjustment_factors=True,
            requires_credential=True,
        )

    @property
    def availability_reason(self) -> str | None:
        if not self._token:
            return "missing credential"
        if self._sdk is None and self._pro_factory is None and importlib.util.find_spec("tushare") is None:
            return "optional SDK not installed"
        return None

    @property
    def available(self) -> bool:
        return self.availability_reason is None

    def _client(self):
        if not self._token:
            raise ProviderUnavailableError("unavailable: missing credential")
        if self._pro_factory is not None:
            return self._pro_factory(self._token, timeout=self.timeout)
        if self._sdk is not None:
            sdk = self._sdk
        else:
            if importlib.util.find_spec("tushare") is None:
                raise ProviderUnavailableError("unavailable: optional SDK not installed")
            import tushare as sdk
        return sdk.pro_api(self._token, timeout=self.timeout)

    def _request(self, method: str, **kwargs) -> pd.DataFrame:
        try:
            client = self._client()
        except (ProviderUnavailableError, ProviderPermissionError):
            raise
        except Exception as exc:
            raise TushareRequestError(f"Tushare client initialization failed ({type(exc).__name__}).") from None
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                result = getattr(client, method)(**kwargs)
                if result is None:
                    raise TushareRequestError(f"{method} returned no response.")
                return result.copy(deep=True)
            except TushareRequestError:
                raise
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "2002" in message or "权限" in message or "积分" in message or "permission" in message:
                    raise ProviderPermissionError(f"Tushare {method} permission denied or insufficient points.") from None
                if attempt >= self.retries or not _retryable(exc):
                    break
                delay = min(self.retry_delay * (2 ** (attempt - 1)), self.max_retry_delay)
                retry_after = _retry_after(exc)
                if retry_after is not None:
                    delay = min(max(delay, retry_after), self.max_retry_delay)
                self.sleeper(delay)
        assert last_error is not None
        raise TushareRequestError(
            f"Tushare {method} failed after {self.retries} attempts ({type(last_error).__name__})."
        ) from None

    def fetch_raw_daily_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "none",
    ) -> pd.DataFrame:
        identity = normalize_ashare_symbol(symbol)
        if adjustment != "none":
            raise ValueError("Tushare daily supports unadjusted prices only; use adj_factor separately.")
        params: dict[str, object] = {"ts_code": identity.symbol, "fields": self.fields}
        if start is not None:
            params["start_date"] = _date_text(start)
        if end is not None:
            params["end_date"] = _date_text(end)
        frame = self._request("daily", **params)
        if frame.empty:
            raise TushareRequestError(f"Tushare daily returned no rows for {identity.symbol}.")
        return frame

    def normalize_raw_daily_bars(
        self, raw: pd.DataFrame, symbol: str, adjustment: str, *, strict: bool = False
    ) -> pd.DataFrame:
        identity = normalize_ashare_symbol(symbol)
        mapped = raw.rename(columns={"trade_date": "trade_date", "vol": "volume"}).copy()
        mapped["volume"] = pd.to_numeric(mapped["volume"], errors="coerce") * 100.0
        mapped["amount"] = pd.to_numeric(mapped["amount"], errors="coerce") * 1000.0
        return normalize_daily_bars(
            mapped,
            market="CN_A",
            exchange=identity.exchange,
            symbol=identity.symbol,
            adjustment="none",
            currency="CNY",
            source=self.name,
            provider_version=self.version,
            source_interface=self.interface,
            strict=strict,
        )

    def fetch_adjustment_factors(
        self, symbol: str, start: date | None = None, end: date | None = None,
    ) -> pd.DataFrame:
        identity = normalize_ashare_symbol(symbol)
        params: dict[str, object] = {"ts_code": identity.symbol}
        if start is not None:
            params["start_date"] = _date_text(start)
        if end is not None:
            params["end_date"] = _date_text(end)
        raw = self._request(self.factor_interface, **params)
        if raw.empty:
            raise TushareRequestError(f"Tushare adj_factor returned no rows for {identity.symbol}.")
        required = {"trade_date", "adj_factor"}
        if not required.issubset(raw.columns):
            raise TushareRequestError("Tushare adj_factor response has an unexpected schema.")
        downloaded_at = pd.Timestamp.now(tz="UTC")
        factors = pd.DataFrame({
            "market": "CN_A",
            "exchange": identity.exchange,
            "symbol": identity.symbol,
            "trade_date": pd.to_datetime(raw["trade_date"], format="%Y%m%d", errors="raise").dt.date,
            "provider": self.name,
            "adjustment_factor": pd.to_numeric(raw["adj_factor"], errors="raise"),
            "downloaded_at": downloaded_at,
            "first_seen_at": downloaded_at,
            "recorded_at": downloaded_at,
            "ingest_id": None,
            "provider_version": self.version,
        })
        if factors["adjustment_factor"].isna().any() or (factors["adjustment_factor"] <= 0).any():
            raise TushareRequestError("Tushare adj_factor contains invalid values.")
        return factors


class TushareRequestError(RuntimeError):
    """A sanitized Tushare request failed or returned an unexpected response."""


class ProviderUnavailableError(RuntimeError):
    """Provider is installed but cannot be used in the current configuration."""


class ProviderPermissionError(RuntimeError):
    """Provider credentials exist but current account entitlements denied access."""


def _date_text(value: date | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%Y%m%d") if isinstance(value, date) else pd.Timestamp(value).strftime("%Y%m%d")


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status == 429 or (isinstance(status, int) and 500 <= status < 600)


def _retry_after(exc: Exception) -> float | None:
    from quantlab.providers.akshare import _retry_after_seconds

    return _retry_after_seconds(exc)
