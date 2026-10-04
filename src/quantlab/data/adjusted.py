"""Build provenance-preserving views over stored vendor price bases."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol


def build_adjusted_price_view(
    symbol: str,
    start: date | None,
    end: date | None,
    adjustment: str,
    provider: str | None = None,
    *,
    store: MarketDataStore | None = None,
) -> pd.DataFrame:
    """Return one provider's stored price series with explicit basis metadata.

    Vendor-adjusted histories remain snapshots. This function never derives
    point-in-time prices by applying today's factors to old bars.
    """
    if adjustment not in {"none", "qfq", "hfq"}:
        raise ValueError("adjustment must be one of none, qfq, hfq.")
    identity = normalize_ashare_symbol(symbol)
    store = store or MarketDataStore()
    bars = store.query(symbol=identity.symbol, market="CN_A", start=start, end=end,
                       adjustment=adjustment, source=provider)
    if bars.empty:
        return pd.DataFrame(columns=[
            "market", "exchange", "symbol", "trade_date", "open", "high", "low", "close",
            "volume", "amount", "adjustment", "currency", "provider", "provider_version",
            "source_interface", "downloaded_at", "ingest_id", "record_version", "revision_id",
            "price_basis", "adjustment_method", "adjustment_snapshot_time",
            "base_price_source", "adjustment_source",
        ])
    providers = sorted(bars["source"].dropna().astype(str).unique())
    if provider is None and len(providers) > 1:
        raise ValueError(
            "Multiple providers have data for this price view; specify provider explicitly."
        )
    selected_provider = provider or providers[0]
    bars = bars.loc[bars["source"] == selected_provider].copy()
    bars = bars.rename(columns={"source": "provider"})
    bars["price_basis"] = (
        "canonical_unadjusted_vendor" if adjustment == "none" else "vendor_snapshot_adjusted"
    )
    bars["adjustment_method"] = "none" if adjustment == "none" else f"{selected_provider}_{adjustment}"
    bars["adjustment_snapshot_time"] = None if adjustment == "none" else bars["downloaded_at"]
    bars["base_price_source"] = selected_provider
    bars["adjustment_source"] = None if adjustment == "none" else selected_provider
    return bars.sort_values(["trade_date", "symbol"], kind="stable").reset_index(drop=True)
