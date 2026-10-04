"""Orchestration of provider fetch, raw archival, validation, and storage."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
import time

import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.data.validation import ValidationResult, validate_daily_bars
from quantlab.providers.akshare import AKShareProvider
from quantlab.research.calendar import AShareTradingCalendar


def download_market_data(
    ticker: str,
    *,
    market: str = "ashare",
    start: date | None = None,
    end: date | None = None,
    adjustment: str = "qfq",
    provider=None,
    store: MarketDataStore | None = None,
) -> dict[str, object]:
    if market != "ashare":
        raise ValueError("Step 3 data ingestion currently supports market=ashare only.")
    identity = normalize_ashare_symbol(ticker)
    provider = provider or AKShareProvider()
    store = store or MarketDataStore()
    ingest_id = str(uuid.uuid4())
    now = pd.Timestamp.now(tz="UTC")
    store.start_ingest(ingest_id, provider.name, "CN_A", identity.symbol, adjustment, start, end)
    try:
        if start is not None and end is not None and _range_is_complete(store, identity.symbol, start, end, adjustment, provider.name):
            reason = "skipped: the requested calendar-session range is already present in the local store"
            store.finish_ingest(ingest_id, status="skipped", error=reason)
            return {"ingest_id": ingest_id, "symbol": identity.symbol, "status": "skipped",
                    "reason": reason, "fetched_rows": 0, "accepted_rows": 0,
                    "rejected_rows": 0, "updated_rows": 0}
        raw = provider.fetch_raw_daily_bars(identity.symbol, start, end, adjustment=adjustment)
        store.save_raw(raw, market="CN_A", symbol=identity.symbol, adjustment=adjustment,
                       ingest_id=ingest_id, source=provider.name, provider_version=provider.version,
                       source_interface=provider.interface, downloaded_at=now)
        standardized = provider.normalize_raw_daily_bars(raw, identity.symbol, adjustment, strict=False)
        standardized["ingest_id"] = ingest_id
        standardized["downloaded_at"] = now
        validation = validate_daily_bars(
            standardized, expected_market="CN_A", expected_exchange=identity.exchange,
            expected_symbol=identity.symbol, expected_adjustment=adjustment, require_amount=True,
        )
        store.record_issues(ingest_id, identity.symbol, validation.issues)
        if not validation.is_valid:
            first = validation.issues.iloc[0]
            store.finish_ingest(ingest_id, status="rejected", fetched=len(raw), rejected=len(validation.rejected),
                                error=f"{first['issue_type']}: {first['detail']}")
            return {"ingest_id": ingest_id, "symbol": identity.symbol, "status": "rejected",
                    "fetched_rows": len(raw), "accepted_rows": 0, "rejected_rows": len(validation.rejected),
                    "updated_rows": 0, "issues": validation.issues}
        updated = store.merge_validated(validation.accepted)
        store.finish_ingest(ingest_id, status="success", fetched=len(raw), accepted=len(validation.accepted), updated=updated)
        return {"ingest_id": ingest_id, "symbol": identity.symbol, "status": "success",
                "fetched_rows": len(raw), "accepted_rows": len(validation.accepted),
                "rejected_rows": 0, "updated_rows": updated, "issues": validation.issues}
    except KeyboardInterrupt:
        store.finish_ingest(ingest_id, status="interrupted", error="interrupted by user; rerun this ticker to resume safely")
        raise
    except Exception as exc:
        store.finish_ingest(ingest_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise


def update_market_data(
    ticker: str,
    *,
    market: str = "ashare",
    start: date | None = None,
    end: date | None = None,
    adjustment: str = "qfq",
    full_refresh: bool = False,
    overlap_days: int = 45,
    initial_start: date = date(1990, 1, 1),
    provider=None,
    store: MarketDataStore | None = None,
) -> dict[str, object]:
    store = store or MarketDataStore()
    symbol = normalize_ashare_symbol(ticker).symbol
    if start is None and not full_refresh:
        latest = store.latest_date(symbol, "akshare", adjustment)
        start = max(initial_start, latest - timedelta(days=overlap_days)) if latest else initial_start
    elif full_refresh and start is None:
        start = initial_start
    return download_market_data(symbol, market=market, start=start, end=end,
                                adjustment=adjustment, provider=provider, store=store)


def validate_market_data(
    ticker: str, *, store: MarketDataStore | None = None, adjustment: str = "qfq",
) -> ValidationResult:
    store = store or MarketDataStore()
    identity = normalize_ashare_symbol(ticker)
    bars = store.query(symbol=identity.symbol, market="CN_A", adjustment=adjustment, source="akshare")
    if bars.empty:
        return validate_daily_bars(bars, expected_market="CN_A", expected_exchange=identity.exchange,
                                   expected_symbol=identity.symbol, expected_adjustment=adjustment, require_amount=True)
    return validate_daily_bars(bars, expected_market="CN_A", expected_exchange=identity.exchange,
                               expected_symbol=identity.symbol, expected_adjustment=adjustment, require_amount=True)


def query_market_data(
    *, ticker: str | None = None, market: str | None = "CN_A", start: date | None = None,
    end: date | None = None, adjustment: str | None = None, source: str | None = None,
    store: MarketDataStore | None = None,
) -> pd.DataFrame:
    symbol = normalize_ashare_symbol(ticker).symbol if ticker else None
    canonical_market = "CN_A" if market == "ashare" else market
    return (store or MarketDataStore()).query(symbol=symbol, market=canonical_market, start=start,
                                               end=end, adjustment=adjustment, source=source)


def get_data_status(*, store: MarketDataStore | None = None) -> pd.DataFrame:
    return (store or MarketDataStore()).status()


def _range_is_complete(
    store: MarketDataStore, symbol: str, start: date, end: date, adjustment: str, source: str
) -> bool:
    try:
        calendar = AShareTradingCalendar(start, end)
        expected = calendar.sessions_between(start, end)
    except ValueError:
        return False
    if expected.empty:
        return False
    existing = store.query(symbol=symbol, market="CN_A", start=start, end=end,
                           adjustment=adjustment, source=source)
    if existing.empty:
        return False
    present = set(pd.to_datetime(existing["trade_date"]).dt.normalize())
    return set(expected).issubset(present)


def _record_skipped(
    store: MarketDataStore, ticker: str, *, adjustment: str, start: date | None,
    end: date | None, reason: str,
) -> dict[str, object]:
    ingest_id = str(uuid.uuid4())
    store.start_ingest(ingest_id, "akshare", "CN_A", ticker, adjustment, start, end)
    store.finish_ingest(ingest_id, status="skipped", error=reason)
    return {"ingest_id": ingest_id, "symbol": ticker, "status": "skipped", "reason": reason,
            "fetched_rows": 0, "accepted_rows": 0, "rejected_rows": 0, "updated_rows": 0}


def download_market_data_batch(
    tickers: list[str],
    *,
    market: str = "ashare",
    start: date | None = None,
    end: date | None = None,
    adjustment: str = "qfq",
    update: bool = False,
    full_refresh: bool = False,
    throttle_seconds: float = 1.5,
    provider=None,
    store: MarketDataStore | None = None,
) -> list[dict[str, object]]:
    """Process symbols serially, recording per-symbol outcomes and preserving resume points."""
    if throttle_seconds < 0:
        raise ValueError("throttle_seconds cannot be negative.")
    store = store or MarketDataStore()
    outcomes: list[dict[str, object]] = []
    for index, ticker in enumerate(tickers):
        try:
            if update:
                outcome = update_market_data(ticker, market=market, start=start, end=end,
                                             adjustment=adjustment, full_refresh=full_refresh,
                                             provider=provider, store=store)
            else:
                outcome = download_market_data(ticker, market=market, start=start, end=end,
                                               adjustment=adjustment, provider=provider, store=store)
            outcomes.append(outcome)
        except KeyboardInterrupt:
            raise
        except ValueError as exc:
            try:
                normalize_ashare_symbol(ticker)
            except ValueError:
                outcomes.append(_record_skipped(store, ticker, adjustment=adjustment, start=start, end=end,
                                                reason=f"skipped: invalid/unsupported pool symbol: {exc}"))
                continue
            outcomes.append({"symbol": ticker, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            outcomes.append({"symbol": ticker, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
        if index < len(tickers) - 1 and throttle_seconds:
            time.sleep(throttle_seconds)
    return outcomes

