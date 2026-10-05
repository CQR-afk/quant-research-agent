"""Orchestration of provider fetch, raw archival, validation, and storage."""

from __future__ import annotations

import uuid
import json
from datetime import date, timedelta
import time

import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.data.validation import ValidationResult, validate_daily_bars
from quantlab.providers.akshare import AKShareProvider
from quantlab.providers.router import ProviderRouter
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
        provider_metadata = provider.normalize_provider_metadata(raw, identity.symbol)
        nontrading_mask = pd.Series(False, index=raw.index)
        if "tradestatus" in raw:
            nontrading_mask = raw["tradestatus"].astype(str).str.strip().eq("0")
        eligible_raw = raw.loc[~nontrading_mask].reset_index(drop=True)
        standardized = provider.normalize_raw_daily_bars(eligible_raw, identity.symbol, adjustment, strict=False)
        standardized["ingest_id"] = ingest_id
        standardized["downloaded_at"] = now
        validation = validate_daily_bars(
            standardized, expected_market="CN_A", expected_exchange=identity.exchange,
            expected_symbol=identity.symbol, expected_adjustment=adjustment, require_amount=True,
        )
        excluded = pd.DataFrame([
            {"row_index": int(index), "issue_type": "provider_reported_nontrading",
             "detail": "BaoStock tradestatus=0; raw record retained, excluded from canonical traded-bar observations."}
            for index in raw.index[nontrading_mask]
        ], columns=["row_index", "issue_type", "detail"])
        issues = pd.concat([validation.issues, excluded], ignore_index=True)
        store.record_issues(ingest_id, identity.symbol, issues)
        if not validation.is_valid:
            first = validation.issues.iloc[0]
            store.finish_ingest(ingest_id, status="rejected", fetched=len(raw), rejected=len(validation.rejected),
                                error=f"{first['issue_type']}: {first['detail']}")
            return {"ingest_id": ingest_id, "symbol": identity.symbol, "status": "rejected",
                    "fetched_rows": len(raw), "accepted_rows": 0, "rejected_rows": len(validation.rejected),
                    "updated_rows": 0, "excluded_nontrading_rows": len(excluded), "issues": issues}
        if not provider_metadata.empty:
            provider_metadata = provider_metadata.copy()
            provider_metadata["provider"] = provider.name
            provider_metadata["symbol"] = identity.symbol
            provider_metadata["provider_version"] = provider.version
            provider_metadata["source_interface"] = provider.interface
            provider_metadata["downloaded_at"] = now
            provider_metadata["ingest_id"] = ingest_id
        updated = store.merge_validated(validation.accepted, provider_metadata)
        store.finish_ingest(ingest_id, status="success", fetched=len(raw), accepted=len(validation.accepted), updated=updated)
        return {"ingest_id": ingest_id, "symbol": identity.symbol, "status": "success",
                "fetched_rows": len(raw), "accepted_rows": len(validation.accepted),
                "rejected_rows": len(validation.rejected), "excluded_nontrading_rows": len(excluded),
                "updated_rows": updated, "issues": issues}
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
        latest = store.latest_date(symbol, provider.name if provider is not None else "akshare", adjustment)
        start = max(initial_start, latest - timedelta(days=overlap_days)) if latest else initial_start
    elif full_refresh and start is None:
        start = initial_start
    return download_market_data(symbol, market=market, start=start, end=end,
                                adjustment=adjustment, provider=provider, store=store)


def validate_market_data(
    ticker: str, *, store: MarketDataStore | None = None, adjustment: str = "qfq", provider: str | None = None,
) -> ValidationResult:
    store = store or MarketDataStore()
    identity = normalize_ashare_symbol(ticker)
    if provider is None:
        sources = store.status()
        if not sources.empty:
            matches = sources.loc[(sources.symbol == identity.symbol) & (sources.adjustment == adjustment), "source"]
            unique = sorted(matches.dropna().astype(str).unique())
        else:
            unique = []
        if len(unique) > 1:
            raise ValueError("Multiple providers have data for this request; specify provider explicitly.")
        provider = unique[0] if unique else None
    bars = store.query(symbol=identity.symbol, market="CN_A", adjustment=adjustment, source=provider)
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
    store = store or MarketDataStore()
    result = store.query(symbol=symbol, market=canonical_market, start=start, end=end,
                         adjustment=adjustment, source=source)
    if source is None and result["source"].dropna().astype(str).nunique() > 1:
        raise ValueError("Query spans multiple providers; specify a provider to prevent mixed-source results.")
    return result


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
    present = set(pd.to_datetime(existing["trade_date"]).dt.normalize()) if not existing.empty else set()
    metadata = store.query_provider_metadata(provider=source, symbol=symbol, start=start, end=end)
    if not metadata.empty:
        for row in metadata.itertuples():
            try:
                state = json.loads(row.metadata_json).get("tradestatus")
            except (TypeError, ValueError, AttributeError):
                state = None
            # Explicit no-trade metadata covers a session for every adjustment
            # because no market bar should exist for it. A traded session is
            # covered only by a bar in this exact source/adjustment partition.
            if str(state).strip() == "0":
                present.add(pd.Timestamp(row.trade_date).normalize())
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


def _record_provider_unavailable(
    store: MarketDataStore, ticker: str, provider: str, *, adjustment: str,
    start: date | None, end: date | None, reason: str,
) -> dict[str, object]:
    ingest_id = str(uuid.uuid4())
    store.start_ingest(ingest_id, provider, "CN_A", ticker, adjustment, start, end)
    store.finish_ingest(ingest_id, status="skipped", error=reason)
    return {"ingest_id": ingest_id, "symbol": ticker, "provider": provider, "status": "skipped",
            "reason": reason, "fetched_rows": 0, "accepted_rows": 0, "rejected_rows": 0,
            "updated_rows": 0}


def download_market_data_routed(
    ticker: str,
    *,
    market: str = "ashare",
    start: date | None = None,
    end: date | None = None,
    adjustment: str = "none",
    provider: str = "auto",
    router: ProviderRouter | None = None,
    store: MarketDataStore | None = None,
) -> dict[str, object]:
    """Try each configured provider for this complete symbol/range, never splice rows."""
    if market != "ashare":
        raise ValueError("A-share provider routing currently supports market=ashare only.")
    identity = normalize_ashare_symbol(ticker)
    store = store or MarketDataStore()
    router = router or ProviderRouter()
    candidates = router.candidates(market="CN_A", adjustment=adjustment, provider=provider)
    attempts: list[dict[str, object]] = []
    final_status = "failed"
    for candidate in candidates:
        name = candidate.provider.name
        if not candidate.availability.available:
            attempts.append(_record_provider_unavailable(
                store, identity.symbol, name, adjustment=adjustment, start=start, end=end,
                reason=f"skipped: {candidate.availability.reason or 'provider unavailable'}",
            ))
            continue
        router.wait_for_provider(candidate)
        try:
            outcome = download_market_data(
                identity.symbol, market=market, start=start, end=end,
                adjustment=adjustment, provider=candidate.provider, store=store,
            )
            outcome["provider"] = name
            attempts.append(outcome)
            if outcome["status"] in {"success", "skipped"}:
                return {**outcome, "attempts": attempts, "selected_provider": name}
            final_status = str(outcome["status"])
        except Exception as exc:
            attempts.append({"symbol": identity.symbol, "provider": name, "status": "failed",
                             "reason": f"{type(exc).__name__}: {exc}"})
        if provider != "auto":
            break
    return {"symbol": identity.symbol, "status": final_status, "selected_provider": None,
            "reason": "all compatible providers failed or were unavailable; no provider rows were combined",
            "attempts": attempts}


def update_market_data_routed(
    ticker: str,
    *,
    market: str = "ashare",
    start: date | None = None,
    end: date | None = None,
    adjustment: str = "none",
    provider: str = "auto",
    full_refresh: bool = False,
    overlap_days: int = 45,
    initial_start: date = date(1990, 1, 1),
    router: ProviderRouter | None = None,
    store: MarketDataStore | None = None,
) -> dict[str, object]:
    """Update using one provider's own overlap window; provider histories stay separate."""
    router, store = router or ProviderRouter(), store or MarketDataStore()
    identity = normalize_ashare_symbol(ticker)
    candidates = router.candidates(market="CN_A", adjustment=adjustment, provider=provider)
    attempts: list[dict[str, object]] = []
    for candidate in candidates:
        if not candidate.availability.available:
            attempts.append(_record_provider_unavailable(
                store, identity.symbol, candidate.provider.name, adjustment=adjustment,
                start=start, end=end, reason=f"skipped: {candidate.availability.reason or 'provider unavailable'}",
            ))
            continue
        provider_start = start
        if provider_start is None:
            latest = None if full_refresh else store.latest_date(
                identity.symbol, candidate.provider.name, adjustment
            )
            provider_start = initial_start if full_refresh or latest is None else max(
                initial_start, latest - timedelta(days=overlap_days)
            )
        router.wait_for_provider(candidate)
        try:
            result = download_market_data(
                identity.symbol, market=market, start=provider_start, end=end,
                adjustment=adjustment, provider=candidate.provider, store=store,
            )
            result["provider"] = candidate.provider.name
            attempts.append(result)
            if result["status"] in {"success", "skipped"}:
                return {**result, "attempts": attempts, "selected_provider": candidate.provider.name}
            if provider != "auto":
                break
        except Exception as exc:
            attempts.append({"symbol": identity.symbol, "provider": candidate.provider.name,
                             "status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
            if provider != "auto":
                break
    return {"symbol": identity.symbol, "status": "failed", "selected_provider": None,
            "reason": "all compatible providers failed or were unavailable; no provider rows were combined",
            "attempts": attempts}


def download_adjustment_factors(
    ticker: str, *, start: date | None = None, end: date | None = None,
    provider=None, store: MarketDataStore | None = None,
) -> dict[str, object]:
    """Fetch adjustment factors into their own versioned table, never rewriting bars."""
    from quantlab.providers.tushare import TushareProvider

    identity = normalize_ashare_symbol(ticker)
    provider = provider or TushareProvider()
    store = store or MarketDataStore()
    ingest_id = str(uuid.uuid4())
    store.start_ingest(ingest_id, provider.name, "CN_A", identity.symbol, "factor", start, end)
    try:
        factors = provider.fetch_adjustment_factors(identity.symbol, start, end)
        factors["ingest_id"] = ingest_id
        updated = store.merge_adjustment_factors(factors)
        store.finish_ingest(ingest_id, status="success", fetched=len(factors), accepted=len(factors), updated=updated)
        return {"status": "success", "provider": provider.name, "symbol": identity.symbol,
                "fetched_rows": len(factors), "updated_rows": updated, "ingest_id": ingest_id}
    except KeyboardInterrupt:
        store.finish_ingest(ingest_id, status="interrupted", error="interrupted by user")
        raise
    except Exception as exc:
        # Provider adapters sanitize upstream errors; never include credential-bearing request data.
        safe_reason = f"{type(exc).__name__}: {exc}"
        store.finish_ingest(ingest_id, status="failed", error=safe_reason)
        raise


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

