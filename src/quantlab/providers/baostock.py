"""BaoStock adapter for A-share daily bars, including provider row metadata."""

from __future__ import annotations

import contextlib
import importlib.metadata
import importlib.util
import io
import json
import socket
import threading
import time
from datetime import date, datetime
from typing import Callable

import pandas as pd

from quantlab.data.schema import normalize_daily_bars
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.providers.base import MarketDataProvider
from quantlab.providers.capabilities import ProviderCapabilities


_SOCKET_TIMEOUT_LOCK = threading.Lock()
_ADJUSTMENT_FLAG = {"hfq": "1", "qfq": "2", "none": "3"}
_ADJUSTMENT_NAME = {value: key for key, value in _ADJUSTMENT_FLAG.items()}


class BaoStockProvider(MarketDataProvider):
    name = "baostock"
    interface = "query_history_k_data_plus"
    fields = "date,code,open,high,low,close,volume,amount,adjustflag,tradestatus,isST"

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 3,
        retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
        sdk=None,
    ) -> None:
        if timeout <= 0 or retries < 1:
            raise ValueError("timeout must be positive and retries must be at least one.")
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = max(0.0, retry_delay)
        self.max_retry_delay = max(self.retry_delay, max_retry_delay)
        self.sleeper = sleeper
        self._sdk = sdk

    @property
    def version(self) -> str:
        try:
            return importlib.metadata.version("baostock")
        except importlib.metadata.PackageNotFoundError:
            return "not-installed"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            daily_bars=True,
            raw_prices=True,
            markets=("CN_A",),
            adjustments=("none", "qfq", "hfq"),
            suspension_status=True,
        )

    @property
    def available(self) -> bool:
        return self._sdk is not None or importlib.util.find_spec("baostock") is not None

    @property
    def availability_reason(self) -> str | None:
        return None if self.available else "optional SDK not installed"

    def _module(self):
        if self._sdk is not None:
            return self._sdk
        if importlib.util.find_spec("baostock") is None:
            raise BaoStockUnavailableError("unavailable: optional SDK not installed")
        import baostock as bs
        return bs

    def fetch_raw_daily_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "none",
    ) -> pd.DataFrame:
        identity = normalize_ashare_symbol(symbol)
        if adjustment not in _ADJUSTMENT_FLAG:
            raise ValueError("BaoStock adjustment must be one of none, qfq, hfq.")
        code = f"{'sh' if identity.exchange == 'SSE' else 'sz'}.{identity.code}"
        start_text = _date_text(start) if start is not None else "1990-01-01"
        end_text = _date_text(end) if end is not None else date.today().isoformat()
        if start_text > end_text:
            raise ValueError("start date must be on or before end date.")

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                return self._fetch_once(code, identity.symbol, start_text, end_text, adjustment)
            except (BaoStockPermissionError, BaoStockUnavailableError, ValueError):
                raise
            except BaoStockRequestError as exc:
                last_error = exc
                if attempt >= self.retries or not exc.retryable:
                    break
                self.sleeper(min(self.retry_delay * (2 ** (attempt - 1)), self.max_retry_delay))
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries or not _retryable(exc):
                    break
                self.sleeper(min(self.retry_delay * (2 ** (attempt - 1)), self.max_retry_delay))
        assert last_error is not None
        raise BaoStockRequestError(
            f"BaoStock {self.interface} failed for {identity.symbol} after {self.retries} attempts ({type(last_error).__name__})."
        ) from None

    def _fetch_once(self, code: str, symbol: str, start: str, end: str, adjustment: str) -> pd.DataFrame:
        bs = self._module()
        logged_in = False
        with _SOCKET_TIMEOUT_LOCK:
            previous_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(self.timeout)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    login = bs.login()
                if str(login.error_code) != "0":
                    if _permission_error(str(login.error_msg)):
                        raise BaoStockPermissionError("BaoStock login permission denied.")
                    raise BaoStockRequestError(f"BaoStock login failed with code {login.error_code}.",
                                               retryable=_retryable_message(login.error_code, login.error_msg))
                logged_in = True
                with contextlib.redirect_stdout(io.StringIO()):
                    cursor = bs.query_history_k_data_plus(
                        code, self.fields, start_date=start, end_date=end,
                        frequency="d", adjustflag=_ADJUSTMENT_FLAG[adjustment],
                    )
                if str(cursor.error_code) != "0":
                    if _permission_error(str(cursor.error_msg)):
                        raise BaoStockPermissionError("BaoStock history permission denied.")
                    raise BaoStockRequestError(f"BaoStock history query failed with code {cursor.error_code}.",
                                               retryable=_retryable_message(cursor.error_code, cursor.error_msg))
                rows: list[list[str]] = []
                while cursor.next():
                    rows.append(cursor.get_row_data())
                result = pd.DataFrame(rows, columns=cursor.fields)
                if result.empty:
                    raise BaoStockRequestError(f"BaoStock returned no rows for {symbol}.")
                return result
            finally:
                try:
                    if logged_in:
                        with contextlib.redirect_stdout(io.StringIO()):
                            bs.logout()
                finally:
                    socket.setdefaulttimeout(previous_timeout)

    def normalize_raw_daily_bars(
        self, raw: pd.DataFrame, symbol: str, adjustment: str, *, strict: bool = False
    ) -> pd.DataFrame:
        identity = normalize_ashare_symbol(symbol)
        expected_flag = _ADJUSTMENT_FLAG.get(adjustment)
        if expected_flag is None:
            raise ValueError("BaoStock adjustment must be one of none, qfq, hfq.")
        if "adjustflag" in raw and not raw.empty:
            flags = set(raw["adjustflag"].dropna().astype(str).unique())
            if flags and flags != {expected_flag}:
                raise ValueError("BaoStock response adjustment flag differs from the request.")
        mapped = raw.rename(columns={"date": "trade_date"}).copy()
        # BaoStock reports volume in shares and amount in CNY; no unit conversion is applied.
        return normalize_daily_bars(
            mapped,
            market="CN_A",
            exchange=identity.exchange,
            symbol=identity.symbol,
            adjustment=adjustment,
            currency="CNY",
            source=self.name,
            provider_version=self.version,
            source_interface=self.interface,
            strict=strict,
        )

    def normalize_provider_metadata(self, raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if raw.empty or not {"date", "tradestatus", "isST", "adjustflag"}.intersection(raw.columns):
            return pd.DataFrame(columns=["trade_date", "metadata_json"])
        records = []
        for row in raw.to_dict("records"):
            fields = {name: row.get(name) for name in ("tradestatus", "isST", "adjustflag") if name in row}
            records.append({
                "trade_date": pd.Timestamp(row["date"]).date(),
                "metadata_json": json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True),
            })
        return pd.DataFrame(records)


class BaoStockRequestError(RuntimeError):
    """A sanitized BaoStock request failed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class BaoStockPermissionError(BaoStockRequestError):
    """BaoStock denied a request or login."""


class BaoStockUnavailableError(RuntimeError):
    """BaoStock SDK is unavailable in the current environment."""


def _date_text(value: date | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat() if isinstance(value, date) else pd.Timestamp(value).date().isoformat()


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def _permission_error(message: str) -> bool:
    text = message.lower()
    return any(word in text for word in ("permission", "权限", "积分", "unauthorized", "forbidden"))


def _retryable_message(error_code: str, error_message: str) -> bool:
    text = f"{error_code} {error_message}".lower()
    return any(word in text for word in (
        "timeout", "timed out", "network", "connection", "socket", "temporarily unavailable", "remote end closed",
    ))
