"""Post-hoc attribution using only frozen Step 4.5C development artifacts."""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from quantlab.research.breadth_study import (
    DATASET_NAMES, FEATURE_COLUMNS, UNIVERSES, _json, _sha256,
    validate_frozen_universes, verify_saved_breadth_report,
)
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.walkforward_splits import DEVELOPMENT_CUTOFF, validate_development_dates

DIAGNOSTIC_ROLE = "POST_HOC_DIAGNOSTIC"
UNIVARIATE_ROLE = "EXPLORATORY_UNIVARIATE_DIAGNOSTIC"
INDUSTRY_WARNING = "CURRENT_INDUSTRY_CLASSIFICATION_NOT_HISTORICAL_PIT"
RUN_ID = "20261005T021422Z_83390a3990"
DIAGNOSTIC_OUTPUT_ID = "20261005T021422Z_83390a3990_final_v2"
REPORT_RELATIVE = Path("artifacts/reports/breadth_robustness_20261005T021422Z_83390a3990.json")
LEGACY_ID = "20261004T120849Z_2cb6448746"


def verify_immutable_file(path: Path, expected_sha256: str) -> None:
    if not path.is_file() or _sha256(path) != expected_sha256:
        raise ValueError(f"Immutable artifact changed or is missing: {path}")


def validate_prediction_input_alignment(predictions: pd.DataFrame, feature_inputs: pd.DataFrame) -> None:
    if len(predictions) != len(feature_inputs) or predictions.duplicated(["symbol", "prediction_date"]).any():
        raise ValueError("Saved prediction and feature input rows are not aligned.")
    if set(feature_inputs.columns) != set(FEATURE_COLUMNS):
        raise ValueError("Saved prediction feature schema is not the frozen Feature v1 schema.")


def validate_no_trade_exclusion(consistency: dict) -> dict:
    status = consistency["trade_status"]
    if status.get("provider_reported_nontrading") != 0 or status.get("unknown") != 0:
        raise ValueError("Current development research samples contain no-trade or unknown-status rows.")
    return {"sampled_actual_trading_rows": status["trading"],
            "sampled_provider_no_trade_rows": status["provider_reported_nontrading"],
            "sampled_unknown_status_rows": status["unknown"],
            "provider_no_trade_sessions_excluded": consistency["provider_reported_no_trade_sessions"]}


def session_horizon(prediction_date: pd.Timestamp, label_end_date: pd.Timestamp,
                    sessions: pd.DatetimeIndex) -> int:
    left = int(sessions.searchsorted(pd.Timestamp(prediction_date).normalize(), side="right"))
    right = int(sessions.searchsorted(pd.Timestamp(label_end_date).normalize(), side="right"))
    if left >= right:
        raise ValueError("Label end must follow prediction date on the exchange session calendar.")
    return right - left


def write_json_exclusive(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)


def _corr(x: pd.Series, y: pd.Series, method: str = "spearman") -> float | None:
    pair = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).replace(
        [np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 3 or pair.x.nunique() < 2 or pair.y.nunique() < 2:
        return None
    value = spearmanr(pair.x, pair.y).statistic if method == "spearman" else pair.x.corr(pair.y)
    return float(value) if np.isfinite(value) else None


def _rank_ic_for_group(actual: pd.Series, score: pd.Series, minimum: int = 3) -> float | None:
    pair = pd.DataFrame({"y": pd.to_numeric(actual, errors="coerce"),
                         "p": pd.to_numeric(score, errors="coerce")}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < minimum or pair.y.nunique() < 2 or pair.p.nunique() < 2:
        return None
    result = spearmanr(pair.y, pair.p).statistic
    return float(result) if np.isfinite(result) else None


def daily_ic(predictions: pd.DataFrame, score_column: str, value_column: str) -> pd.DataFrame:
    rows = []
    for date, group in predictions.groupby("prediction_date", sort=True):
        rows.append({"prediction_date": pd.Timestamp(date),
                     "rank_ic": _rank_ic_for_group(group[value_column], group[score_column]),
                     "sample_count": int(group[[value_column, score_column]].dropna().shape[0])})
    return pd.DataFrame(rows)


def ic_distribution(daily: pd.DataFrame, *, lower_tail_fraction: float = .05) -> tuple[dict, pd.DataFrame]:
    values = pd.to_numeric(daily["rank_ic"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    positive, negative = values[values > 0], values[values < 0]
    tail_n = max(1, int(math.ceil(len(values) * lower_tail_fraction))) if len(values) else 0
    ordered = values.sort_values(kind="stable")
    tail = ordered.head(tail_n)
    positive_sum = float(positive.sum()) if len(positive) else 0.0
    negative_sum = float(negative.sum()) if len(negative) else 0.0
    summary = {
        "valid_dates": int(len(values)), "mean": float(values.mean()) if len(values) else None,
        "median": float(values.median()) if len(values) else None,
        "std": float(values.std(ddof=1)) if len(values) > 1 else None,
        "quantiles": {f"p{q:02d}": float(values.quantile(q / 100)) for q in (1, 5, 10, 90, 95, 99)} if len(values) else {},
        "positive_day_ratio": float((values > 0).mean()) if len(values) else None,
        "negative_day_ratio": float((values < 0).mean()) if len(values) else None,
        "zero_day_ratio": float((values == 0).mean()) if len(values) else None,
        "positive_magnitude": {"count": len(positive), "mean": float(positive.mean()) if len(positive) else None,
                               "median": float(positive.median()) if len(positive) else None,
                               "p90": float(positive.quantile(.9)) if len(positive) else None,
                               "mean_contribution_per_fold_date": positive_sum / len(values) if len(values) else None},
        "negative_magnitude": {"count": len(negative), "absolute_mean": float(negative.abs().mean()) if len(negative) else None,
                               "absolute_median": float(negative.abs().median()) if len(negative) else None,
                               "absolute_p90": float(negative.abs().quantile(.9)) if len(negative) else None,
                               "mean_contribution_per_fold_date": negative_sum / len(values) if len(values) else None},
        "negative_lower_tail": {"selection": f"worst {lower_tail_fraction:.0%} of all valid dates",
                                "dates": len(tail), "sum_ic": float(tail.sum()) if len(tail) else None,
                                "mean_contribution_per_fold_date": float(tail.sum() / len(values)) if len(values) else None,
                                "fold_mean_ic": float(values.mean()) if len(values) else None,
                                "tail_mean_contribution_share_of_fold_mean": float((tail.sum() / len(values)) / values.mean())
                                if len(values) and abs(values.mean()) > 1e-15 else None},
    }
    extremes = daily.dropna(subset=["rank_ic"]).sort_values("rank_ic", kind="stable").head(10).copy()
    extremes["rank_ic"] = pd.to_numeric(extremes["rank_ic"])
    return summary, extremes


def _describe_numeric(values: pd.Series) -> dict:
    x = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return {"count": int(len(x)), "mean": float(x.mean()) if len(x) else None,
            "std": float(x.std(ddof=1)) if len(x) > 1 else None,
            "p01": float(x.quantile(.01)) if len(x) else None,
            "p05": float(x.quantile(.05)) if len(x) else None,
            "p10": float(x.quantile(.10)) if len(x) else None,
            "median": float(x.median()) if len(x) else None,
            "p90": float(x.quantile(.90)) if len(x) else None,
            "p95": float(x.quantile(.95)) if len(x) else None,
            "p99": float(x.quantile(.99)) if len(x) else None,
            "min": float(x.min()) if len(x) else None, "max": float(x.max()) if len(x) else None}


def _load_frozen_artifacts(root: Path, report_path: Path) -> tuple[dict, dict[str, dict], dict[str, dict[str, pd.DataFrame]], dict[str, dict[str, pd.DataFrame]]]:
    report = _json(report_path)
    if report.get("status") != "breadth_not_supported" or report.get("run_id") != RUN_ID:
        raise ValueError("Only the frozen Step 4.5C diagnostic run is accepted.")
    replay = verify_saved_breadth_report(report_path)
    if replay["status"] != "replay_verified" or replay["folds"] != 9:
        raise ValueError("Step 4.5C saved experiments failed immutable replay verification.")
    frozen = validate_frozen_universes(root / "config")
    manifests: dict[str, dict] = {}
    predictions: dict[str, dict[str, pd.DataFrame]] = {name: {} for name in UNIVERSES}
    inputs: dict[str, dict[str, pd.DataFrame]] = {name: {} for name in UNIVERSES}
    for name in UNIVERSES:
        manifest_path = Path(report["experiments"][name]["manifest_path"])
        manifest = _json(manifest_path)
        if manifest.get("universe_hash") != frozen["hashes"][name]:
            raise ValueError(f"{name} Universe hash no longer matches the frozen manifest.")
        if manifest.get("evaluation_role") != "development_walk_forward" or manifest.get("legacy_observed_test_accessed") is not False:
            raise ValueError(f"{name} evaluation governance metadata is invalid.")
        dataset_path = Path(manifest["dataset_path"])
        if _sha256(dataset_path) != manifest["dataset_sha256"]:
            raise ValueError(f"{name} development dataset checksum changed.")
        dataset_manifest = _json(dataset_path.with_suffix(".manifest.json"))
        if dataset_manifest.get("universe_hash") not in (None, frozen["hashes"][name]):
            raise ValueError(f"{name} dataset manifest Universe hash mismatch.")
        data = pd.read_parquet(dataset_path)
        validate_development_dates(data)
        if pd.to_datetime(data["prediction_date"]).lt("2018-01-01").any():
            raise ValueError(f"{name} development dataset begins before research boundary.")
        if not data["source"].eq("baostock").all() or not data["adjustment"].eq("qfq").all():
            raise ValueError(f"{name} dataset has mixed provider or adjustment.")
        manifests[name] = manifest
        for fold in manifest["folds"]:
            fold_id = fold["fold_id"]
            for kind, expected in fold["file_sha256"].items():
                file_path = Path(fold["files"][kind])
                verify_immutable_file(file_path, expected)
            pred = pd.read_parquet(fold["files"]["predictions"])
            x = pd.read_parquet(fold["files"]["validation_inputs"])
            validate_prediction_input_alignment(pred, x)
            pred["prediction_date"] = pd.to_datetime(pred["prediction_date"]).dt.normalize()
            pred["label_end_date"] = pd.to_datetime(pred["label_end_date"]).dt.normalize()
            validate_development_dates(pred)
            if not pred["prediction_date"].between("2018-01-01", "2023-12-31").all():
                raise ValueError(f"{name}/{fold_id} prediction dates escape development scope.")
            if not set(("lightgbm", "train_mean", "momentum_5d", "target_5d")) <= set(pred):
                raise ValueError(f"{name}/{fold_id} lacks saved prediction or baseline columns.")
            if not set(x.columns) == set(FEATURE_COLUMNS):
                raise ValueError(f"{name}/{fold_id} saved feature input schema changed.")
            pred = pred.reset_index(drop=True)
            for feature in FEATURE_COLUMNS:
                pred[feature] = x[feature].to_numpy()
            pred["universe"] = name
            pred["fold_id"] = fold_id
            predictions[name][fold_id] = pred
            inputs[name][fold_id] = x
    return report, manifests, predictions, inputs


def _baseline_summary(predictions: dict[str, dict[str, pd.DataFrame]]) -> list[dict]:
    rows = []
    for universe in UNIVERSES:
        for fold_id, frame in predictions[universe].items():
            daily_all = []
            for column, label in (("lightgbm", "LightGBM"), ("train_mean", "Train Mean"), ("momentum_5d", "Return_5d Momentum")):
                valid = frame[["target_5d", column]].dropna()
                actual, score = valid.target_5d.to_numpy(), valid[column].to_numpy()
                daily = daily_ic(frame, column, "target_5d")
                rank = pd.to_numeric(daily.rank_ic, errors="coerce").dropna()
                pearson_daily = []
                for _, group in frame.groupby("prediction_date", sort=True):
                    pair = group[["target_5d", column]].dropna()
                    if len(pair) >= 3 and pair.target_5d.nunique() > 1 and pair[column].nunique() > 1:
                        pearson_daily.append(float(pair.target_5d.corr(pair[column])))
                rows.append({"universe": universe, "fold_id": fold_id, "baseline": label,
                             "diagnostic_role":DIAGNOSTIC_ROLE,
                             "rows": int(len(valid)), "mae": float(np.mean(np.abs(actual-score))),
                             "rmse": float(np.sqrt(np.mean((actual-score)**2))),
                             "rank_ic_mean": float(rank.mean()) if len(rank) else None,
                             "rank_icir": float(rank.mean()/rank.std(ddof=1)) if len(rank)>1 and rank.std(ddof=1)>0 else None,
                             "rank_ic_positive_day_ratio": float((rank>0).mean()) if len(rank) else None,
                             "rank_ic_negative_day_ratio": float((rank<0).mean()) if len(rank) else None,
                             "pearson_ic_mean": float(np.mean(pearson_daily)) if pearson_daily else None,
                             "pearson_icir": float(np.mean(pearson_daily)/np.std(pearson_daily,ddof=1)) if len(pearson_daily)>1 and np.std(pearson_daily,ddof=1)>0 else None,
                             "prediction_distribution": _describe_numeric(frame[column])})
    return rows


def _ic_tables(predictions: dict[str, dict[str, pd.DataFrame]]) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
    summaries, extremes, monthly = [], [], []
    for universe in UNIVERSES:
        for fold_id, frame in predictions[universe].items():
            daily = daily_ic(frame, "lightgbm", "target_5d")
            summary, tail_dates = ic_distribution(daily)
            summaries.append({"universe": universe, "fold_id": fold_id, "diagnostic_role": DIAGNOSTIC_ROLE, **summary})
            tail_dates["universe"] = universe
            tail_dates["fold_id"] = fold_id
            tail_dates["diagnostic_role"] = DIAGNOSTIC_ROLE
            extremes.append(tail_dates)
            work = daily.dropna(subset=["rank_ic"]).copy()
            work["prediction_date"] = pd.to_datetime(work.prediction_date)
            work["year_month"] = work.prediction_date.dt.to_period("M").astype(str)
            work["year_quarter"] = work.prediction_date.dt.to_period("Q").astype(str)
            for field in ("year_month", "year_quarter"):
                for key, group in work.groupby(field, sort=True):
                    monthly.append({"universe": universe, "fold_id": fold_id, "period_type": field,
                                    "period": key, **_describe_numeric(group.rank_ic),
                                    "positive_day_ratio": float((group.rank_ic>0).mean()),
                                    "negative_day_ratio": float((group.rank_ic<0).mean()),
                                    "effective_breadth_mean": float(group.sample_count.mean()),
                                    "diagnostic_role": DIAGNOSTIC_ROLE})
    return summaries, pd.concat(extremes, ignore_index=True), pd.DataFrame(monthly)


def _ranking_agreement(predictions: dict[str, dict[str, pd.DataFrame]], anchors: set[str]) -> list[dict]:
    records = []
    for fold_id in ("fold_1", "fold_2", "fold_3"):
        frames = {name: predictions[name][fold_id].loc[
            predictions[name][fold_id].symbol.isin(anchors), ["symbol", "prediction_date", "lightgbm", "target_5d"]]
            for name in UNIVERSES}
        merged = frames["U11"].rename(columns={"lightgbm":"score_u11","target_5d":"target_u11"})
        for name in ("U50","U100"):
            merged = merged.merge(frames[name].rename(columns={"lightgbm":f"score_{name.lower()}","target_5d":f"target_{name.lower()}"}),
                                  on=["symbol","prediction_date"],how="inner",validate="one_to_one")
        if not np.allclose(merged.target_u11, merged.target_u50, rtol=1e-12, atol=1e-12) or not np.allclose(merged.target_u11, merged.target_u100, rtol=1e-12, atol=1e-12):
            raise ValueError("Anchor raw targets are not identical on aligned keys.")
        for pair in (("u11","u50"),("u11","u100"),("u50","u100")):
            vals=[]
            for _, day in merged.groupby("prediction_date",sort=True):
                vals.append(_corr(day[f"score_{pair[0]}"],day[f"score_{pair[1]}"]))
            valid=pd.Series(vals,dtype=float).dropna()
            records.append({"fold_id":fold_id,"model_pair":"/".join(pair),"anchor_symbols":len(anchors),
                            "common_keys":len(merged),"valid_dates":len(valid),
                            "daily_rank_agreement_mean":float(valid.mean()) if len(valid) else None,
                            "daily_rank_agreement_median":float(valid.median()) if len(valid) else None,
                            "diagnostic_role":DIAGNOSTIC_ROLE})
    return records


def _rank_errors(frame: pd.DataFrame) -> pd.DataFrame:
    parts=[]
    for _, day in frame.groupby("prediction_date",sort=True):
        block=day.copy()
        block["predicted_rank_pct"]=block.lightgbm.rank(method="average",pct=True)
        block["realized_rank_pct"]=block.target_5d.rank(method="average",pct=True)
        block["absolute_rank_error"]=(block.predicted_rank_pct-block.realized_rank_pct).abs()
        parts.append(block)
    return pd.concat(parts,ignore_index=True) if parts else frame.assign(absolute_rank_error=np.nan)


def _group_summary(frame: pd.DataFrame, group_col: str, min_cross_section: int) -> list[dict]:
    rows=[]
    for key, group in frame.groupby(group_col,dropna=False,sort=True):
        ics=[]
        for _,day in group.groupby("prediction_date",sort=True):
            if day.symbol.nunique()>=min_cross_section:
                value=_rank_ic_for_group(day.target_5d,day.lightgbm,min_cross_section)
                if value is not None: ics.append(value)
        rows.append({group_col:key,"symbols":int(group.symbol.nunique()),"observations":int(len(group)),
                     "dates":int(group.prediction_date.nunique()),"target_mean":float(group.target_5d.mean()),
                     "target_std":float(group.target_5d.std()),"prediction_mean":float(group.lightgbm.mean()),
                     "prediction_std":float(group.lightgbm.std()),"mae":float((group.lightgbm-group.target_5d).abs().mean()),
                     "rmse":float(np.sqrt(((group.lightgbm-group.target_5d)**2).mean())),
                     "daily_rank_ic_mean":float(np.mean(ics)) if ics else None,"valid_daily_ic_dates":len(ics),
                     "minimum_cross_section_for_ic":min_cross_section,
                     "insufficient_group_breadth":not bool(ics),"diagnostic_role":DIAGNOSTIC_ROLE,
                     **({"industry_classification_warning":INDUSTRY_WARNING} if group_col=="industry" else {})})
    return rows


def _symbol_diagnostics(u100: pd.DataFrame, members: dict[str, dict]) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    frame=u100.copy()
    frame["exchange"]=frame.symbol.map(lambda s: members[s]["exchange"])
    frame["industry"]=frame.symbol.map(lambda s: members[s].get("industry") or "Unknown")
    frame["industry_classification_warning"]=INDUSTRY_WARNING
    ranked=_rank_errors(frame)
    symbol_rows=[]
    for symbol,group in ranked.groupby("symbol",sort=True):
        ic=[]
        for _,day in group.groupby("prediction_date",sort=True):
            value=_rank_ic_for_group(day.target_5d,day.lightgbm)
            if value is not None:ic.append(value)
        symbol_rows.append({"symbol":symbol,"exchange":members[symbol]["exchange"],
            "industry":members[symbol].get("industry") or "Unknown","industry_classification_warning":INDUSTRY_WARNING,
            "prediction_dates":int(group.prediction_date.nunique()),"observations":int(len(group)),
            "target_mean":float(group.target_5d.mean()),"target_std":float(group.target_5d.std()),
            "target_p05":float(group.target_5d.quantile(.05)),"target_p95":float(group.target_5d.quantile(.95)),
            "score_mean":float(group.lightgbm.mean()),"score_std":float(group.lightgbm.std()),
            "score_p05":float(group.lightgbm.quantile(.05)),"score_p95":float(group.lightgbm.quantile(.95)),
            "mae":float((group.lightgbm-group.target_5d).abs().mean()),
            "rmse":float(np.sqrt(((group.lightgbm-group.target_5d)**2).mean())),
            "per_symbol_time_series_rank_ic_mean":float(np.mean(ic)) if ic else None,
            "absolute_rank_error_mean":float(group.absolute_rank_error.mean()),
            "absolute_rank_error_p95":float(group.absolute_rank_error.quantile(.95)),
            "absolute_rank_error_max":float(group.absolute_rank_error.max()),
            "diagnostic_role":DIAGNOSTIC_ROLE})
    return pd.DataFrame(symbol_rows),pd.DataFrame(_group_summary(ranked,"exchange",3)),pd.DataFrame(_group_summary(ranked,"industry",8))


def _scale_diagnostics(predictions: dict[str, dict[str,pd.DataFrame]]) -> tuple[list[dict],list[dict]]:
    per_fold=[]; score_medians={}
    for fold_id in ("fold_1","fold_2","fold_3"):
        frame=predictions["U100"][fold_id]
        symbols=frame.groupby("symbol",sort=True).agg(score_median=("lightgbm","median"),
            price_scale_proxy=("MA_60","median"),volume_scale_proxy=("Volume_MA20","median"),
            volatility_20d=("Volatility_20d","median"),score_std=("lightgbm","std")).reset_index()
        score_medians[fold_id]=symbols.set_index("symbol").score_median
        per_fold.append({"fold_id":fold_id,"symbols":len(symbols),
            "symbol_median_score_vs_MA60_spearman":_corr(symbols.score_median,symbols.price_scale_proxy),
            "symbol_median_score_vs_Volume_MA20_spearman":_corr(symbols.score_median,symbols.volume_scale_proxy),
            "symbol_median_score_vs_Volatility_20d_spearman":_corr(symbols.score_median,symbols.volatility_20d),
            "pooled_score_vs_MA60_spearman":_corr(frame.lightgbm,frame.MA_60),
            "pooled_score_vs_Volume_MA20_spearman":_corr(frame.lightgbm,frame.Volume_MA20),
            "pooled_score_vs_Volatility_20d_spearman":_corr(frame.lightgbm,frame.Volatility_20d),
            "median_score_dispersion_across_symbols":float(symbols.score_median.std()),
            "within_symbol_score_std_median":float(symbols.score_std.median()),
            "diagnostic_role":DIAGNOSTIC_ROLE})
    persistence=[]
    for i,left in enumerate(score_medians):
        for right in list(score_medians)[i+1:]:
            persistence.append({"fold_pair":f"{left}/{right}","symbols":int(score_medians[left].dropna().index.intersection(score_medians[right].dropna().index).size),
                               "symbol_median_score_spearman":_corr(score_medians[left],score_medians[right]),
                               "diagnostic_role":DIAGNOSTIC_ROLE})
    return per_fold,persistence


def _univariate_diagnostics(predictions: dict[str,dict[str,pd.DataFrame]]) -> pd.DataFrame:
    rows=[]
    for universe in UNIVERSES:
        for fold_id,frame in predictions[universe].items():
            for feature in FEATURE_COLUMNS:
                daily=daily_ic(frame,feature,"target_5d")
                summary,_=ic_distribution(daily)
                rows.append({"universe":universe,"fold_id":fold_id,"feature":feature,
                             "daily_rank_ic_mean":summary["mean"],"daily_rank_ic_median":summary["median"],
                             "daily_rank_ic_std":summary["std"],"p10":summary["quantiles"].get("p10"),
                             "p90":summary["quantiles"].get("p90"),"positive_day_ratio":summary["positive_day_ratio"],
                             "negative_day_ratio":summary["negative_day_ratio"],"valid_dates":summary["valid_dates"],
                             "diagnostic_role":UNIVARIATE_ROLE,"orientation":"raw feature; no post-hoc inversion"})
    return pd.DataFrame(rows)


def _horizon_audit(root: Path, consistency_path: Path) -> tuple[dict,pd.DataFrame]:
    sessions=AShareTradingCalendar("2018-01-01","2023-12-31").sessions
    records=[]; summaries={}
    for universe in UNIVERSES:
        base=root/"data"/"processed"/f"{DATASET_NAMES[universe]}.parquet"
        manifest=_json(base.with_suffix(".manifest.json"))
        if manifest.get("research_end")!="2023-12-31" or manifest.get("provider")!="baostock" or manifest.get("adjustment")!="qfq":
            raise ValueError(f"{universe} horizon source is outside permitted development scope.")
        frame=pd.read_parquet(base)
        validate_development_dates(frame)
        date_counts=frame.groupby("prediction_date").label_end_date.nunique(dropna=True)
        mismatch=0; labeled=0
        for symbol,group in frame.sort_values("prediction_date").groupby("symbol",sort=False):
            dates=pd.to_datetime(group.prediction_date).dt.normalize().reset_index(drop=True)
            ends=pd.to_datetime(group.label_end_date).dt.normalize().reset_index(drop=True)
            targets=pd.to_numeric(group.target_5d,errors="coerce").reset_index(drop=True)
            expected_ends=dates.shift(-5)
            valid=targets.notna() & ends.notna()
            labeled+=int(valid.sum())
            mismatch+=int((ends[valid].to_numpy()!=expected_ends[valid].to_numpy()).sum())
            for i in np.flatnonzero(valid.to_numpy()):
                prediction,ending=dates.iloc[i],ends.iloc[i]
                if prediction not in sessions or ending not in sessions:
                    raise ValueError("Target dates are not valid XSHG sessions.")
                calendar_sessions=session_horizon(prediction,ending,sessions)
                records.append({"universe":universe,"symbol":symbol,"prediction_date":prediction,
                                "label_end_date":ending,"calendar_days":int((ending-prediction).days),
                                "exchange_sessions_after_prediction":calendar_sessions,
                                "additional_no_trade_or_missing_sessions":calendar_sessions-5,
                                "diagnostic_role":DIAGNOSTIC_ROLE})
        if mismatch:
            raise ValueError(f"{universe} label_end_date does not point to the fifth subsequent effective observation ({mismatch} rows).")
        summaries[universe]={"labeled_rows":labeled,"label_end_mismatches":mismatch,
                             "prediction_dates_with_multiple_label_ends":int((date_counts>1).sum()),
                             "prediction_date_count":int(len(date_counts))}
    horizons=pd.DataFrame(records)
    for universe,group in horizons.groupby("universe"):
        summaries[universe]["calendar_days"]=_describe_numeric(group.calendar_days)
        summaries[universe]["exchange_sessions_after_prediction"]=_describe_numeric(group.exchange_sessions_after_prediction)
        summaries[universe]["sessions_gt_five_rows"]=int((group.exchange_sessions_after_prediction>5).sum())
        summaries[universe]["sessions_gt_five_ratio"]=float((group.exchange_sessions_after_prediction>5).mean())
        summaries[universe]["max_calendar_span_days"]=int(group.calendar_days.max())
    consistency=_json(consistency_path)
    summaries["no_trade_exclusion"]={**validate_no_trade_exclusion(consistency),
        "stale_canonical_overlap_rows_961":True,
        "source_note":"Step 4.5B quality audit counted 961 stale canonical bars overlapping provider tradestatus=0; Step 4.5C consistency report confirms current U100 sample keys are all tradestatus=1. No database rows were modified."}
    return summaries,horizons


def _governance_audit(root: Path, consistency: dict) -> dict:
    gov=_json(root/"config"/"research_governance.json")
    base=gov["experiments"][LEGACY_ID]
    baseline_path=root/"artifacts"/"experiments"/LEGACY_ID/"manifest.json"
    baseline_hash=_sha256(baseline_path)
    step43=_json(root/"artifacts"/"reports"/"walkforward_comparison_2ee1453e2be5.json")
    step44=_json(root/"artifacts"/"reports"/"target_comparison_28b36e885c5a.json")
    step45c=_json(root/REPORT_RELATIVE)
    current_report=(root/"docs"/"step_4_5C_breadth_robustness.md").read_text(encoding="utf-8")
    mismatch=("46" in current_report and "55" in current_report and "tradestatus=0" in current_report
              and consistency["immutable_step43_u11_reference_comparison"]["old_only_provider_no_trade"]==46
              and consistency["immutable_step43_u11_reference_comparison"]["current_only_unlabeled"]==55)
    return {"baseline_v1_manifest_sha256_matches_registry":baseline_hash==base["manifest_sha256"],
        "baseline_v1_role":base["evaluation_role"],"baseline_v1_immutable":base["immutable"],
        "step43_v2_status":step43["candidate_assessment"]["status"],
        "step43_v2_excluded_absolute_scale_features":step43["feature_v2"]["feature_version"],
        "step44_target_status":step44["candidate_assessment"]["status"],
        "step44_selected_target":step44["candidate_assessment"]["selected_target"],
        "step45c_status":step45c["status"],"step45c_universe_hashes":step45c["frozen_hashes"],
        "step43_u11_difference_documented":bool(mismatch),
        "step43_u11_old_only_provider_no_trade":consistency["immutable_step43_u11_reference_comparison"]["old_only_provider_no_trade"],
        "step43_u11_current_only_unlabeled":consistency["immutable_step43_u11_reference_comparison"]["current_only_unlabeled"],
        "legacy_observed_test_role":"LEGACY_OBSERVED_TEST","legacy_observed_test_accessed":False,
        "future_sealed_holdout":"2026+; not accessed","u100_point_in_time":"NOT_POINT_IN_TIME",
        "survivorship_bias":"unresolved; current listing snapshot excludes historical delisted members",
        "processing_versions_not_pure_model_differences":True,
        "baseline_manifest_integrity_sha256":baseline_hash}


def run_signal_diagnostics(root: Path | None=None) -> dict:
    root=(root or Path(__file__).resolve().parents[3]).resolve()
    report_path=root/REPORT_RELATIVE
    experiment_report,manifests,predictions,inputs=_load_frozen_artifacts(root,report_path)
    consistency_path=Path(experiment_report["consistency_gate"])
    if not consistency_path.is_absolute(): consistency_path=root/consistency_path
    consistency=_json(consistency_path)
    ic_summaries,extremes,periods=_ic_tables(predictions)
    all_u100=pd.concat([predictions["U100"][fold] for fold in ("fold_1","fold_2","fold_3")],ignore_index=True)
    frozen=validate_frozen_universes(root/"config")
    config=_json(root/"config"/"ashare_universe_breadth_100.json")
    member_map={m["symbol"]:m for m in config["members"]}
    symbol_table,exchange_table,industry_table=_symbol_diagnostics(all_u100,member_map)
    symbol_2022,exchange_2022,industry_2022=_symbol_diagnostics(predictions["U100"]["fold_2"],member_map)
    scale,persistence=_scale_diagnostics(predictions)
    univariate=_univariate_diagnostics(predictions)
    horizon,horizon_rows=_horizon_audit(root,consistency_path)
    governance=_governance_audit(root,consistency)
    baseline=_baseline_summary(predictions)
    agreement=_ranking_agreement(predictions,set(frozen["symbols"]["U11"]))
    date_rows=[]
    for universe in UNIVERSES:
        for fold_id,frame in predictions[universe].items():
            daily=daily_ic(frame,"lightgbm","target_5d").dropna(subset=["rank_ic"])
            for date,value in zip(daily.prediction_date,daily.rank_ic):
                date_rows.append({"universe":universe,"fold_id":fold_id,"prediction_date":pd.Timestamp(date).date().isoformat(),"rank_ic":float(value)})
    dates=pd.DataFrame(date_rows)
    period_focus=periods.loc[periods.fold_id=="fold_2"].to_dict("records")
    lower_tail_2022={row["universe"]:next(x for x in ic_summaries if x["universe"]==row["universe"] and x["fold_id"]=="fold_2") for row in ({"universe":u} for u in UNIVERSES)}
    versions={"step45c_report":str(report_path),"step45c_report_sha256":_sha256(report_path),
        "universe_hashes":experiment_report["frozen_hashes"],
        "dataset_hashes":experiment_report["dataset_sha256"],
        "experiment_ids":{u:experiment_report["experiments"][u]["experiment_id"] for u in UNIVERSES},
        "fold_model_hashes":{u:{f["fold_id"]:f["file_sha256"]["model"] for f in manifests[u]["folds"]} for u in UNIVERSES},
        "validation_input_hashes":{u:{f["fold_id"]:f["file_sha256"]["validation_inputs"] for f in manifests[u]["folds"]} for u in UNIVERSES},
        "replay_verified_folds":9}
    payload={"diagnostic_id":f"signal_failure_{DIAGNOSTIC_OUTPUT_ID}","status":"root_cause_not_identified",
        "diagnostic_role":DIAGNOSTIC_ROLE,"univariate_role":UNIVARIATE_ROLE,
        "created_at_utc":datetime.now(timezone.utc).isoformat(),"inputs":versions,
        "data_scope":{"start":"2018-01-01","end":"2023-12-31","provider":"BaoStock","adjustment":"qfq",
                      "legacy_test_accessed":False,"future_sealed_holdout_accessed":False,
                      "no_model_training":True,"no_candidate_selection":True},
        "daily_ic_distribution":ic_summaries,"extreme_negative_dates":extremes.to_dict("records"),
        "monthly_quarterly_ic":periods.to_dict("records"),"2022_monthly_quarterly":period_focus,
        "2022_model_rank_agreement_on_u11_anchors":[x for x in agreement if x["fold_id"]=="fold_2"],
        "symbol_diagnostics":{"classification_warning":INDUSTRY_WARNING,"symbols":symbol_table.to_dict("records"),
                              "exchange":exchange_table.to_dict("records"),"industry":industry_table.to_dict("records"),
                              "u100_2022":{"symbols":symbol_2022.to_dict("records"),
                                           "exchange":exchange_2022.to_dict("records"),
                                           "industry":industry_2022.to_dict("records")}},
        "stock_scale_proxy_hypothesis":{"diagnostic_role":DIAGNOSTIC_ROLE,"fold_results":scale,
            "cross_fold_symbol_score_persistence":persistence,
            "proxy_definitions":{"price":"median saved validation MA_60, a historical observed price-level proxy",
                                 "volume":"median saved validation Volume_MA20, a historical observed share-volume proxy"}},
        "univariate_features":{"diagnostic_role":UNIVARIATE_ROLE,"feature_count":len(FEATURE_COLUMNS),
                               "results":univariate.to_dict("records"),"no_orientation_flip":True},
        "feature_importance_diagnostics":{"u100":manifests["U100"]["feature_importance_stability"],
            "source":"Saved Step 4.5C fold models/importance only; no refit"},
        "target_horizon_audit":horizon,"baseline_comparison":baseline,
        "governance_audit":governance,
        "findings":{"u100_positive_day_ratio_over_half_but_negative_fold_mean":[
            {"fold_id":x["fold_id"],"mean":x["mean"],"positive_day_ratio":x["positive_day_ratio"],
             "negative_tail":x["negative_lower_tail"]} for x in ic_summaries if x["universe"]=="U100"],
            "root_cause_not_identified":True,
            "regime_effect_claim":"not established; observed 2022 association only",
            "feature_winner_selected":False,"security_or_sector_removed":False,
            "scale_causality_claim":"not established; scale correlations are descriptive proxies"},
        "next_research_recommendation":[
            "First reduce point-in-time and survivorship uncertainty by reconstructing historical membership and timestamped corporate-action prices before claiming historical model comparisons.",
            "Pre-register a cross-sectional feature and benchmark study on frozen development dates; retain all 17 features as diagnostics and do not select direction after observing the ICs.",
            "Quantify five-valid-observation versus five-exchange-session realization delays and transaction feasibility before mapping predictions to a tradable holding rule.",
            "Treat the 2022/2021/2023 difference as a regime-instability hypothesis; any future state conditioning needs an independently pre-registered state variable and untouched evaluation period."],
        "caveats":["All results are post-hoc diagnostics, not candidate validation.",
                   "Industry labels are current 2026 classifications, not historical PIT.",
                   "The chosen universe is survivorship limited and qfq snapshots are not point-in-time.",
                   "Lower daily IC dispersion with larger breadth may partly reflect cross-sectional sampling mechanics."]}
    return {"payload":payload,"tables":{"daily_ic_extreme_dates":extremes,"period_ic":periods,
        "symbol_diagnostics":symbol_table,"exchange_diagnostics":exchange_table,"industry_diagnostics":industry_table,
        "symbol_diagnostics_2022":symbol_2022,"exchange_diagnostics_2022":exchange_2022,
        "industry_diagnostics_2022":industry_2022,
        "stock_scale":pd.DataFrame(scale),"stock_score_persistence":pd.DataFrame(persistence),
        "univariate_feature_ic":univariate,"target_horizon_rows":horizon_rows,
        "baseline_comparison":pd.DataFrame(baseline),"anchor_rank_agreement":pd.DataFrame(agreement)}}


def write_signal_diagnostics(result: dict, *, root: Path | None=None) -> dict:
    root=(root or Path(__file__).resolve().parents[3]).resolve()
    base=root/"artifacts"/"reports"/f"signal_failure_diagnostics_{DIAGNOSTIC_OUTPUT_ID}"
    base.mkdir(parents=True,exist_ok=True)
    for name,frame in result["tables"].items():
        frame.to_csv(base/f"{name}.csv",index=False,encoding="utf-8-sig")
    json_path=base/"report.json"
    write_json_exclusive(json_path,result["payload"])
    return {"json":str(json_path),"csv_directory":str(base),"csv_files":sorted(p.name for p in base.glob("*.csv"))}
