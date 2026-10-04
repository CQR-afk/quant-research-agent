"""Configuration-driven provider discovery and request routing."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from quantlab.providers.akshare import AKShareProvider
from quantlab.providers.baostock import BaoStockProvider
from quantlab.providers.base import MarketDataProvider
from quantlab.providers.capabilities import ProviderAvailability
from quantlab.providers.tushare import TushareProvider
from quantlab.providers.yfinance import YFinanceProvider


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ProviderCandidate:
    provider: MarketDataProvider
    availability: ProviderAvailability
    interval_seconds: float


class ProviderRouter:
    """Select compatible providers in user-configured order, without merging rows."""

    def __init__(
        self,
        config_path: Path | str | None = None,
        *,
        factories: dict[str, Callable[[], MarketDataProvider]] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config_path = Path(config_path) if config_path else _project_root() / "config" / "data_sources.json"
        self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        default_factories: dict[str, Callable[[], MarketDataProvider]] = {
            "tushare": TushareProvider,
            "baostock": BaoStockProvider,
            "akshare": AKShareProvider,
            "yahoo": YFinanceProvider,
        }
        self.factories = factories or default_factories
        self.sleeper = sleeper
        self._last_request: dict[str, float] = {}

    def configured_order(self, purpose: str = "download") -> list[str]:
        block = self.config.get(purpose, {})
        if purpose == "historical_backfill":
            names = block.get("providers", [])
        else:
            names = block.get("providers", self.config.get("download", {}).get("providers", []))
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError(f"Invalid provider order in data source configuration: {purpose}.")
        return names

    def _candidate(self, name: str) -> ProviderCandidate:
        if name not in self.factories:
            raise ValueError(f"Unknown provider {name!r} in data source configuration.")
        provider = self.factories[name]()
        capabilities = provider.capabilities
        reason = getattr(provider, "availability_reason", None)
        available = bool(getattr(provider, "available", True))
        availability = ProviderAvailability(
            name=name,
            available=available,
            reason=reason,
            version=provider.version,
            capabilities=capabilities,
        )
        interval = float(self.config.get("historical_backfill", {}).get(
            "provider_interval_seconds", {}).get(name, 0.0))
        if interval < 0:
            raise ValueError(f"Provider interval cannot be negative: {name}.")
        return ProviderCandidate(provider, availability, interval)

    def statuses(self) -> list[dict[str, object]]:
        ordered = list(dict.fromkeys(self.config.get("download", {}).get("providers", [])))
        for name in self.factories:
            if name not in ordered:
                ordered.append(name)
        results = []
        for name in ordered:
            if name not in self.factories:
                results.append({"name": name, "available": False, "status": "unavailable: unknown provider",
                                "reason": "unknown provider", "capabilities": None, "version": None})
                continue
            candidate = self._candidate(name)
            results.append(candidate.availability.to_dict())
        return results

    def candidates(
        self,
        *,
        market: str = "CN_A",
        adjustment: str = "none",
        provider: str = "auto",
        purpose: str = "download",
    ) -> list[ProviderCandidate]:
        names = [provider] if provider != "auto" else self.configured_order(purpose)
        candidates: list[ProviderCandidate] = []
        for name in names:
            candidate = self._candidate(name)
            caps = candidate.availability.capabilities
            if market not in caps.markets or adjustment not in caps.adjustments or not caps.daily_bars:
                if provider != "auto":
                    raise ValueError(
                        f"Provider {name} does not support market={market} adjustment={adjustment}."
                    )
                continue
            candidates.append(candidate)
        if provider != "auto" and candidates and not candidates[0].availability.available:
            return candidates
        if not candidates and provider != "auto":
            raise ValueError(f"Provider {provider!r} is not configured or supported for this request.")
        return candidates

    def wait_for_provider(self, candidate: ProviderCandidate) -> None:
        previous = self._last_request.get(candidate.provider.name)
        if previous is not None:
            delay = candidate.interval_seconds - (time.monotonic() - previous)
            if delay > 0:
                self.sleeper(delay)
        self._last_request[candidate.provider.name] = time.monotonic()
