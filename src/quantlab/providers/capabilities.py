"""Declarative provider features and availability diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    daily_bars: bool
    raw_prices: bool
    markets: tuple[str, ...]
    adjustments: tuple[str, ...]
    adjustment_factors: bool = False
    trade_calendar: bool = False
    suspension_status: bool = False
    stock_metadata: bool = False
    requires_credential: bool = False

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for field in ("markets", "adjustments"):
            result[field] = list(result[field])
        return result


@dataclass(frozen=True)
class ProviderAvailability:
    name: str
    available: bool
    reason: str | None
    version: str | None
    capabilities: ProviderCapabilities

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "available": self.available,
            "status": "available" if self.available else f"unavailable: {self.reason or 'unknown'}",
            "reason": self.reason,
            "version": self.version,
            "capabilities": self.capabilities.to_dict(),
        }
