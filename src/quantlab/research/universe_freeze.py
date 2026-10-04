"""Outcome-independent, deterministic breadth-universe freeze helpers."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SELECTION_SEED = "quantlab-step4.5R2-2026-10-05-v1"
SELECTION_RULE_VERSION = "exchange-industry-hash-round-robin-v1"
RESEARCH_LISTING_CUTOFF = date(2017, 12, 31)
UNIVERSE_SIZES = (50, 100)


class UniverseFreezeError(ValueError):
    """Raised when metadata cannot support a defensible freeze."""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to replace frozen manifest: {path}")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_and_freeze_breadth_universes(
    security_master: pd.DataFrame,
    *,
    anchor_config_path: Path | str,
    candidate_snapshot_time: str,
    source_hash: str,
    source_files: list[dict[str, Any]],
    output_root: Path | str,
    seed: str = SELECTION_SEED,
) -> dict[str, Any]:
    """Freeze U50/U100 using official-list metadata only; no returns are read."""
    required = {"market", "symbol", "exchange", "security_type", "listing_date", "industry", "name"}
    missing = required - set(security_master.columns)
    if missing:
        raise UniverseFreezeError(f"Security Master is missing fields: {', '.join(sorted(missing))}.")
    master = security_master.copy()
    if master["symbol"].isna().any() or master["symbol"].duplicated().any():
        raise UniverseFreezeError("Security Master contains missing or duplicate symbols.")
    anchor_payload = json.loads(Path(anchor_config_path).read_text(encoding="utf-8"))
    anchors = anchor_payload.get("members", [])
    anchor_symbols = [str(item["symbol"]) for item in anchors]
    if len(anchor_symbols) != 11 or len(set(anchor_symbols)) != 11:
        raise UniverseFreezeError("The configured U11 anchor pool must contain exactly 11 unique symbols.")
    by_symbol = master.set_index("symbol", drop=False)
    missing_anchors = [symbol for symbol in anchor_symbols if symbol not in by_symbol.index]
    if missing_anchors:
        raise UniverseFreezeError(f"Official current lists do not contain anchor(s): {', '.join(missing_anchors)}.")

    permitted_types = {
        "A_share_main_board", "A_share_star_market", "A_share_growth_enterprise_market", "A_share_common",
    }
    rejects: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    anchors_set = set(anchor_symbols)
    for row in master.to_dict("records"):
        symbol = str(row["symbol"])
        if row["market"] != "CN_A" or row["exchange"] not in {"SSE", "SZSE"}:
            reason = "not_an_exchange_verified_cn_a_share"
        elif row["security_type"] not in permitted_types:
            reason = "security_type_not_verified_as_common_a_share"
        elif symbol in anchors_set:
            continue
        else:
            listing = pd.to_datetime(row["listing_date"], errors="coerce")
            if pd.isna(listing):
                reason = "official_listing_date_missing; observed_history_start_unavailable_without_price_data"
            elif listing.date() > RESEARCH_LISTING_CUTOFF:
                reason = "listed_after_2017-12-31; development_history_priority_rule"
            else:
                industry_value = row.get("industry")
                industry = "UNKNOWN" if pd.isna(industry_value) else str(industry_value).strip() or "UNKNOWN"
                eligible.append({
                    "symbol": symbol,
                    "name": row.get("name"),
                    "exchange": row["exchange"],
                    "industry": industry,
                    "security_type": row["security_type"],
                    "listing_date": listing.date().isoformat(),
                    "selection_hash": _hash(symbol + seed),
                })
                continue
        rejects.append({"symbol": symbol, "reason": reason})

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in eligible:
        groups[(candidate["exchange"], candidate["industry"])].append(candidate)
    for group in groups.values():
        group.sort(key=lambda item: (item["selection_hash"], item["symbol"]))
    group_keys = sorted(groups, key=lambda key: _hash(f"{seed}|{key[0]}|{key[1]}"))
    ranked: list[dict[str, Any]] = []
    while any(groups[key] for key in group_keys):
        for key in group_keys:
            if groups[key]:
                ranked.append(groups[key].pop(0))

    if len(ranked) < 89:
        raise UniverseFreezeError(f"Only {len(ranked)} new securities meet listing eligibility; 89 are required for U100.")
    selected_additions = ranked[:89]
    deferred = [{"symbol": item["symbol"], "reason": "deterministic_rank_after_u100_cutoff"}
                for item in ranked[89:]]
    rejects.extend(deferred)

    def member_for(symbol: str, anchor: bool) -> dict[str, Any]:
        row = by_symbol.loc[symbol]
        return {
            "symbol": symbol, "name": row["name"], "exchange": row["exchange"],
            "industry": row["industry"] if pd.notna(row["industry"]) else None,
            "security_type": row["security_type"],
            "listing_date": row["listing_date"] if pd.notna(row["listing_date"]) else None,
            "eligibility_status": "anchor_exception_preserved" if anchor else "listing_date_on_or_before_2017-12-31",
            "metadata_source": row["metadata_source"],
        }

    addition_members = [member_for(item["symbol"], False) for item in selected_additions]
    u11_members = [member_for(symbol, True) for symbol in anchor_symbols]
    now = datetime.now(timezone.utc).isoformat()
    common = {
        "schema_version": "1.0", "universe_type": "deterministic_breadth_engineering_universe",
        "selection_rule_version": SELECTION_RULE_VERSION, "selection_seed": seed,
        "anchor_pool": {"pool_id": anchor_payload["pool_id"], "symbols": anchor_symbols,
                        "source_path": str(Path(anchor_config_path).resolve())},
        "candidate_source": "official SSE Main Board/STAR and SZSE A-share current listing snapshots",
        "candidate_snapshot_time": candidate_snapshot_time, "candidate_source_hash": source_hash,
        "candidate_source_files": source_files,
        "listing_date_cutoff_for_new_members": "2017-12-31",
        "anchor_exception_rule": "all original U11 symbols retained regardless of listing date",
        "selection_inputs": ["market", "exchange", "security_type", "listing_date", "industry", "symbol"],
        "future_return_used": False,
        "survivorship_limitation_code": "SURVIVORSHIP_LIMITED_PROVIDER_UNIVERSE",
        "survivorship_bias_status": "unresolved_current_snapshot_only_not_point_in_time",
        "created_at": now,
        "rejection_ledger_path": "data/validated/security_master/universe_candidate_rejections.json",
        "candidate_order_path": "data/validated/security_master/universe_candidate_order.json",
    }
    root = Path(output_root)
    output: dict[str, Any] = {}
    for size in UNIVERSE_SIZES:
        additions = addition_members[:size - 11]
        members = u11_members + additions
        symbols = [item["symbol"] for item in members]
        if len(symbols) != size or len(set(symbols)) != size or not set(anchor_symbols).issubset(symbols):
            raise UniverseFreezeError(f"U{size} failed cardinality/anchor nesting checks.")
        pool_hash = _hash("\n".join(symbols))
        payload = {
            **common,
            "pool_id": f"ashare_breadth_{size}_{pool_hash[:12]}",
            "universe_size": size,
            "universe_hash": pool_hash,
            "anchor_pool_hash": _hash("\n".join(anchor_symbols)),
            "selection_date": candidate_snapshot_time[:10],
            "market": "CN_A",
            "research_start": "2018-01-01", "research_end": "2023-12-31",
            "purpose": "Metadata-frozen candidate universe for a deterministic breadth engineering study; not market representative or PIT.",
            "survivorship_bias_note": "Current exchange listings are not a 2018-2023 point-in-time security master; delisted historical names may be absent.",
            "members": members,
            "selection_order": [item["symbol"] for item in addition_members],
            "candidate_rejection_count": len(rejects),
        }
        path = root / "config" / f"ashare_universe_breadth_{size}.json"
        _json_write_atomic(path, payload)
        output[str(size)] = {"path": str(path.resolve()), "pool_id": payload["pool_id"],
                             "universe_hash": pool_hash, "symbols": symbols}

    full_freeze = {
        **common,
        "status": "metadata_ready_for_universe_freeze",
        "candidate_count_total": len(master),
        "eligible_new_candidate_count": len(eligible),
        "selected_new_candidate_count": len(selected_additions),
        "rejected_or_deferred_count": len(rejects),
        "exchange_distribution_u100": pd.Series([x["exchange"] for x in u11_members + addition_members]).value_counts().to_dict(),
        "industry_distribution_u100": pd.Series([str(x["industry"] or "UNKNOWN") for x in u11_members + addition_members]).value_counts().to_dict(),
        "u11": output["50"]["symbols"][:11],
        "u50": output["50"], "u100": output["100"],
        "candidate_order": [item["symbol"] for item in ranked],
        "candidate_order_sha256": _hash("\n".join(item["symbol"] for item in ranked)),
        "selected_u100_candidate_order": [item["symbol"] for item in selected_additions],
        "candidate_records": selected_additions,
        "rejections": rejects,
        "replacement_status": "not_applicable_before_price_data_quality_validation",
        "replacement_rule": "if a selected symbol later fails preregistered data/security checks, advance to the next eligible symbol in candidate_order_path; preserve selected/rejected/replacement reasons",
        "metadata_fields_not_inferred": ["delisting_status_from_prices", "historical_point_in_time_membership"],
    }
    _json_write_atomic(root / "data" / "validated" / "security_master" / "universe_candidate_order.json",
                       {"candidate_order": ranked, "candidate_order_sha256": full_freeze["candidate_order_sha256"],
                        "eligible_new_candidate_count": len(ranked), "u100_selected_count": len(selected_additions)})
    _json_write_atomic(root / "data" / "validated" / "security_master" / "universe_candidate_rejections.json",
                       {"rejections": rejects, "rejected_or_deferred_count": len(rejects)})
    _json_write_atomic(root / "data" / "validated" / "security_master" / f"universe_freeze_{_hash(seed)[:12]}.json",
                       full_freeze)
    return full_freeze
