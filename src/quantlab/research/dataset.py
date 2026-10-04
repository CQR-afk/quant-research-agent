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
from quantlab.research.features import FEATURE_COLUMNS, FEATURE_PARAMETERS, FEATURE_VERSION, FeatureBuilder


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
    output_path: Path | str | None = None,
) -> DatasetBuildResult:
    """Create per-security causal features and five-observation labels from DuckDB."""
    root = project_root()
    pool_path = Path(pool_path) if pool_path else root / "config" / "ashare_universe.json"
    config_path = Path(research_config_path) if research_config_path else root / "config" / "research.json"
    pool, config = _read_json(pool_path), _read_json(config_path)
    research_start = pd.Timestamp(start or pool["research_start"]).normalize()
    research_end = pd.Timestamp(end or pool["research_end"]).normalize()
    if research_start > research_end:
        raise ValueError("Research start must be on or before end.")
    adjustment, source = adjustment or str(config["adjustment"]), str(config["source"])
    calendar = AShareTradingCalendar(research_start, research_end)
    store = store or MarketDataStore(root / "data")
    output_path = Path(output_path) if output_path else root / "data" / "processed" / "ashare_research_dataset_v1.parquet"
    manifest_path = output_path.with_suffix(".manifest.json")

    bars_by_symbol: list[pd.DataFrame] = []
    coverage_records: list[dict[str, object]] = []
    for member in pool["members"]:
        identity = normalize_ashare_symbol(str(member["symbol"]))
        if member.get("exchange") != identity.exchange:
            raise ValueError(f"Pool exchange mismatch for {identity.symbol}: expected {identity.exchange}.")
        bars = store.query(symbol=identity.symbol, market="CN_A", start=research_start.date(),
                           end=research_end.date(), adjustment=adjustment, source=source)
        if bars.empty:
            coverage_records.append({
                **member, "rows": 0, "first_date": None, "last_date": None,
                "expected_sessions": 0, "missing_sessions": None, "coverage_ratio": 0.0,
                "max_consecutive_missing": None, "unexpected_dates": 0,
                "history_start_status": "no_data; listing date and provider coverage are unknown",
                "coverage_status": "no_data",
            })
            continue

        bars = bars.copy()
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.normalize()
        if bars["adjustment"].nunique(dropna=False) != 1 or bars["adjustment"].iloc[0] != adjustment:
            raise ValueError(f"Mixed or unexpected adjustment mode for {identity.symbol}.")
        expected = calendar.sessions_between(bars["trade_date"].min(), bars["trade_date"].max())
        observed = set(bars["trade_date"])
        missing = expected.difference(pd.DatetimeIndex(sorted(observed)))
        unexpected = [day for day in observed if not calendar.is_session(day)]
        coverage_ratio = len(expected.intersection(pd.DatetimeIndex(sorted(observed)))) / len(expected) if len(expected) else 0.0
        if unexpected:
            coverage_status = "calendar_mismatch"
        elif 1 - coverage_ratio > float(config["maximum_missing_session_ratio"]):
            coverage_status = "gappy"
        else:
            coverage_status = "within_threshold"
        actual_start = bars["trade_date"].min()
        if actual_start > research_start:
            history_status = "partial; pre-first-bar interval may be pre-listing or source gap and is not inferred"
        else:
            history_status = "starts at research boundary; listing history still not independently verified"
        coverage_records.append({
            **member,
            "rows": len(bars),
            "first_date": actual_start.date().isoformat(),
            "last_date": bars["trade_date"].max().date().isoformat(),
            "expected_sessions": len(expected),
            "missing_sessions": len(missing),
            "missing_ratio": 1 - coverage_ratio if len(expected) else None,
            "coverage_ratio": coverage_ratio,
            "max_consecutive_missing": _max_gap_days(expected, observed),
            "missing_dates_sample": [day.date().isoformat() for day in missing[:20]],
            "unexpected_dates": len(unexpected),
            "history_start_status": history_status,
            "coverage_status": coverage_status,
            "source_versions": sorted(bars["provider_version"].dropna().astype(str).unique().tolist()),
            "adjustments": sorted(bars["adjustment"].dropna().astype(str).unique().tolist()),
        })
        bars_by_symbol.append(bars)

    if bars_by_symbol:
        bars = pd.concat(bars_by_symbol, ignore_index=True)
        if bars.duplicated(["symbol", "trade_date"]).any():
            raise ValueError("Research bars contain duplicate symbol/prediction dates.")
        features = FeatureBuilder().build(bars)
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
    else:
        dataset = pd.DataFrame(columns=[
            "symbol", "prediction_date", *FEATURE_COLUMNS, "target_5d", "label_end_date",
            "market", "exchange", "source", "adjustment", "ingest_id", "bar_record_version",
        ])
        bars = pd.DataFrame(columns=["symbol", "trade_date", "source", "adjustment", "row_hash", "record_version", "ingest_id"])

    coverage = pd.DataFrame(coverage_records)
    manifest: dict[str, object] = {
        "dataset_id": f"ashare_research_{uuid.uuid4().hex}",
        "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "pool_id": pool["pool_id"],
        "pool_selection_date": pool["selection_date"],
        "pool_size": len(pool["members"]),
        "symbols_with_data": int(sum(record["rows"] > 0 for record in coverage_records)),
        "research_start": research_start.date().isoformat(),
        "research_end": research_end.date().isoformat(),
        "adjustment": adjustment,
        "source": source,
        "calendar": calendar.metadata,
        "feature_version": FEATURE_VERSION,
        "feature_columns": FEATURE_COLUMNS,
        "feature_parameters": FEATURE_PARAMETERS,
        "target": "target_5d = close.shift(-5) / close - 1",
        "target_horizon_observations": 5,
        "point_in_time_limit": "Current qfq histories are exploratory; no point-in-time corporate-action reconstruction.",
        "survivorship_bias_note": pool["survivorship_bias_note"],
        "dataset_rows": len(dataset),
        "feature_complete_rows": int(dataset[FEATURE_COLUMNS].notna().all(axis=1).sum()) if len(dataset) else 0,
        "labeled_rows": int(dataset["target_5d"].notna().sum()) if len(dataset) else 0,
        "data_version": _data_version(bars) if len(bars) else hashlib.sha256(b"").hexdigest(),
        "dataset_path": str(output_path.resolve()),
        "coverage": coverage_records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.tmp.parquet")
    dataset.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
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
