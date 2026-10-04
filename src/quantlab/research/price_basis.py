"""Read-only comparison of provider unadjusted and qfq price snapshots."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data.store import MarketDataStore
from quantlab.research.dataset import project_root


def compare_price_bases(
    tickers: list[str], *, provider: str, start: str | None = None, end: str | None = None,
    jump_threshold: float = 0.20, root: Path | str | None = None,
) -> dict[str, object]:
    """Compare matching none/qfq bars; jumps are diagnostics only, never corporate-action labels."""
    if jump_threshold <= 0:
        raise ValueError("jump_threshold must be positive")
    root = Path(root) if root else project_root()
    store = MarketDataStore(root / "data")
    symbols: dict[str, object] = {}
    for ticker in tickers:
        bases = {}
        for adjustment in ("none", "qfq"):
            frame = store.query(symbol=ticker, market="CN_A", start=start, end=end,
                                adjustment=adjustment, source=provider)
            if frame.empty:
                bases[adjustment] = pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume", "amount"])
            else:
                bases[adjustment] = frame.copy()
                bases[adjustment]["trade_date"] = pd.to_datetime(bases[adjustment]["trade_date"]).dt.normalize()
        none, qfq = bases["none"], bases["qfq"]
        if none.empty or qfq.empty:
            symbols[ticker] = {"status": "missing_basis", "none_rows": int(len(none)), "qfq_rows": int(len(qfq))}
            continue
        common = none.merge(qfq, on="trade_date", suffixes=("_none", "_qfq"), how="inner", validate="one_to_one")
        ratio = common["close_qfq"] / common["close_none"]
        ohlc_ratios = {}
        for field in ("open", "high", "low", "close"):
            field_ratio = common[f"{field}_qfq"] / common[f"{field}_none"]
            ohlc_ratios[field] = {
                "first": float(field_ratio.iloc[0]) if len(field_ratio) else None,
                "last": float(field_ratio.iloc[-1]) if len(field_ratio) else None,
                "median": float(field_ratio.median()) if len(field_ratio) else None,
                "min": float(field_ratio.min()) if len(field_ratio) else None,
                "max": float(field_ratio.max()) if len(field_ratio) else None,
            }
        jumps: dict[str, list[dict[str, object]]] = {}
        for basis, frame in (("none", none), ("qfq", qfq)):
            ordered = frame.sort_values("trade_date").copy()
            ordered["return_1d"] = ordered["close"].pct_change()
            flagged = ordered.loc[ordered["return_1d"].abs() >= jump_threshold]
            jumps[basis] = [{"trade_date": row.trade_date.date().isoformat(),
                             "return_1d": float(row.return_1d),
                             "diagnostic": "mechanical threshold exceedance; cause not inferred"}
                            for row in flagged.itertuples()]
        symbols[ticker] = {
            "status": "compared", "provider": provider, "none_rows": int(len(none)),
            "qfq_rows": int(len(qfq)), "common_dates": int(len(common)),
            "first_common_date": common.trade_date.min().date().isoformat() if len(common) else None,
            "last_common_date": common.trade_date.max().date().isoformat() if len(common) else None,
            "close_qfq_to_none_ratio": {
                "first": float(ratio.iloc[0]) if len(ratio) else None,
                "last": float(ratio.iloc[-1]) if len(ratio) else None,
                "median": float(ratio.median()) if len(ratio) else None,
                "min": float(ratio.min()) if len(ratio) else None,
                "max": float(ratio.max()) if len(ratio) else None,
                "max_absolute_basis_difference": float(np.max(np.abs(ratio.to_numpy() - 1))) if len(ratio) else None,
            },
            "ohlc_qfq_to_none_ratio": ohlc_ratios,
            "volume_comparison": {
                "equal_rows": int((common.volume_none == common.volume_qfq).sum()),
                "different_rows": int((common.volume_none != common.volume_qfq).sum()),
            },
            "amount_comparison": {
                "equal_rows": int((common.amount_none == common.amount_qfq).sum()),
                "different_rows": int((common.amount_none != common.amount_qfq).sum()),
            },
            "mechanical_jump_threshold": jump_threshold,
            "mechanical_jump_diagnostics": jumps,
            "interpretation_limit": "A threshold exceedance is a review flag only; this report does not identify corporate actions or repair prices.",
        }
    report: dict[str, object] = {
        "report_id": uuid.uuid4().hex, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider": provider, "market": "CN_A", "requested_start": start, "requested_end": end,
        "adjustment_modes": ["none", "qfq"], "jump_threshold": jump_threshold,
        "symbols": symbols,
    }
    output = root / "artifacts" / "reports" / f"price_basis_{report['report_id'][:12]}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(output)
    report["report_path"] = str(output.resolve())
    return report
