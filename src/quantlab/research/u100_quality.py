"""Date-scoped, per-symbol quality audit for the frozen U100 development pool."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.research.calendar import AShareTradingCalendar


DEVELOPMENT_END = date(2023, 12, 31)
ADJUSTMENT_FLAG = {"none": "3", "qfq": "2"}


def audit_u100_data(
    *,
    store: MarketDataStore | None = None,
    universe_path: Path | str = "config/ashare_universe_breadth_100.json",
    start: date = date(2018, 1, 1),
    end: date = DEVELOPMENT_END,
    missing_ratio_limit: float = 0.02,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Audit all frozen U100 symbols and both adjustment partitions; every query is date bounded."""
    if start < date(2018, 1, 1) or end > DEVELOPMENT_END or start > DEVELOPMENT_END:
        raise ValueError("U100 quality audit is scoped to 2018-01-01 through 2023-12-31.")
    if start > end or not 0 <= missing_ratio_limit <= 1:
        raise ValueError("Invalid U100 quality audit date range or missing-ratio limit.")
    root = Path(__file__).resolve().parents[3]
    path = Path(universe_path)
    if not path.is_absolute():
        path = root / path
    pool = json.loads(path.read_text(encoding="utf-8-sig"))
    members = pool.get("members")
    symbols = [str(member["symbol"]) for member in members or []]
    if len(symbols) != 100 or len(set(symbols)) != 100:
        raise ValueError("Frozen U100 must contain exactly 100 unique symbols.")

    store = store or MarketDataStore(root / "data")
    calendar = AShareTradingCalendar(start, end)
    records: list[dict[str, object]] = []
    issues_by_key: dict[tuple[str, str], dict[str, int]] = {}
    attempt_by_key: dict[tuple[str, str], list[dict[str, object]]] = {}
    successful_ingests_by_key: dict[tuple[str, str], set[str]] = {}

    with store._connect() as con:
        for symbol in symbols:
            for adjustment in ("none", "qfq"):
                attempts = con.execute(
                    """SELECT ingest_id,status,started_at,requested_start,requested_end,fetched_rows,accepted_rows,
                              rejected_rows,error,completed_at
                       FROM ingest_runs
                       WHERE source='baostock' AND symbol=? AND adjustment=?
                         AND (requested_start IS NULL OR requested_start<=?)
                         AND (requested_end IS NULL OR requested_end>=?)
                       ORDER BY started_at""",
                    [symbol, adjustment, end, start],
                ).df()
                attempt_by_key[(symbol, adjustment)] = attempts.to_dict("records")
                successful_ingests_by_key[(symbol, adjustment)] = set(
                    attempts.loc[attempts["status"].eq("success"), "ingest_id"].astype(str)
                ) if not attempts.empty else set()
                if attempts.empty:
                    issues_by_key[(symbol, adjustment)] = {}
                else:
                    ingest_ids = attempts["ingest_id"].astype(str).tolist()
                    con.register("u100_ingest_ids", pd.DataFrame({"ingest_id": ingest_ids}))
                    issue_frame = con.execute(
                        """SELECT vi.issue_type, count(*) AS issue_count
                           FROM validation_issues vi JOIN u100_ingest_ids u USING(ingest_id)
                           WHERE vi.symbol=? GROUP BY vi.issue_type""",
                        [symbol],
                    ).df()
                    issues_by_key[(symbol, adjustment)] = {
                        str(row.issue_type): int(row.issue_count) for row in issue_frame.itertuples()
                    }
                    con.unregister("u100_ingest_ids")

    for member in members:
        symbol = str(member["symbol"])
        listing = pd.to_datetime(member.get("listing_date"), errors="coerce")
        eligible_start = max(pd.Timestamp(start), listing.normalize()) if pd.notna(listing) else pd.Timestamp(start)
        expected = calendar.sessions_between(eligible_start, pd.Timestamp(end)) if eligible_start <= pd.Timestamp(end) else pd.DatetimeIndex([])
        for adjustment in ("none", "qfq"):
            bars = store.query(symbol=symbol, market="CN_A", start=start, end=end,
                               adjustment=adjustment, source="baostock")
            scoped_all = store.query(symbol=symbol, market="CN_A", start=start, end=end)
            bars = bars.copy()
            if not bars.empty:
                bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="coerce").dt.normalize()

            raw_dir = (store.raw_ashare / "source=baostock" /
                       f"symbol={symbol.replace('.', '_')}" / f"adjustment={adjustment}")
            raw_frames: list[pd.DataFrame] = []
            raw_file_count = 0
            for raw_path in sorted(raw_dir.glob("*.parquet")) if raw_dir.exists() else []:
                try:
                    raw = pd.read_parquet(raw_path)
                except (OSError, ValueError):
                    continue
                if "date" not in raw:
                    continue
                raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.normalize()
                raw = raw.loc[raw["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()
                if not raw.empty:
                    raw_file_count += 1
                    raw_frames.append(raw.reindex(columns=["date", "tradestatus", "adjustflag", "volume", "amount",
                                                           "_ingest_id", "_downloaded_at"]))
            raw = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame(
                columns=["date", "tradestatus", "adjustflag", "volume", "amount", "_ingest_id", "_downloaded_at"]
            )
            successful_ids = successful_ingests_by_key[(symbol, adjustment)]
            if not raw.empty and "_ingest_id" in raw:
                raw = raw.loc[raw["_ingest_id"].astype(str).isin(successful_ids)].copy()
            if not raw.empty:
                raw["tradestatus"] = raw["tradestatus"].astype("string").str.strip()
                raw["adjustflag"] = raw["adjustflag"].astype("string").str.strip()
                raw["_downloaded_at"] = pd.to_datetime(raw["_downloaded_at"], utc=True, errors="coerce")
                # Resolve overlapping successful snapshots per date to the newest provider response.
                # Older raw attempts remain archived and are still represented in the attempt ledger.
                latest_time = raw.groupby("date")["_downloaded_at"].transform("max")
                latest_rows = raw.loc[raw["_downloaded_at"].eq(latest_time)].copy()
                raw_snapshot_duplicate_rows = int(latest_rows.duplicated(["date"]).sum())
                raw = latest_rows.drop_duplicates(["date"], keep="last")
            else:
                raw_snapshot_duplicate_rows = 0
            response_dates = set(pd.DatetimeIndex(raw["date"].dropna().unique())) if len(raw) else set()
            if len(raw) and "tradestatus" in raw:
                no_trade_mask = raw["tradestatus"].eq("0")
                active_mask = raw["tradestatus"].eq("1")
                no_trade_dates = set(raw.loc[no_trade_mask, "date"].dropna())
                active_response_dates = set(raw.loc[active_mask, "date"].dropna())
                no_trade_missing_volume_amount = int(
                    raw.loc[no_trade_mask, ["volume", "amount"]].isna().any(axis=1).sum()
                )
            else:
                no_trade_dates = set()
                active_response_dates = set()
                no_trade_missing_volume_amount = 0
            valid_dates = set(bars["trade_date"].dropna()) if len(bars) else set()
            expected_set = set(expected)
            in_scope_response = response_dates & expected_set
            missing_response = expected_set - in_scope_response
            unknown_status = (in_scope_response - no_trade_dates - active_response_dates)
            active_without_bar = (active_response_dates & expected_set) - valid_dates
            unknown_gaps = unknown_status | active_without_bar
            bars_on_provider_no_trade = valid_dates & no_trade_dates

            prices = bars.reindex(columns=["open", "high", "low", "close"]).apply(pd.to_numeric, errors="coerce")
            finite_prices = bool(np.isfinite(prices.to_numpy(dtype=float)).all()) if len(bars) else False
            ohlc_ok = bool(((prices["low"] <= prices[["open", "close"]].min(axis=1)) &
                            (prices["high"] >= prices[["open", "close"]].max(axis=1)) &
                            (prices["high"] >= prices["low"])).all()) if len(bars) else False
            volume = pd.to_numeric(bars.get("volume", pd.Series(dtype=float)), errors="coerce")
            amount = pd.to_numeric(bars.get("amount", pd.Series(dtype=float)), errors="coerce")
            finite_volume_amount = bool(np.isfinite(volume.to_numpy(dtype=float)).all() and
                                        np.isfinite(amount.to_numpy(dtype=float)).all()) if len(bars) else False
            nonnegative_volume_amount = bool((volume.ge(0) & amount.ge(0)).all()) if len(bars) else False
            duplicate_bars = int(bars.duplicated(["symbol", "trade_date"]).sum()) if len(bars) else 0
            flags = sorted(raw["adjustflag"].dropna().astype(str).unique()) if len(raw) else []
            sources = sorted(scoped_all["source"].dropna().astype(str).unique()) if len(scoped_all) else []
            adjustments = sorted(scoped_all["adjustment"].dropna().astype(str).unique()) if len(scoped_all) else []
            exact_partition = bool(not len(bars) or (bars["source"].eq("baostock").all() and
                                  bars["adjustment"].eq(adjustment).all()))
            flag_ok = not flags or flags == [ADJUSTMENT_FLAG[adjustment]]
            unexpected_valid_dates = valid_dates - expected_set
            classified_expected = ((valid_dates & active_response_dates) | no_trade_dates) & expected_set
            coverage_ratio = len(classified_expected) / len(expected) if len(expected) else 1.0
            attempts = attempt_by_key[(symbol, adjustment)]
            rejected_attempts = sum(str(item["status"]) in {"failed", "rejected"} for item in attempts)
            issue_types = issues_by_key[(symbol, adjustment)]
            snapshots = (pd.to_datetime(bars["downloaded_at"], utc=True, errors="coerce").dropna()
                         if len(bars) else pd.Series(dtype="datetime64[ns, UTC]"))
            record = {
                "symbol": symbol, "exchange": member.get("exchange"), "listing_date": member.get("listing_date"),
                "adjustment": adjustment, "provider": "baostock", "expected_eligible_sessions": len(expected),
                "raw_response_sessions": len(in_scope_response), "actual_trading_observations": len(valid_dates & active_response_dates),
                "provider_reported_no_trade_sessions": len(no_trade_dates & expected_set),
                "raw_nontrade_rows_missing_volume_amount": no_trade_missing_volume_amount,
                "canonical_bars_on_latest_provider_no_trade_dates": len(bars_on_provider_no_trade & expected_set),
                "raw_duplicate_rows_in_latest_response_snapshot": raw_snapshot_duplicate_rows,
                "missing_provider_response_sessions": len(missing_response), "unknown_gap_sessions": len(unknown_gaps),
                "valid_bars_outside_listing_or_expected_sessions": len(unexpected_valid_dates),
                "bars": len(bars), "first_valid_observation": bars["trade_date"].min().date().isoformat() if len(bars) else None,
                "last_valid_observation": bars["trade_date"].max().date().isoformat() if len(bars) else None,
                "coverage_ratio_active_or_provider_no_trade": coverage_ratio,
                "raw_parquet_files_with_scoped_rows": raw_file_count,
                "raw_adjustment_flags": flags, "expected_adjustment_flag": ADJUSTMENT_FLAG[adjustment],
                "finite_prices": finite_prices, "ohlc_consistent": ohlc_ok,
                "finite_volume_amount": finite_volume_amount, "nonnegative_volume_amount": nonnegative_volume_amount,
                "duplicate_bar_rows": duplicate_bars, "sources_in_scope": sources,
                "adjustments_in_scope": adjustments, "exact_partition_ok": exact_partition and flag_ok,
                "provider_version": sorted(bars["provider_version"].dropna().astype(str).unique()) if len(bars) else [],
                "source_interface": sorted(bars["source_interface"].dropna().astype(str).unique()) if len(bars) else [],
                "downloaded_at_latest_utc": snapshots.max().isoformat() if len(snapshots) else None,
                "ingest_attempts": len(attempts), "rejected_attempts": rejected_attempts,
                "validation_issue_types": issue_types,
                "unknown_listing_date": pd.isna(listing),
            }
            record["row_quality_ok"] = bool(
                len(bars) > 0 and finite_prices and ohlc_ok and finite_volume_amount and
                nonnegative_volume_amount and duplicate_bars == 0 and record["exact_partition_ok"] and
                coverage_ratio >= 1 - missing_ratio_limit and not record["unknown_listing_date"] and
                len(unexpected_valid_dates) == 0 and raw_snapshot_duplicate_rows == 0
            )
            records.append(record)

    quality = pd.DataFrame(records)
    wide: dict[str, object] = {}
    for symbol, group in quality.groupby("symbol", sort=False):
        none = group.loc[group.adjustment.eq("none")].iloc[0]
        qfq = group.loc[group.adjustment.eq("qfq")].iloc[0]
        wide[symbol] = {
            "none_bars": int(none["bars"]), "qfq_bars": int(qfq["bars"]),
            "none_qfq_date_count_equal": int(none["bars"]) == int(qfq["bars"]),
            "none_snapshot_time": none["downloaded_at_latest_utc"],
            "qfq_snapshot_time": qfq["downloaded_at_latest_utc"],
        }
    qfq_times = {item["qfq_snapshot_time"] for item in wide.values() if item["qfq_snapshot_time"]}
    status = "u100_data_ready" if bool(quality["row_quality_ok"].all()) else "u100_data_incomplete"
    summary: dict[str, object] = {
        "status": status,
        "universe_hash": pool.get("universe_hash"),
        "universe_size": len(symbols),
        "symbol_adjustment_partitions": len(quality),
        "none_partitions_quality_ok": int(quality.loc[quality.adjustment.eq("none"), "row_quality_ok"].sum()),
        "qfq_partitions_quality_ok": int(quality.loc[quality.adjustment.eq("qfq"), "row_quality_ok"].sum()),
        "symbols_with_none_and_qfq_same_active_bar_count": int(sum(v["none_qfq_date_count_equal"] for v in wide.values())),
        "provider": "baostock",
        "adjustment_flags": ADJUSTMENT_FLAG,
        "research_start": start.isoformat(), "research_end_cap": end.isoformat(),
        "missing_ratio_limit": missing_ratio_limit,
        "mixed_vendor_snapshot_times": True,
        "qfq_snapshot_time_count": len(qfq_times),
        "per_symbol_snapshot_times": wide,
        "data_quality_sql_scope": "symbol + market + source + adjustment + trade_date BETWEEN requested start/end",
        "training": "not performed", "walk_forward": "not performed", "backtest": "not performed",
    }
    return quality, summary
