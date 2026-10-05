"""Build model-ready A-share rows only from the local research database."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.features import make_forward_target
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.features import FEATURE_VERSION, feature_spec


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DatasetBuildResult:
    dataset: pd.DataFrame
    coverage: pd.DataFrame
    manifest: dict[str, object]
    dataset_path: Path
    manifest_path: Path


def _read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _max_gap_days(expected: pd.DatetimeIndex, actual: set[pd.Timestamp]) -> int:
    absent = [day for day in expected if day not in actual]
    if not absent:
        return 0
    longest = current = 1
    for previous, day in zip(absent, absent[1:]):
        current = current + 1 if expected.get_loc(day) == expected.get_loc(previous) + 1 else 1
        longest = max(longest, current)
    return longest


def _data_version(bars: pd.DataFrame) -> str:
    cols = ["symbol", "trade_date", "source", "adjustment", "row_hash", "record_version", "ingest_id"]
    frame = bars.reindex(columns=cols).sort_values(["symbol", "trade_date", "source", "adjustment"])
    content = frame.to_csv(index=False, na_rep="").encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def build_research_dataset(
    *,
    store: MarketDataStore | None = None,
    pool_path: Path | str | None = None,
    research_config_path: Path | str | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
    adjustment: str | None = None,
    provider: str | None = None,
    output_path: Path | str | None = None,
    feature_version: str = FEATURE_VERSION,
    development_only: bool = False,
    reused_universe_hash: str | None = None,
) -> DatasetBuildResult:
    """Create per-security causal features and five-observation labels from DuckDB."""
    root = project_root()
    config_path = Path(research_config_path) if research_config_path else root / "config" / "research.json"
    config = _read_json(config_path)
    pool_path = Path(pool_path) if pool_path else Path(str(config.get("universe", "config/ashare_universe.json")))
    if not pool_path.is_absolute():
        pool_path = root / pool_path
    pool = _read_json(pool_path)
    configured_range = config.get("date_range", {})
    research_start = pd.Timestamp(start or configured_range.get("start") or pool["research_start"]).normalize()
    research_end = pd.Timestamp(end or configured_range.get("end") or pool["research_end"]).normalize()
    if research_start > research_end:
        raise ValueError("Research start must be on or before end.")
    if development_only and (research_start < pd.Timestamp("2018-01-01") or
                             research_end > pd.Timestamp("2023-12-31")):
        raise ValueError("Development-only datasets are scoped to 2018-01-01 through 2023-12-31.")
    feature_columns, feature_parameters, builder_type = feature_spec(feature_version)
    if feature_version != FEATURE_VERSION and research_end >= pd.Timestamp("2024-01-01"):
        raise ValueError("The development feature build is sealed at 2023-12-31; later dates are unavailable to this workflow.")
    adjustment = adjustment or str(config["adjustment"])
    market = str(config.get("market", "CN_A"))
    if market != "CN_A":
        raise ValueError("The current model-ready dataset builder supports market=CN_A only.")
    provider = provider or config.get("provider")
    calendar = AShareTradingCalendar(research_start, research_end)
    store = store or MarketDataStore(root / "data")
    if provider is None:
        stored = store.status()
        if not stored.empty:
            eligible = stored.loc[(stored["market"] == "CN_A") & (stored["adjustment"] == adjustment), "source"]
            sources = sorted(eligible.dropna().astype(str).unique())
        else:
            sources = []
        if len(sources) > 1:
            raise ValueError("Multiple providers have data for this adjustment; specify provider explicitly.")
        source = sources[0] if sources else None
    else:
        source = provider
    output_path = Path(output_path) if output_path else root / "data" / "processed" / "ashare_research_dataset_v1.parquet"
    manifest_path = output_path.with_suffix(".manifest.json")

    bars_by_symbol: list[pd.DataFrame] = []
    coverage_records: list[dict[str, object]] = []
    for member in pool["members"]:
        identity = normalize_ashare_symbol(str(member["symbol"]))
        if member.get("exchange") != identity.exchange:
            raise ValueError(f"Pool exchange mismatch for {identity.symbol}: expected {identity.exchange}.")
        bars = store.query(symbol=identity.symbol, market=market, start=research_start.date(),
                           end=research_end.date(), adjustment=adjustment, source=source)
        bars = bars.copy()
        if not bars.empty:
            bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.normalize()
        if not bars.empty and (bars["adjustment"].nunique(dropna=False) != 1 or bars["adjustment"].iloc[0] != adjustment):
            raise ValueError(f"Mixed or unexpected adjustment mode for {identity.symbol}.")

        listing_date = pd.to_datetime(member.get("listing_date"), errors="coerce")
        eligible_start = max(research_start, listing_date.normalize()) if pd.notna(listing_date) else research_start
        expected = (calendar.sessions_between(eligible_start, research_end)
                    if eligible_start <= research_end else pd.DatetimeIndex([]))
        metadata = store.query_provider_metadata(provider=source, symbol=identity.symbol,
                                                 start=research_start.date(), end=research_end.date()) if source else pd.DataFrame()
        status_by_date: dict[pd.Timestamp, str | None] = {}
        if not metadata.empty:
            for row in metadata.itertuples():
                try:
                    status = json.loads(row.metadata_json).get("tradestatus")
                    status = str(status).strip() if status is not None else None
                except (TypeError, ValueError, AttributeError):
                    status = None
                status_by_date[pd.Timestamp(row.trade_date).normalize()] = status if status in {"0", "1"} else None

        bar_dates = set(bars["trade_date"]) if not bars.empty else set()
        # A bar or a provider's explicit trade-status record covers the session.
        # Missing sessions without a status remain Unknown, never inferred as a halt.
        observed = bar_dates | {day for day, status in status_by_date.items() if status in {"0", "1"}}
        missing = expected.difference(pd.DatetimeIndex(sorted(observed)))
        unexpected = [day for day in observed if not calendar.is_session(day)]
        coverage_ratio = len(expected.intersection(pd.DatetimeIndex(sorted(observed)))) / len(expected) if len(expected) else 0.0
        if unexpected:
            coverage_status = "calendar_mismatch"
        elif 1 - coverage_ratio > float(config["maximum_missing_session_ratio"]):
            coverage_status = "gappy"
        else:
            coverage_status = "within_threshold"
        if pd.notna(listing_date):
            history_status = "listing-date-aware; pre-listing sessions excluded from expected coverage"
        else:
            history_status = "listing date Unknown; expected coverage begins at research start"

        unknown_status_dates = {day for day, status in status_by_date.items() if status is None}
        if not bars.empty:
            bars["provider_trade_status"] = bars["trade_date"].map(status_by_date)
            prelisting_mask = bars["trade_date"] < eligible_start
            unknown_status_dates.update(bars.loc[bars["provider_trade_status"].isna(), "trade_date"])
            if source == "baostock":
                research_mask = (bars["provider_trade_status"] == "1") & ~prelisting_mask
            else:
                research_mask = ~prelisting_mask
            research_bars = bars.loc[research_mask].copy()
            first_valid = research_bars["trade_date"].min() if not research_bars.empty else pd.NaT
            last_valid = research_bars["trade_date"].max() if not research_bars.empty else pd.NaT
            unknown_status_rows = len(unknown_status_dates) if source == "baostock" else 0
            excluded_nontrading_rows = int((bars["provider_trade_status"] == "0").sum()) if source == "baostock" else 0
            prelisting_rows = int(prelisting_mask.sum())
            source_versions = sorted(bars["provider_version"].dropna().astype(str).unique().tolist())
            adjustments = sorted(bars["adjustment"].dropna().astype(str).unique().tolist())
        else:
            research_bars = bars
            first_valid = last_valid = pd.NaT
            unknown_status_rows = excluded_nontrading_rows = 0
            prelisting_rows = 0
            source_versions = adjustments = []
        coverage_by_year = []
        for year in range(research_start.year, research_end.year + 1):
            annual_expected = expected[expected.year == year]
            annual_observed = observed.intersection(set(annual_expected))
            annual_missing = annual_expected.difference(pd.DatetimeIndex(sorted(annual_observed)))
            coverage_by_year.append({
                "year": year,
                "expected_sessions": len(annual_expected),
                "observed_sessions": len(annual_observed),
                "missing_sessions": len(annual_missing),
                "missing_ratio": len(annual_missing) / len(annual_expected) if len(annual_expected) else None,
                "missing_status": "Unknown suspension versus provider gap" if len(annual_missing) else None,
            })
        coverage_records.append({
            **member,
            "rows": len(research_bars),
            "canonical_bar_records": len(bars),
            "first_date": first_valid.date().isoformat() if pd.notna(first_valid) else None,
            "last_date": last_valid.date().isoformat() if pd.notna(last_valid) else None,
            "expected_sessions": len(expected),
            "missing_sessions": len(missing),
            "missing_ratio": 1 - coverage_ratio if len(expected) else None,
            "coverage_ratio": coverage_ratio,
            "max_consecutive_missing": _max_gap_days(expected, observed),
            "missing_dates_sample": [day.date().isoformat() for day in missing[:20]],
            "unexpected_dates": len(unexpected),
            "provider_reported_nontrading_sessions": int(sum(status == "0" for status in status_by_date.values())),
            "unknown_trade_status_sessions": unknown_status_rows,
            "prelisting_records": prelisting_rows,
            "missing_session_classification": "unknown_suspension_or_provider_gap" if len(missing) else None,
            "coverage_by_year": coverage_by_year,
            "history_start_status": history_status,
            "coverage_status": coverage_status,
            "source_versions": source_versions,
            "adjustments": adjustments,
        })
        if not research_bars.empty:
            bars_by_symbol.append(research_bars)

    if bars_by_symbol:
        bars = pd.concat(bars_by_symbol, ignore_index=True)
        if bars.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("Research bars contain duplicate symbol/prediction dates.")
        features = builder_type().build(bars)
        targets: list[pd.DataFrame] = []
        for symbol, group in bars.groupby("symbol", sort=False):
            chronological = group.sort_values("trade_date").set_index("trade_date")
            target = make_forward_target(chronological, horizon=5, close_column="close")
            target = target.rename(columns={"target_5d": "target_5d"}).reset_index(names="prediction_date")
            target["symbol"] = symbol
            targets.append(target[["symbol", "prediction_date", "target_5d", "label_end_date"]])
        target_frame = pd.concat(targets, ignore_index=True)
        lineage_columns = [
            "symbol", "trade_date", "market", "exchange", "source", "adjustment", "currency",
            "ingest_id", "record_version", "revision_id", "downloaded_at", "provider_version", "row_hash",
        ]
        lineage = bars.loc[:, lineage_columns].rename(columns={
            "trade_date": "prediction_date", "record_version": "bar_record_version",
        })
        dataset = features.merge(target_frame, on=["symbol", "prediction_date"], how="left", validate="one_to_one")
        dataset = dataset.merge(lineage, on=["symbol", "prediction_date"], how="left", validate="one_to_one")
        dataset = dataset.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
        if (dataset["prediction_date"] > research_end).any():
            raise ValueError("Prediction dates exceed the configured development-period cap.")
        realized_ends = pd.to_datetime(dataset["label_end_date"], errors="coerce")
        if realized_ends.dropna().gt(research_end).any():
            raise ValueError("Five-observation labels exceed the configured development-period cap.")
    else:
        dataset = pd.DataFrame(columns=[
            "symbol", "prediction_date", *feature_columns, "target_5d", "label_end_date",
            "market", "exchange", "source", "adjustment", "ingest_id", "bar_record_version",
        ])
        bars = pd.DataFrame(columns=["symbol", "trade_date", "source", "adjustment", "row_hash", "record_version", "ingest_id"])

    coverage = pd.DataFrame(coverage_records)
    snapshot_times = ({
        str(symbol): pd.to_datetime(group["downloaded_at"], utc=True, errors="coerce").max().isoformat()
        for symbol, group in bars.groupby("symbol", sort=True)
        if "downloaded_at" in group and pd.to_datetime(group["downloaded_at"], utc=True, errors="coerce").notna().any()
    } if len(bars) else {})
    manifest: dict[str, object] = {
        "dataset_id": f"ashare_research_{uuid.uuid4().hex}",
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "pool_id": pool["pool_id"],
        "universe_hash": pool.get("universe_hash"),
        "parent_universe_hash": pool.get("parent_universe_hash"),
        "reused_universe_hash": reused_universe_hash,
        "universe_selection_seed": pool.get("selection_seed"),
        "candidate_source_hash": pool.get("candidate_source_hash"),
        "candidate_snapshot_time": pool.get("candidate_snapshot_time"),
        "pool_selection_date": pool["selection_date"],
        "pool_size": len(pool["members"]),
        "symbols_with_data": int(sum(record["rows"] > 0 for record in coverage_records)),
        "research_start": research_start.date().isoformat(),
        "research_end": research_end.date().isoformat(),
        "development_date_cap": "2023-12-31" if development_only else None,
        "adjustment": adjustment,
        "market": market,
        "price_basis": "canonical_unadjusted_vendor" if adjustment == "none" else "vendor_snapshot_adjusted",
        "provider": source,
        "source": source,
        "date_range": {"start": research_start.date().isoformat(), "end": research_end.date().isoformat()},
        "calendar": calendar.metadata,
        "feature_version": feature_version,
        "feature_columns": feature_columns,
        "feature_parameters": feature_parameters,
        "target": "target_5d = close.shift(-5) / close - 1",
        "target_horizon_observations": 5,
        "target_version": "target_5d_h5_v1",
        "adjustment_snapshot_time_by_symbol": snapshot_times,
        "mixed_vendor_snapshot_times": bool(adjustment != "none" and len(set(snapshot_times.values())) > 1),
        "point_in_time_status": "NOT_POINT_IN_TIME" if adjustment != "none" else "UNADJUSTED_VENDOR_SNAPSHOT",
        "point_in_time_limit": "No point-in-time corporate-action reconstruction; provider adjusted history is a vendor snapshot and may be revised. Unadjusted price returns exclude distributions.",
        "research_sample_policy": "BaoStock tradestatus=1 records only; tradestatus=0 retained in raw/provider metadata and excluded from feature/target observations; missing status is Unknown and excluded from research rows.",
        "development_only": True,
        "survivorship_bias_note": pool["survivorship_bias_note"],
        "dataset_rows": len(dataset),
        "feature_complete_rows": int(dataset[feature_columns].notna().all(axis=1).sum()) if len(dataset) else 0,
        "labeled_rows": int(dataset["target_5d"].notna().sum()) if len(dataset) else 0,
        "data_version": _data_version(bars) if len(bars) else hashlib.sha256(b"").hexdigest(),
        "dataset_path": str(output_path.resolve()),
        "coverage": coverage_records,
    }
    manifest["dataset_version"] = manifest["data_version"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.tmp.parquet")
    dataset.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    manifest["dataset_sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
    _write_json_atomic(manifest_path, manifest)
    return DatasetBuildResult(dataset, coverage, manifest, output_path, manifest_path)


def dataset_status(
    manifest_path: Path | str | None = None,
    dataset_path: Path | str | None = None,
) -> dict[str, object]:
    root = project_root()
    dataset_path = Path(dataset_path) if dataset_path else root / "data" / "processed" / "ashare_research_dataset_v1.parquet"
    manifest_path = Path(manifest_path) if manifest_path else dataset_path.with_suffix(".manifest.json")
    if not manifest_path.exists() or not dataset_path.exists():
        return {"status": "missing", "dataset_path": str(dataset_path), "manifest_path": str(manifest_path)}
    manifest = _read_json(manifest_path)
    manifest["status"] = "available"
    return manifest
