"""Step 4.8A: bounded, read-only historical security coverage audit.

This module never opens market bars, model outputs, or evaluation periods.
Annual exchange books and single-stock notices are evidence, not a daily roster.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import csv
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd


CONFIG = Path("config/step_4_8A_historical_sources.json")
CURRENT_MASTER = Path("data/validated/security_master/security_master_36b385c51dbc0c4c.parquet")
REPORT = Path("artifacts/reports/historical_universe_coverage_step_4_8A.json")
ROLE = "HISTORICAL_UNIVERSE_COVERAGE_AUDIT"
DATES = ("2018-12-28", "2020-12-31", "2022-12-30", "2023-12-29")


class CoverageEvidenceError(ValueError):
    """An input is malformed, ambiguous, or no longer matches its pinned source."""


def normalize_symbol(value: str) -> tuple[str, str]:
    """Normalize a six-digit A-share identifier without stripping leading zeroes."""
    match = re.fullmatch(r"(?:(sh|sz)\.)?(\d{6})(?:\.(SH|SZ))?", value, re.IGNORECASE)
    if not match:
        raise CoverageEvidenceError(f"Invalid A-share security code: {value!r}")
    prefix, digits, suffix = match.groups()
    exchange = "SSE" if digits.startswith(("5", "6", "9")) else "SZSE" if digits.startswith(("0", "1", "2", "3")) else None
    if exchange is None or (prefix and prefix.lower() != ("sh" if exchange == "SSE" else "sz")) or (suffix and suffix.upper() != ("SH" if exchange == "SSE" else "SZ")):
        raise CoverageEvidenceError(f"A-share code/exchange mismatch: {value!r}")
    return f"{digits}.{'SH' if exchange == 'SSE' else 'SZ'}", exchange


def normalize_delisted(raw: dict[str, object]) -> dict[str, object]:
    symbol, exchange = normalize_symbol(str(raw["symbol"]))
    if raw.get("exchange") != exchange:
        raise CoverageEvidenceError(f"Exchange mismatch for {symbol}")
    listing = date.fromisoformat(str(raw["listing_date"]))
    ending = date.fromisoformat(str(raw["delisting_date"])) if raw.get("delisting_date") else None
    if ending and ending <= listing:
        raise CoverageEvidenceError(f"Invalid delisting interval for {symbol}")
    sources = raw.get("source_ids")
    if not isinstance(sources, list) or not sources or len(set(sources)) != len(sources):
        raise CoverageEvidenceError(f"Missing or duplicate provenance for {symbol}")
    return {**raw, "symbol": symbol, "exchange": exchange,
            "listing_date": listing.isoformat(), "delisting_date": ending.isoformat() if ending else None,
            "listing_interval_status": "bounded_interval_evidence" if ending else "open_end_unknown_historical_status",
            "trading_status_known": False, "status_source": list(sources)}


def listing_interval_status(record: dict[str, object], on_date: str) -> str:
    """A bounded listing interval says nothing about trading or suspension."""
    day = date.fromisoformat(on_date)
    start = date.fromisoformat(str(record["listing_date"])) if record.get("listing_date") else None
    end = date.fromisoformat(str(record["delisting_date"])) if record.get("delisting_date") else None
    if start is None:
        return "unknown"
    if day < start or (end and day >= end):
        return "outside_listing_interval"
    if end is None:
        return "unknown"
    return "member_of_listing_interval"


def deterministic_pilot(records: list[dict[str, object]], per_exchange: int = 2) -> list[dict[str, object]]:
    normalized = [normalize_delisted(record) for record in records]
    if len({record["symbol"] for record in normalized}) != len(normalized):
        raise CoverageEvidenceError("Duplicate security in historical pilot candidates")
    result: list[dict[str, object]] = []
    for exchange in ("SSE", "SZSE"):
        result.extend(sorted((row for row in normalized if row["exchange"] == exchange),
                             key=lambda row: str(row["symbol"]))[:per_exchange])
    return result


def compare_pilot_with_current(historical: set[str], current: set[str]) -> dict[str, object]:
    """Set comparison is explicitly scoped to documented pilot symbols."""
    return {"scope": "documented_pilot_only_not_full_historical_roster",
            "historical_only_symbols": sorted(historical - current),
            "current_only_count": len(current - historical),
            "overlap_symbols": sorted(historical & current),
            "delisted_documented_count": len(historical - current),
            "unknown_historical_population_count": None}


def date_coverage_report(dates: tuple[str, ...] = DATES, rosters: dict[str, list[dict[str, object]]] | None = None) -> list[dict[str, object]]:
    """Leave all daily coverage counts unknown until real dated rosters exist."""
    rosters = rosters or {}
    result = []
    for day in dates:
        date.fromisoformat(day)
        rows = rosters.get(day)
        if rows is None:
            result.append({"roster_date": day, "status": "daily_roster_unavailable", "total": None,
                           "sse": None, "szse": None, "still_current": None, "later_delisted": None,
                           "unknown_status": None, "duplicate_count": None, "source_coverage": "not_measured"})
            continue
        symbols = [normalize_symbol(str(row["symbol"])) for row in rows]
        result.append({"roster_date": day, "status": "fixture_or_source_supplied_requires_provenance",
                       "total": len(rows), "sse": sum(ex == "SSE" for _, ex in symbols),
                       "szse": sum(ex == "SZSE" for _, ex in symbols),
                       "still_current": None, "later_delisted": None, "unknown_status": None,
                       "duplicate_count": len(rows) - len(set(symbols)), "source_coverage": "unverified"})
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(root: Path, source: dict[str, object]) -> None:
    if not source.get("organization") or not str(source.get("url", "")).startswith(("https://", "http://")):
        raise CoverageEvidenceError("Source organization or URL is missing")
    path = root / str(source["path"])
    if not path.is_file() or sha256_file(path) != source.get("sha256"):
        raise CoverageEvidenceError(f"Source hash mismatch or missing file: {path}")
    prefix = path.open("rb").read(5)
    content_type = str(source["content_type"])
    valid = ((content_type == "application/pdf" and prefix.startswith(b"%PDF-"))
             or (content_type == "text/html" and prefix.lower().startswith((b"<html", b"<!doc")))
             or (content_type == "application/json" and prefix.lstrip().startswith(b"{")))
    if not valid:
        raise CoverageEvidenceError(f"Unexpected source format: {path}")


def tushare_credential_status(root: Path, environ: dict[str, str] | None = None) -> str:
    names = ("TUSHARE_TOKEN", "TUSHARE_API_TOKEN", "TS_TOKEN")
    environment = os.environ if environ is None else environ
    if any(environment.get(name) for name in names):
        return "credential_present_live_metadata_pilot_required"
    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                if name.strip() in names and value.strip().strip("'\""):
                    return "credential_present_live_metadata_pilot_required"
    return "credential_unavailable"


def _vendor_row(root: Path, path: str, expected: str) -> dict[str, str]:
    raw = json.loads((root / path).read_text(encoding="utf-8"))
    if raw.get("code") != expected or raw.get("login_error_code") != "0" or raw.get("query_error_code") != "0":
        raise CoverageEvidenceError(f"BaoStock query failed: {expected}")
    fields, rows = raw.get("fields"), raw.get("rows")
    if fields != ["code", "code_name", "ipoDate", "outDate", "type", "status"] or not isinstance(rows, list) or len(rows) != 1 or len(rows[0]) != len(fields):
        raise CoverageEvidenceError(f"BaoStock metadata shape changed: {expected}")
    values = dict(zip(fields, rows[0]))
    if values["code"] != expected:
        raise CoverageEvidenceError(f"BaoStock returned a different security: {expected}")
    return {key: str(values[key]) for key in ("code", "ipoDate", "outDate", "type", "status")}


def build_report(root: Path) -> dict[str, object]:
    config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    if config.get("role") != ROLE:
        raise CoverageEvidenceError("Invalid audit role")
    sources = config["sources"]
    if len({item["source_id"] for item in sources}) != len(sources):
        raise CoverageEvidenceError("Duplicate source ID")
    for item in sources:
        verify_source(root, item)
    source_ids = {item["source_id"] for item in sources}
    pilot = deterministic_pilot(config["pilot_records"])
    if any(not set(row["source_ids"]).issubset(source_ids) for row in pilot):
        raise CoverageEvidenceError("Pilot refers to an unverified source")
    for row in pilot:
        prefix = "sh" if row["exchange"] == "SSE" else "sz"
        code = f"{prefix}.{str(row['symbol'])[:6]}"
        vendor = _vendor_row(root, next(str(item["path"]) for item in sources if item["source_id"] == f"baostock_{prefix}_{str(row['symbol'])[:6]}"), code)
        if vendor["ipoDate"] != row["listing_date"] or vendor["outDate"] != row["delisting_date"] or vendor["type"] != "1" or vendor["status"] != "0":
            raise CoverageEvidenceError(f"Official pilot and vendor metadata disagree: {row['symbol']}")
        row["vendor_cross_check"] = vendor
    master = pd.read_parquet(root / CURRENT_MASTER, columns=["symbol", "exchange"])
    if master["symbol"].duplicated().any():
        raise CoverageEvidenceError("Current master has duplicate symbols")
    current = set(master["symbol"].astype(str))
    pilot_symbols = {str(row["symbol"]) for row in pilot}
    if pilot_symbols & current:
        raise CoverageEvidenceError("A documented delisted pilot code unexpectedly appears in the current snapshot")
    source_years = {"2018": "not_retrieved", "2019": "not_retrieved", "2020": "not_retrieved",
                    "2021": "not_retrieved", "2022": "SSE_annual_pilot_only", "2023": "SZSE_annual_delisting_pilot_only"}
    credential = tushare_credential_status(root)
    return {
        "role": ROLE, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_checkpoint_step_4_7": "d24055a",
        "step_4_7_immutable": {"historical_universe": "historical_universe_partially_feasible",
                               "pit_price": "pit_price_partially_feasible", "combined": "data_foundation_upgrade_partial",
                               "historical_data_foundation_not_verified": True},
        "scope": {"market_bars_opened": False, "models_trained": False, "ic_recomputed": False,
                  "legacy_test_accessed": False, "sealed_holdout_accessed": False,
                  "pit_price_reconstructed": False, "model_universe_created": False},
        "source_matrix": sources,
        "source_coverage_by_year": source_years,
        "tushare": {"credential_status": credential, "live_call_made": False,
                    "stock_basic": {"url": "https://tushare.pro/document/2?doc_id=25", "documented_fields": ["ts_code", "exchange", "list_status", "list_date", "delist_date"], "documented_delisted_filter": "list_status=D", "actual_rows": None, "sha256": None},
                    "bak_basic": {"url": "https://tushare.pro/document/2?doc_id=262", "documented_fields": ["trade_date", "ts_code", "list_date"], "documented_since_year": 2016, "documented_max_rows": 7000, "documented_points_required": 5000, "actual_rows": None, "sha256": None, "pit_semantics_verified": False}},
        "pilot_selection_rule": config["selection_rule"], "official_delisted_pilot": pilot,
        "listing_checks": [{"symbol": row["symbol"], "date": day,
                            "listing_interval_status": listing_interval_status(row, day),
                            "trading_status_known": False, "status_source": row["status_source"]}
                           for row in pilot for day in DATES],
        "dated_roster_coverage": date_coverage_report(),
        "current_2026_master": {"symbol_count": len(current), "sse": int((master["exchange"] == "SSE").sum()),
                                "szse": int((master["exchange"] == "SZSE").sum()),
                                "snapshot_role": "current_only"},
        "current_vs_historical": compare_pilot_with_current(pilot_symbols, current),
        "historical_universe_pilot_artifact": "not_created_daily_coverage_unverified",
        "final_status": "historical_universe_coverage_partial",
        "remaining_unknowns": ["No full 2018-2023 daily SSE/SZSE A-share roster was obtained.",
                               "Annual statistics and delisting lists do not prove membership or tradability on arbitrary dates.",
                               "Only three independently checked delisted codes were compared with the 2026 master.",
                               "The SSE pilot exact IPO/delist dates are not fully independently verified from locally retrievable official primary records.",
                               "Tushare stock_basic/bak_basic response, permissions, date semantics, and completeness remain untested without a credential."]
    }


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    report = build_report(root)
    output = root / REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    csv_path = output.with_suffix(".csv")
    fields = ("symbol", "exchange", "security_type", "listing_date", "listing_date_validation",
              "last_trading_date", "delisting_date", "delisting_date_validation",
              "listing_interval_status", "trading_status_known", "status_source")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in report["official_delisted_pilot"]:
            writer.writerow({**record, "status_source": ";".join(record["source_ids"]),
                             "listing_interval_status": record["listing_interval_status"],
                             "trading_status_known": record["trading_status_known"]})
    print(json.dumps({"status": report["final_status"], "report": str(output),
                      "pilot_symbols": [row["symbol"] for row in report["official_delisted_pilot"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
