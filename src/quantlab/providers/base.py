"""Provider-independent contract for raw and normalized daily bars."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quantlab.providers.capabilities import ProviderCapabilities


@dataclass(frozen=True)
class ProviderBatch:
    raw_frame: pd.DataFrame
    standardized_bars: pd.DataFrame
    provider_version: str
    source_interface: str


class MarketDataProvider(ABC):
    """Fetch raw rows and map one security into the canonical market schema."""

    name: str
    interface: str

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            daily_bars=True, raw_prices=True, markets=(), adjustments=(),
        )

    @abstractmethod
    def fetch_raw_daily_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "none",
    ) -> pd.DataFrame:
        """Return untouched provider fields for archival before validation."""
        raise NotImplementedError

    @abstractmethod
    def normalize_raw_daily_bars(
        self, raw: pd.DataFrame, symbol: str, adjustment: str, *, strict: bool = False
    ) -> pd.DataFrame:
        """Map upstream field names and units into the canonical schema."""
        raise NotImplementedError

    def fetch_batch(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "none",
    ) -> ProviderBatch:
        raw = self.fetch_raw_daily_bars(symbol, start, end, adjustment=adjustment)
        normalized = self.normalize_raw_daily_bars(raw, symbol, adjustment, strict=False)
        return ProviderBatch(raw, normalized, self.version, self.interface)

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        *,
        adjustment: str = "none",
    ) -> pd.DataFrame:
        from quantlab.data.validation import validate_daily_bars

        batch = self.fetch_batch(symbol, start, end, adjustment=adjustment)
        result = validate_daily_bars(
            batch.standardized_bars,
            expected_symbol=symbol if symbol.endswith((".SH", ".SZ")) else None,
            expected_adjustment=adjustment,
        )
        if not result.is_valid:
            first = result.issues.iloc[0]
            raise ValueError(f"Provider data failed validation: {first['detail']}")
        return result.accepted

    def normalize_provider_metadata(self, raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Return provider-specific row metadata separately from canonical OHLCV."""
        return pd.DataFrame(columns=["trade_date", "metadata_json"])

    def fetch_adjustment_factors(
        self, symbol: str, start: date | None = None, end: date | None = None,
    ) -> pd.DataFrame:
        raise NotImplementedError(f"{self.name} does not provide adjustment factors.")

    @property
    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

