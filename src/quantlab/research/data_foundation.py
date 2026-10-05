"""Read-only Step 4.7 evidence audit. No market bars or model artifacts are opened."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd


ROLE = "DATA_FOUNDATION_FEASIBILITY"
PILOT_CONFIG = Path("config/step_4_7_pilot_sources.json")
SNAPSHOT_MANIFEST = Path("data/raw/security_master/20261004T145201Z/manifest.json")
SNAPSHOT_PARQUET = Path("data/validated/security_master/security_master_36b385c51dbc0c4c.parquet")
RAW_ROOT = Path("data/raw/data_foundation_feasibility")
REPORT_PATH = Path("artifacts/reports/data_foundation_feasibility_step_4_7.json")


class EvidenceError(ValueError):
    """Source evidence does not match the pinned pilot provenance."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(path: Path, *, expected_sha256: str, content_type: str) -> None:
    if not path.is_file() or sha256_file(path) != expected_sha256:
        raise EvidenceError(f"Missing or changed source: {path}")
    with path.open("rb") as stream:
        prefix = stream.read(8)
    if content_type == "application/pdf" and not prefix.startswith(b"%PDF-"):
        raise EvidenceError(f"Official document did not return a PDF: {path}")
    if content_type == "xlsx" and not prefix.startswith(b"PK"):
        raise EvidenceError(f"Exchange workbook did not return an xlsx file: {path}")
    if content_type == "text/html" and not prefix.lower().startswith(b"<html"):
        raise EvidenceError(f"Access response did not return HTML: {path}")


def listed_on(
    *,
    listing_date: str | None,
    delisting_date: str | None,
    research_date: str,
    status_at_snapshot: str,
    snapshot_date: str | None = None,
) -> str:
    """Evaluate an evidenced listing interval, without inferring trading availability.

    A current list only proves membership at its capture date. For past dates an
    open-ended interval is unknown until an independent historical roster exists.
    """
    target = date.fromisoformat(research_date)
    if not listing_date:
        return "unknown"
    start = date.fromisoformat(listing_date)
    if delisting_date:
        end = date.fromisoformat(delisting_date)
        if end <= start:
            raise EvidenceError("Delisting date must follow listing date.")
    else:
        end = None
    if target < start or (end is not None and target >= end):
        return "not_listed"
    if end is not None and status_at_snapshot == "delisted":
        return "listed_interval_not_trading_status"
    if status_at_snapshot == "current_listed" and snapshot_date:
        return "listed_at_snapshot" if target == date.fromisoformat(snapshot_date) else "unknown"
    return "unknown"


def deterministic_pilot(records: list[dict[str, object]]) -> list[dict[str, object]]:
    if len({row.get("symbol") for row in records}) != len(records):
        raise EvidenceError("Pilot contains a duplicate symbol.")
    classes = ("current_official_snapshot", "official_documented_delisted")
    chosen = []
    for source_class in classes:
        candidates = sorted(
            (row for row in records if row.get("selection_class") == source_class),
            key=lambda row: str(row["symbol"]),
        )
        if not candidates or not candidates[0].get("source_ids"):
            raise EvidenceError(f"Missing verifiable {source_class} pilot candidate.")
        chosen.append(candidates[0])
    return sorted(chosen, key=lambda row: str(row["symbol"]))


def factor_version_class(*, trade_date: str, observed_at: str, as_of_archive: str | None = None) -> str:
    """A historical factor trade date is not a publication/version timestamp."""
    date.fromisoformat(trade_date)
    datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if as_of_archive is None:
        return "historical_factor_snapshot"
    archived = datetime.fromisoformat(as_of_archive.replace("Z", "+00:00"))
    if archived.tzinfo is None:
        raise EvidenceError("An as-of archive needs an explicit timezone.")
    return "as_of_archive_candidate_requires_methodology_validation"


def _checked_probe(path: Path, *, expected_code: str, interface: str) -> tuple[dict[str, object], dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (raw.get("code") != expected_code or raw.get("interface") != interface
            or raw.get("login_error_code") != "0" or raw.get("query_error_code") != "0"):
        raise EvidenceError(f"BaoStock probe did not succeed as expected: {path}")
    fields, rows = raw.get("fields"), raw.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list) or any(len(row) != len(fields) for row in rows):
        raise EvidenceError(f"BaoStock probe has inconsistent rows: {path}")
    record = {
        "source_id": path.stem,
        "source_organization": "BaoStock",
        "interface": interface,
        "sdk_version": raw["sdk_version"],
        "retrieved_at_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "local_path": str(path),
        "sha256": sha256_file(path),
        "fields": fields,
        "row_count": len(rows),
        "error_code": raw["query_error_code"],
        "elapsed_seconds": raw["elapsed_seconds"],
        "coverage_limit": "single-symbol, current response; not a historical market roster or as-of archive",
    }
    return raw, record


def build_feasibility_report(root: Path) -> dict[str, object]:
    config = json.loads((root / PILOT_CONFIG).read_text(encoding="utf-8"))
    if config.get("classification") != ROLE:
        raise EvidenceError("Pilot configuration lost its feasibility-only role.")
    documents = config["official_documents"]
    for item in documents:
        verify_source(root / item["local_path"], expected_sha256=item["sha256"], content_type=item["content_type"])

    manifest = json.loads((root / SNAPSHOT_MANIFEST).read_text(encoding="utf-8"))
    szse = next(entry for entry in manifest["list_results"] if entry["list_id"] == "szse_a_shares")
    source_file = szse["source_files"][0]
    verify_source(Path(source_file["path"]), expected_sha256=source_file["sha256"], content_type="xlsx")
    master = pd.read_parquet(root / SNAPSHOT_PARQUET)
    current = master.loc[master["symbol"] == "000001.SZ"]
    if len(current) != 1 or not master.loc[master["symbol"] == "002618.SZ"].empty:
        raise EvidenceError("Current security snapshot pilot membership changed.")
    current_row = current.iloc[0]
    if current_row["listing_date"] != "1991-04-03" or current_row["exchange"] != "SZSE":
        raise EvidenceError("Current pilot listing metadata changed.")

    basic_delisted, basic_delisted_source = _checked_probe(
        root / RAW_ROOT / "baostock_sz_002618.json", expected_code="sz.002618", interface="query_stock_basic(code=...)")
    basic_current, basic_current_source = _checked_probe(
        root / RAW_ROOT / "baostock_sz_000001.json", expected_code="sz.000001", interface="query_stock_basic(code=...)")
    required_basic_fields = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
    if basic_delisted["fields"] != required_basic_fields or basic_current["fields"] != required_basic_fields:
        raise EvidenceError("BaoStock basic field schema changed.")
    if len(basic_delisted["rows"]) != 1 or len(basic_current["rows"]) != 1:
        raise EvidenceError("BaoStock basic probe expected exactly one row per code.")
    retired_values = dict(zip(required_basic_fields, basic_delisted["rows"][0]))
    current_values = dict(zip(required_basic_fields, basic_current["rows"][0]))
    if (retired_values["ipoDate"], retired_values["outDate"], retired_values["status"], retired_values["type"]) != ("2011-09-20", "2022-06-22", "0", "1"):
        raise EvidenceError("Retired security metadata disagrees with the official pilot documents.")
    if (current_values["ipoDate"], current_values["outDate"], current_values["status"]) != ("1991-04-03", "", "1"):
        raise EvidenceError("Current security metadata disagrees with the exchange snapshot.")

    pilot = deterministic_pilot(config["pilot_records"])
    if [row["symbol"] for row in pilot] != ["000001.SZ", "002618.SZ"]:
        raise EvidenceError("Pilot selection changed.")
    snapshot_day = manifest["metadata_snapshot_time"][:10]
    interval_checks = []
    for row in pilot:
        dates = (["1990-01-01", "2018-12-31", snapshot_day]
                 if row["symbol"] == "000001.SZ" else
                 ["2011-09-19", "2018-12-31", "2022-06-21", "2022-06-22"])
        for research_day in dates:
            interval_checks.append({"symbol": row["symbol"], "research_date": research_day,
                                    "result": listed_on(listing_date=row["listing_date"],
                                                        delisting_date=row["delisting_date"],
                                                        research_date=research_day,
                                                        status_at_snapshot=row["status_at_snapshot"],
                                                        snapshot_date=snapshot_day)})

    dividend, dividend_source = _checked_probe(
        root / RAW_ROOT / "baostock_query_dividend_data_sh_600519_2023.json",
        expected_code="sh.600519", interface="query_dividend_data")
    factor, factor_source = _checked_probe(
        root / RAW_ROOT / "baostock_query_adjust_factor_sh_600519_2023.json",
        expected_code="sh.600519", interface="query_adjust_factor")
    if not dividend["rows"] or not factor["rows"]:
        raise EvidenceError("Corporate-action pilot returned no real rows.")
    factor_row = dict(zip(factor["fields"], factor["rows"][0]))
    factor_semantics = factor_version_class(
        trade_date=factor_row["dividOperateDate"], observed_at=factor_source["retrieved_at_utc"])
    exchange_source = {
        "source_id": "szse_current_a_share_list_20261004",
        "source_organization": "Shenzhen Stock Exchange",
        "source_url": source_file["source_url"],
        "retrieved_at_utc": source_file["downloaded_at"],
        "local_path": source_file["path"],
        "sha256": source_file["sha256"],
        "fields": szse["fields"],
        "row_count": szse["reported_row_count"],
        "coverage_limit": "Current 2026 A-share snapshot; no historical delisted members or daily status history.",
    }
    report = {
        "role": ROLE,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_checkpoint_step_4_6": "e385264",
        "scope": {"legacy_test_accessed": False, "sealed_holdout_accessed": False,
                  "new_market_bars_fetched": False, "models_fitted": False, "ic_recomputed": False},
        "historical_universe": {
            "status": "historical_universe_partially_feasible",
            "pilot_selection_rule": config["selection_rule"],
            "pilot_records": pilot,
            "listed_on_checks": interval_checks,
            "baostock_observations": {"delisted": retired_values, "current": current_values,
                                       "empty_outDate_interpretation": "unknown historical delisting; current status only as observed"},
            "coverage_limit": "One official delisting and one current snapshot cannot enumerate all 2018-2023 A shares, historical relistings, or daily status.",
        },
        "pit_price": {
            "status": "pit_price_partially_feasible",
            "none_price_semantics": "unadjusted vendor history is available in the existing project; no price rows opened in this audit",
            "qfq_semantics": "vendor_snapshot_adjusted / NOT_POINT_IN_TIME",
            "factor_row": factor_row,
            "factor_version_class": factor_semantics,
            "dividend_fields": dividend["fields"],
            "dividend_row_count": len(dividend["rows"]),
            "dividend_rows": dividend["rows"],
            "reconstruction_pilot": "not_run_source_version_semantics_unverified",
            "coverage_limit": "No archived as-of factor versions or complete, versioned corporate-action event history was verified. A current historical factor cannot establish past availability.",
        },
        "source_provenance": {
            "official_documents": documents,
            "exchange_snapshot": exchange_source,
            "baostock_probes": [basic_delisted_source, basic_current_source, dividend_source, factor_source],
            "documentation_only": [
                {"source_url": "https://tushare.pro/document/2?doc_id=25", "interface": "stock_basic",
                 "fields": ["ts_code", "exchange", "list_status", "list_date", "delist_date"],
                 "row_count": None, "sha256": None, "access": "documentation_only_no_local_token",
                 "coverage_limit": "D status documented; no live response or completeness measured"},
                {"source_url": "https://tushare.pro/document/2?doc_id=262", "interface": "bak_basic",
                 "fields": ["trade_date", "ts_code", "list_date"], "row_count": None, "sha256": None,
                 "access": "documentation_only_no_local_token",
                 "coverage_limit": "Daily historical roster documented from 2016; 5000-point permission and completeness unverified"},
                {"source_url": "https://tushare.pro/document/2?doc_id=28", "interface": "adj_factor",
                 "fields": ["ts_code", "trade_date", "adj_factor"], "row_count": None, "sha256": None,
                 "access": "documentation_only_no_local_token",
                 "coverage_limit": "Trade date is not a historical publication/version timestamp"},
                {"source_url": "https://tushare.pro/document/2?doc_id=103", "interface": "dividend",
                 "fields": ["ann_date", "imp_ann_date", "ex_date", "cash_div", "stk_div"],
                 "row_count": None, "sha256": None, "access": "documentation_only_no_local_token",
                 "coverage_limit": "Announcement fields documented; event revisions and access unverified"},
            ],
        },
        "target_horizon_known_limit": {"target": "target_5d_h5_v1", "semantics": "five subsequent valid trading observations",
                                       "u100_labeled_rows": 143411, "rows_over_five_exchange_sessions": 377,
                                       "ratio": 0.002629, "max_calendar_days": 150,
                                       "max_exchange_session_distance": 100},
        "decision_matrix": {
            "historical_universe": "historical_universe_partially_feasible",
            "pit_price": "pit_price_partially_feasible",
            "combined": "data_foundation_upgrade_partial",
            "historical_data_foundation_not_verified": True,
        },
    }
    return report


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    report = build_feasibility_report(root)
    path = root / REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if stable_payload(existing) != stable_payload(report):
            raise EvidenceError(f"Immutable feasibility output differs from current evidence: {path}")
        reused = True
    else:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        reused = False
    print(json.dumps({"status": report["decision_matrix"]["combined"], "report": str(path), "reused": reused},
                     ensure_ascii=False))


def stable_payload(report: dict[str, object]) -> dict[str, object]:
    """Ignore generation time while comparing immutable evidence and decisions."""
    return {key: value for key, value in report.items() if key != "created_at_utc"}


if __name__ == "__main__":
    main()
