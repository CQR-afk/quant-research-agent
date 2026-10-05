"""Checkpointed per-symbol historical backfill with deterministic provider scope."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from quantlab.data.service import download_market_data
from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.providers.router import ProviderRouter


DEVELOPMENT_END = date(2023, 12, 31)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def backfill_market_data(
    *,
    universe: Path | str | None = None,
    start: date = date(2018, 1, 1),
    end: date = date(2025, 12, 31),
    adjustment: str = "none",
    provider: str = "auto",
    retry_failed_only: bool = False,
    limit: int | None = None,
    router: ProviderRouter | None = None,
    store: MarketDataStore | None = None,
    throttle_seconds: float | None = None,
    development_only: bool = False,
) -> dict[str, object]:
    """Process at most one small batch; completed symbols are never re-requested.

    One selected provider is fixed for the complete job. Per-symbol fallback is
    deliberately left to single-symbol download requests, avoiding a silent
    cross-provider pool merge.
    """
    if development_only:
        if start < date(2018, 1, 1):
            raise ValueError("Development-only backfill starts no earlier than 2018-01-01.")
        if end > DEVELOPMENT_END or start > DEVELOPMENT_END:
            raise ValueError("Development-only backfill requests are capped at 2023-12-31.")
    if start > end:
        raise ValueError("Backfill start must be on or before end.")
    if adjustment not in {"none", "qfq", "hfq"}:
        raise ValueError("A-share backfill adjustment must be none, qfq, or hfq.")
    if limit is not None and limit < 1:
        raise ValueError("Backfill limit must be positive.")
    router = router or ProviderRouter()
    store = store or MarketDataStore()
    universe_path = Path(universe) if universe else _project_root() / "config" / "ashare_universe.json"
    if not universe_path.is_absolute():
        universe_path = (_project_root() / universe_path).resolve()
    payload = json.loads(universe_path.read_text(encoding="utf-8-sig"))
    members = payload.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("Universe configuration must contain a non-empty members list.")
    identities = []
    for member in members:
        identity = normalize_ashare_symbol(member["symbol"])
        if member.get("exchange") != identity.exchange:
            raise ValueError(f"Universe exchange mismatch for {identity.symbol}.")
        identities.append(identity.symbol)
    if len(set(identities)) != len(identities):
        raise ValueError("Universe contains duplicate symbols.")

    candidates = router.candidates(market="CN_A", adjustment=adjustment,
                                   provider=provider, purpose="historical_backfill")
    available = [candidate for candidate in candidates if candidate.availability.available]
    if not available:
        return {"status": "blocked", "reason": "No configured compatible provider is available.",
                "provider_status": [candidate.availability.to_dict() for candidate in candidates]}
    chosen = available[0]
    job_material = {
        "universe_path": str(universe_path),
        "universe_id": payload.get("pool_id"),
        "symbols": identities,
        "start": start.isoformat(), "end": end.isoformat(),
        "adjustment": adjustment, "provider": chosen.provider.name,
    }
    job_key = hashlib.sha256(json.dumps(job_material, sort_keys=True).encode()).hexdigest()
    job_id = store.get_or_create_backfill_job(
        job_key=job_key, universe_path=str(universe_path), start=start, end=end,
        adjustment=adjustment, provider=chosen.provider.name, symbols=identities,
    )
    items = store.backfill_items(job_id)
    # The status table is alphabetically ordered for display; execution must
    # follow the frozen universe order so pilots and resumed batches are stable.
    status_by_symbol = dict(zip(items["symbol"], items["status"], strict=True))
    eligible_statuses = {"failed"} if retry_failed_only else {"pending", "running"}
    selected = [symbol for symbol in identities if status_by_symbol.get(symbol) in eligible_statuses]
    configured_limit = int(router.config.get("historical_backfill", {}).get("batch_size", 3))
    batch_limit = limit if limit is not None else configured_limit
    selected = selected[:batch_limit]
    throttle = chosen.interval_seconds if throttle_seconds is None else throttle_seconds
    if throttle < 0:
        raise ValueError("throttle_seconds cannot be negative.")

    outcomes: list[dict[str, object]] = []
    for index, symbol in enumerate(selected):
        store.set_backfill_item(job_id, symbol, status="running", provider=chosen.provider.name, started=True)
        router.wait_for_provider(chosen)
        try:
            result = download_market_data(symbol, market="ashare", start=start, end=end,
                                          adjustment=adjustment, provider=chosen.provider, store=store,
                                          development_only=development_only)
            succeeded = result["status"] in {"success", "skipped"}
            result_status = "completed" if succeeded else "failed"
            if succeeded:
                message = None
            elif result.get("status") == "rejected" and isinstance(result.get("issues"), pd.DataFrame):
                issues = result["issues"]
                issue_codes = sorted(issues["issue_type"].dropna().astype(str).unique()) if not issues.empty else []
                message = (f"validation rejected {result.get('rejected_rows', 0)} row(s); "
                           f"issue types: {', '.join(issue_codes) or 'unknown'}")
            else:
                message = str(result.get("reason") or "provider rejected the full request")
            store.set_backfill_item(job_id, symbol, status=result_status, provider=chosen.provider.name,
                                    row_count=int(result.get("accepted_rows", 0)), error=message)
            outcomes.append({"symbol": symbol, "provider": chosen.provider.name,
                             "status": result_status, "result": result})
        except KeyboardInterrupt:
            store.set_backfill_item(job_id, symbol, status="pending", provider=chosen.provider.name,
                                    error="interrupted; safe to resume", started=False)
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            store.set_backfill_item(job_id, symbol, status="failed", provider=chosen.provider.name,
                                    error=reason)
            outcomes.append({"symbol": symbol, "provider": chosen.provider.name,
                             "status": "failed", "reason": reason})

    final_items = store.backfill_items(job_id)
    any_pending = final_items["status"].isin(["pending", "running"]).any()
    any_failed = (final_items["status"] == "failed").any()
    job_status = "complete" if not any_pending and not any_failed else "partial_failed" if not any_pending else "in_progress"
    return {
        "status": job_status,
        "job_id": job_id,
        "provider": chosen.provider.name,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "adjustment": adjustment,
        "batch_limit": batch_limit,
        "processed_this_run": len(selected),
        "retry_failed_only": retry_failed_only,
        "items": final_items.to_dict("records"),
        "outcomes": outcomes,
    }


def backfill_status(
    *, store: MarketDataStore | None = None, universe: Path | str | None = None,
    start: date | None = None, end: date | None = None,
    adjustment: str | None = None, provider: str | None = None,
    development_only: bool = False,
) -> pd.DataFrame:
    if development_only:
        if start is not None and start < date(2018, 1, 1):
            raise ValueError("Development-only status starts no earlier than 2018-01-01.")
        start = start or date(2018, 1, 1)
        end = end or DEVELOPMENT_END
        if end > DEVELOPMENT_END:
            raise ValueError("Development-only status is capped at 2023-12-31.")
    universe_path = None
    if universe is not None:
        path = Path(universe)
        if not path.is_absolute():
            path = _project_root() / path
        universe_path = str(path.resolve())
    return (store or MarketDataStore()).backfill_jobs_status(
        universe_path=universe_path, start=start, end=end, adjustment=adjustment, provider=provider,
    )
