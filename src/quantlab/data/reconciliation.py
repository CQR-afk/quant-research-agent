"""Small-window, side-by-side provider checks; this module never merges histories."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pandas as pd

from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.providers.router import ProviderRouter


def reconcile_providers(
    symbol: str,
    *,
    provider_a: str,
    provider_b: str,
    start: date,
    end: date,
    adjustment: str = "none",
    router: ProviderRouter | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, object]:
    """Compare two provider responses over one bounded window and write a JSON report."""
    if start > end:
        raise ValueError("Reconciliation start must be on or before end.")
    identity = normalize_ashare_symbol(symbol)
    router = router or ProviderRouter()
    results: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for name in (provider_a, provider_b):
        try:
            candidate = router.candidates(market="CN_A", adjustment=adjustment, provider=name)[0]
            if not candidate.availability.available:
                raise RuntimeError(f"provider unavailable: {candidate.availability.reason or 'unknown reason'}")
            raw = candidate.provider.fetch_raw_daily_bars(identity.symbol, start, end, adjustment=adjustment)
            results[name] = candidate.provider.normalize_raw_daily_bars(
                raw, identity.symbol, adjustment, strict=False
            ).sort_values("trade_date", kind="stable")
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"

    output_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[3] / "artifacts" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"reconcile_{uuid.uuid4().hex}"
    if errors:
        report: dict[str, object] = {
            "report_id": report_id, "status": "blocked", "market": "CN_A", "symbol": identity.symbol,
            "adjustment": adjustment,
            "price_basis": "canonical_unadjusted_vendor" if adjustment == "none" else "vendor_snapshot_adjusted",
            "requested_start": start.isoformat(), "requested_end": end.isoformat(),
            "providers": [provider_a, provider_b],
            "provider_a": provider_a, "provider_b": provider_b,
            "attempted_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "provider_errors": errors,
            "successful_response_rows": {name: len(frame) for name, frame in results.items()},
            "interpretation": "Comparison incomplete because at least one provider request failed. No rows were selected, combined, or written.",
        }
        path = output_dir / f"{report_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        report["report_path"] = str(path.resolve())
        return report

    left, right = results[provider_a], results[provider_b]
    joined = left.merge(right, on="trade_date", how="outer", suffixes=("_a", "_b"), indicator=True)
    common = joined.loc[joined["_merge"] == "both"]
    fields = ("open", "high", "low", "close", "volume", "amount")
    differences: dict[str, dict[str, float | int | None]] = {}
    for field in fields:
        a, b = common[f"{field}_a"], common[f"{field}_b"]
        comparable = a.notna() & b.notna()
        absolute = (a[comparable] - b[comparable]).abs()
        relative = absolute / a[comparable].abs().replace(0, pd.NA)
        differences[field] = {
            "comparable_rows": int(comparable.sum()),
            "max_absolute_difference": float(absolute.max()) if len(absolute) else None,
            "mean_absolute_difference": float(absolute.mean()) if len(absolute) else None,
            "max_relative_difference": float(relative.max()) if len(relative) else None,
        }
    report: dict[str, object] = {
        "report_id": report_id,
        "status": "compared",
        "market": "CN_A", "symbol": identity.symbol, "adjustment": adjustment,
        "price_basis": "canonical_unadjusted_vendor" if adjustment == "none" else "vendor_snapshot_adjusted",
        "requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "compared_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "providers": [provider_a, provider_b],
        "provider_a": provider_a, "provider_b": provider_b,
        "row_counts": {provider_a: len(left), provider_b: len(right)},
        "common_dates": int((joined["_merge"] == "both").sum()),
        "only_in_a_dates": sorted(pd.to_datetime(joined.loc[joined["_merge"] == "left_only", "trade_date"]).dt.date.astype(str).tolist()),
        "only_in_b_dates": sorted(pd.to_datetime(joined.loc[joined["_merge"] == "right_only", "trade_date"]).dt.date.astype(str).tolist()),
        "field_differences": differences,
        "interpretation": "Diagnostic comparison only. Rows are not selected, combined, or written to the canonical store.",
    }
    report["missing_in_a_dates"] = report["only_in_b_dates"]
    report["missing_in_b_dates"] = report["only_in_a_dates"]
    path = output_dir / f"{report['report_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["report_path"] = str(path.resolve())
    return report
