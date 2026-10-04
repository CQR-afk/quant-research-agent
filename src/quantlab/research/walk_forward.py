"""Development-only, pre-2024 expanding-window comparison of fixed LightGBM features."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from lightgbm import LGBMRegressor
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error

from quantlab.data.store import MarketDataStore
from quantlab.experiments import daily_information_coefficients
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import project_root
from quantlab.research.features import FEATURE_V1_SCALE_DEPENDENT, FEATURE_V1_SCALE_INVARIANT
from quantlab.research.walkforward_splits import (
    DEVELOPMENT_CUTOFF, split_walk_forward, validate_development_dates,
)

LEGACY_BASELINE_ID = "20261004T120849Z_2cb6448746"
DEVELOPMENT_END = "2023-12-31"
TARGET_VERSION = "target_5d_h5_v1"


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def fixed_lightgbm_parameters(config_path: Path) -> dict[str, object]:
    """Use the existing frozen baseline parameters without search or validation-based selection."""
    params = dict(_read_json(config_path)["lightgbm"])
    params.update({"verbosity": -1, "deterministic": True, "force_col_wise": True})
    return params


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_sealed_development_parquet(path: Path) -> pd.DataFrame:
    """Arrow row filters avoid materializing the observed 2024–2025 prediction rows."""
    table = pq.read_table(path, filters=[
        ("prediction_date", "<", DEVELOPMENT_CUTOFF.to_pydatetime()),
        ("label_end_date", "<", DEVELOPMENT_CUTOFF.to_pydatetime()),
    ])
    frame = table.to_pandas()
    validate_development_dates(frame)
    return frame


def _write_v1_development_snapshot(
    frame: pd.DataFrame, source_manifest: dict[str, object], root: Path,
) -> dict[str, object]:
    """Materialize only pre-2024 v1 samples with a development-only content version."""
    output = root / "data" / "processed" / "ashare_research_dataset_baostock_qfq_v1_development_2018_2023.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = frame.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.parquet")
    frame.to_parquet(temporary, index=False)
    temporary.replace(output)
    version_columns = [column for column in frame.columns if column not in {"feature_parameters"}]
    canonical = frame[version_columns].to_csv(index=False, na_rep="").encode("utf-8")
    version = hashlib.sha256(canonical).hexdigest()
    coverage = []
    for symbol, group in frame.groupby("symbol", sort=True):
        coverage.append({"symbol": symbol, "rows": len(group),
                         "first_date": pd.Timestamp(group["prediction_date"].min()).date().isoformat(),
                         "last_date": pd.Timestamp(group["prediction_date"].max()).date().isoformat()})
    manifest = {
        "dataset_id": f"development_v1_{version[:16]}", "dataset_version": version,
        "feature_version": "ashare_daily_v1", "feature_columns": source_manifest["feature_columns"],
        "feature_parameters": source_manifest.get("feature_parameters", {}),
        "target": source_manifest.get("target"), "target_version": TARGET_VERSION,
        "target_horizon_observations": 5, "provider": "baostock", "source": "baostock",
        "adjustment": "qfq", "price_basis": "vendor_snapshot_adjusted",
        "pool_id": source_manifest.get("pool_id"), "pool_selection_date": source_manifest.get("pool_selection_date"),
        "pool_size": source_manifest.get("pool_size"), "research_start": "2018-01-01",
        "research_end": DEVELOPMENT_END, "date_range": {"start": "2018-01-01", "end": DEVELOPMENT_END},
        "dataset_rows": len(frame), "coverage": coverage,
        "dataset_path": str(output.resolve()), "evaluation_role": "development_walk_forward",
        "legacy_observed_test_rows_materialized": False,
        "derivation": "Only rows selected by prediction_date < 2024-01-01 and label_end_date < 2024-01-01 Arrow filters were persisted.",
        "point_in_time_limit": source_manifest.get("point_in_time_limit"),
    }
    _write_json(output.with_suffix(".manifest.json"), manifest)
    return manifest


def _ic_summary(values: pd.Series | np.ndarray) -> dict[str, float | int | None]:
    series = pd.to_numeric(pd.Series(values), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    mean = float(series.mean()) if len(series) else None
    std = float(series.std(ddof=1)) if len(series) > 1 else None
    return {
        "mean": mean, "median": float(series.median()) if len(series) else None,
        "std": std, "icir": float(mean / std) if mean is not None and std is not None and std > 0 else None,
        "positive_day_ratio": float((series > 0).mean()) if len(series) else None,
        "valid_days": int(len(series)),
    }


def newey_west_mean_diagnostic(values: pd.Series | np.ndarray, lag: int = 5) -> dict[str, float | int | None]:
    """Bartlett Newey–West standard error/t diagnostic for a daily mean (diagnostic only)."""
    if lag < 0:
        raise ValueError("Newey-West lag must be non-negative.")
    series = pd.to_numeric(pd.Series(values), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    count = len(series)
    if count < 2:
        return {"lag": lag, "observations": count, "mean": float(series.mean()) if count else None,
                "standard_error": None, "t_diagnostic": None}
    array = series.to_numpy(dtype=float)
    centered = array - array.mean()
    max_lag = min(lag, count - 1)
    long_run_variance = float(np.dot(centered, centered) / count)
    for offset in range(1, max_lag + 1):
        weight = 1.0 - offset / (lag + 1.0)
        covariance = float(np.dot(centered[offset:], centered[:-offset]) / count)
        long_run_variance += 2.0 * weight * covariance
    standard_error = float(np.sqrt(max(long_run_variance, 0.0) / count))
    mean = float(array.mean())
    return {"lag": lag, "observations": count, "mean": mean,
            "standard_error": standard_error,
            "t_diagnostic": mean / standard_error if standard_error > 0 else None}


def _fold_metrics(predictions: pd.DataFrame) -> tuple[dict[str, object], pd.DataFrame]:
    daily = daily_information_coefficients(predictions, ["lightgbm"])
    pearson = _ic_summary(daily["pearson_ic"])
    rank = _ic_summary(daily["spearman_rank_ic"])
    actual = predictions["target_5d"].to_numpy(dtype=float)
    predicted = predictions["lightgbm"].to_numpy(dtype=float)
    actual = actual[np.isfinite(actual)]
    predicted_valid = predictions.loc[np.isfinite(predictions["target_5d"]) & np.isfinite(predictions["lightgbm"])]
    predicted = predicted_valid["lightgbm"].to_numpy(dtype=float)
    actual = predicted_valid["target_5d"].to_numpy(dtype=float)

    def distribution(values: np.ndarray) -> dict[str, float | int]:
        return {"count": len(values), "mean": float(np.mean(values)), "std": float(np.std(values, ddof=1)),
                "min": float(np.min(values)), "p01": float(np.quantile(values, .01)),
                "median": float(np.median(values)), "p99": float(np.quantile(values, .99)),
                "max": float(np.max(values))}

    metric = {
        "rows": int(len(predicted_valid)), "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "pearson_ic": pearson, "rank_ic": rank,
        "newey_west_lag5": {
            "pearson_ic": newey_west_mean_diagnostic(daily["pearson_ic"], 5),
            "rank_ic": newey_west_mean_diagnostic(daily["spearman_rank_ic"], 5),
            "interpretation": "Overlap-aware diagnostic only; not formal significance or evidence of investment effectiveness.",
        },
        "prediction_distribution": distribution(predicted),
        "actual_target_distribution": distribution(actual),
        "prediction_to_target_std_ratio": float(np.std(predicted, ddof=1) / np.std(actual, ddof=1))
        if len(actual) > 1 and np.std(actual, ddof=1) > 0 else None,
        "ic_coverage": float(daily["pearson_ic"].notna().mean()) if len(daily) else 0.0,
        "rank_ic_coverage": float(daily["spearman_rank_ic"].notna().mean()) if len(daily) else 0.0,
    }
    return metric, daily


def _period_ic(daily: pd.DataFrame, period: str) -> pd.DataFrame:
    frame = daily.copy()
    dates = pd.to_datetime(frame["prediction_date"])
    frame["period"] = dates.dt.to_period(period).astype(str)
    rows = []
    for key, group in frame.groupby("period", sort=True):
        pearson = _ic_summary(group["pearson_ic"])
        rank = _ic_summary(group["spearman_rank_ic"])
        rows.append({"period": key, "prediction_days": int(group["prediction_date"].nunique()),
                     **{f"pearson_{name}": value for name, value in pearson.items()},
                     **{f"rank_{name}": value for name, value in rank.items()}})
    return pd.DataFrame(rows)


def _aggregate_fold_metrics(folds: list[dict[str, object]]) -> dict[str, object]:
    fields = {
        "mae": lambda m: m["mae"], "rmse": lambda m: m["rmse"],
        "pearson_ic_mean": lambda m: m["pearson_ic"]["mean"],
        "rank_ic_mean": lambda m: m["rank_ic"]["mean"],
        "icir": lambda m: m["pearson_ic"]["icir"], "rank_icir": lambda m: m["rank_ic"]["icir"],
        "positive_ic_day_ratio": lambda m: m["pearson_ic"]["positive_day_ratio"],
        "positive_rank_ic_day_ratio": lambda m: m["rank_ic"]["positive_day_ratio"],
    }
    output: dict[str, object] = {}
    for name, getter in fields.items():
        values = np.asarray([getter(fold["metrics"]) for fold in folds], dtype=float)
        values = values[np.isfinite(values)]
        if not len(values):
            output[name] = {"mean": None, "median": None, "std": None, "min": None, "max": None,
                            "sign_consistency": None, "fold_count": 0}
            continue
        positive, negative = int((values > 0).sum()), int((values < 0).sum())
        output[name] = {
            "mean": float(values.mean()), "median": float(np.median(values)),
            "std": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
            "min": float(values.min()), "max": float(values.max()),
            "sign_consistency": max(positive, negative) / len(values), "fold_count": int(len(values)),
        }
    return output


def _feature_scale_audit(v1: pd.DataFrame, *, root: Path) -> dict[str, object]:
    # v1 has already been filtered to pre-2024 prediction and realization dates.
    feature_columns = [
        "Return_1d", "Return_5d", "Return_10d", "Return_20d", "MA_5", "MA_10", "MA_20", "MA_60",
        "Price_to_MA20", "RSI_14", "MACD", "MACD_Signal", "Volatility_5d", "Volatility_20d",
        "Volume_MA5", "Volume_MA20", "Volume_Ratio",
    ]
    symbols = sorted(v1["symbol"].astype(str).unique())
    store = MarketDataStore(root / "data")
    records = []
    for symbol in symbols:
        group = v1.loc[v1["symbol"] == symbol]
        bars = store.query(symbol=symbol, market="CN_A", start="2018-01-01", end=DEVELOPMENT_END,
                           adjustment="qfq", source="baostock")
        bars = bars.loc[pd.to_datetime(bars["trade_date"]) < DEVELOPMENT_CUTOFF] if len(bars) else bars
        record: dict[str, object] = {
            "symbol": symbol,
            "median_close": float(pd.to_numeric(bars["close"], errors="coerce").median()) if len(bars) else None,
            "median_volume": float(pd.to_numeric(bars["volume"], errors="coerce").median()) if len(bars) else None,
        }
        for column in feature_columns:
            values = pd.to_numeric(group[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            record[f"{column}_median"] = float(values.median()) if len(values) else None
            record[f"{column}_min"] = float(values.min()) if len(values) else None
            record[f"{column}_max"] = float(values.max()) if len(values) else None
        records.append(record)
    panel = pd.DataFrame(records)
    correlations = {}
    for feature, scale in [
        *((column, "median_close") for column in ("MA_5", "MA_10", "MA_20", "MA_60", "MACD", "MACD_Signal")),
        *((column, "median_volume") for column in ("Volume_MA5", "Volume_MA20")),
    ]:
        pair = panel[[f"{feature}_median", scale]].apply(pd.to_numeric, errors="coerce").dropna()
        correlations[feature] = {
            "scale_proxy": scale, "symbols": int(len(pair)),
            "pearson_correlation": float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
            if len(pair) > 2 and pair.iloc[:, 0].nunique() > 1 and pair.iloc[:, 1].nunique() > 1 else None,
            "interpretation": "Descriptive 11-stock scale association; not causal and does not include a symbol model feature.",
        }
    return {
        "evaluation_role": "development_walk_forward", "date_cap": DEVELOPMENT_END,
        "feature_v1_scale_invariant": FEATURE_V1_SCALE_INVARIANT,
        "feature_v1_scale_dependent": FEATURE_V1_SCALE_DEPENDENT,
        "absolute_feature_correlations": correlations,
        "per_symbol_feature_median_and_range": records,
        "symbol_feature_added": False,
    }


def _importance_stability(folds: list[dict[str, object]]) -> dict[str, object]:
    feature_names = sorted({row["feature"] for fold in folds for row in fold["feature_importance"]})
    matrices = []
    fold_summaries = []
    for fold in folds:
        frame = pd.DataFrame(fold["feature_importance"]).set_index("feature").reindex(feature_names).fillna(0)
        total = float(frame["gain"].sum())
        frame["gain_share"] = frame["gain"] / total if total > 0 else 0.0
        frame["rank"] = frame["gain"].rank(method="average", ascending=False)
        matrices.append(frame)
        order = frame.sort_values("gain", ascending=False)
        fold_summaries.append({"fold_id": fold["fold_id"], "top_feature": order.index[0],
                               "top_5": order.head(5).index.tolist(),
                               "top5_gain_share": float(order.head(5)["gain_share"].sum()),
                               "scale_dependent_top_feature": order.index[0] in FEATURE_V1_SCALE_DEPENDENT})
    correlations = {}
    for left in range(len(matrices)):
        for right in range(left + 1, len(matrices)):
            correlation = spearmanr(matrices[left]["rank"], matrices[right]["rank"]).statistic
            correlations[f"{folds[left]['fold_id']}__{folds[right]['fold_id']}"] = float(correlation) if np.isfinite(correlation) else None
    aggregate = []
    for feature in feature_names:
        aggregate.append({
            "feature": feature,
            "mean_gain_share": float(np.mean([matrix.at[feature, "gain_share"] for matrix in matrices])),
            "mean_gain_rank": float(np.mean([matrix.at[feature, "rank"] for matrix in matrices])),
            "rank_std": float(np.std([matrix.at[feature, "rank"] for matrix in matrices], ddof=1)) if len(matrices) > 1 else 0.0,
            "top1_fold_count": sum(summary["top_feature"] == feature for summary in fold_summaries),
            "top5_fold_count": sum(feature in summary["top_5"] for summary in fold_summaries),
            "v1_scale_dependent": feature in FEATURE_V1_SCALE_DEPENDENT,
        })
    return {"folds": fold_summaries, "pairwise_rank_correlations": correlations,
            "aggregate": sorted(aggregate, key=lambda row: row["mean_gain_share"], reverse=True),
            "mean_top5_gain_share": float(np.mean([item["top5_gain_share"] for item in fold_summaries]))}


def _prediction_stability(folds: list[dict[str, object]]) -> dict[str, object]:
    stds = [float(fold["metrics"]["prediction_distribution"]["std"]) for fold in folds]
    ratios = [fold["metrics"]["prediction_to_target_std_ratio"] for fold in folds]
    nonzero = [value for value in stds if value > 0]
    return {
        "folds": [{"fold_id": f["fold_id"], "prediction": f["metrics"]["prediction_distribution"],
                   "actual_target": f["metrics"]["actual_target_distribution"],
                   "prediction_to_target_std_ratio": f["metrics"]["prediction_to_target_std_ratio"],
                   "near_constant_prediction": bool(f["metrics"]["prediction_distribution"]["std"] < 1e-6)}
                  for f in folds],
        "prediction_std_max_to_min_ratio": max(nonzero) / min(nonzero) if nonzero else None,
        "median_prediction_to_target_std_ratio": float(np.nanmedian(ratios)) if ratios else None,
    }


def _fit_feature_experiment(
    *, label: str, version: str, feature_columns: list[str], dataset: pd.DataFrame,
    dataset_manifest: dict[str, object], params: dict[str, object], folds, output_dir: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    run_id = uuid.uuid4().hex
    experiment_id = f"walkforward_{version}_{run_id[:12]}"
    experiment_dir = output_dir / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)
    fold_results = []
    predictions_by_fold: dict[str, pd.DataFrame] = {}
    importance_by_fold: dict[str, list[dict[str, object]]] = {}
    for fold in folds:
        train, validation = fold.train, fold.validation
        model = LGBMRegressor(**params)
        model.fit(train[feature_columns], train["target_5d"])
        predictions = validation[["symbol", "prediction_date", "label_end_date", "target_5d"]].copy()
        predictions["lightgbm"] = model.predict(validation[feature_columns])
        metric, daily = _fold_metrics(predictions)
        fold_dir = experiment_dir / fold.fold_id
        fold_dir.mkdir(parents=True, exist_ok=False)
        predictions.to_parquet(fold_dir / "validation_predictions.parquet", index=False)
        daily.to_csv(fold_dir / "daily_ic.csv", index=False)
        monthly = _period_ic(daily, "M")
        quarterly = _period_ic(daily, "Q")
        monthly.to_csv(fold_dir / "monthly_ic.csv", index=False)
        quarterly.to_csv(fold_dir / "quarterly_ic.csv", index=False)
        joblib.dump(model, fold_dir / "lightgbm.joblib")
        importance = pd.DataFrame({
            "feature": feature_columns,
            "gain": model.booster_.feature_importance(importance_type="gain"),
            "split": model.booster_.feature_importance(importance_type="split"),
        }).sort_values("gain", ascending=False)
        importance.to_csv(fold_dir / "feature_importance.csv", index=False)
        predictions_by_fold[fold.fold_id] = predictions
        importance_by_fold[fold.fold_id] = importance.to_dict("records")
        fold_results.append({
            "fold_id": fold.fold_id, "split": fold.metadata, "metrics": metric,
            "monthly_ic": monthly.to_dict("records"), "quarterly_ic": quarterly.to_dict("records"),
            "feature_importance": importance.to_dict("records"),
            "files": {name: str((fold_dir / name).resolve()) for name in (
                "validation_predictions.parquet", "daily_ic.csv", "monthly_ic.csv",
                "quarterly_ic.csv", "feature_importance.csv", "lightgbm.joblib")},
        })

    manifest = {
        "experiment_id": experiment_id, "experiment_name": label, "status": "complete",
        "evaluation_role": "development_walk_forward", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "feature_version": version, "feature_columns": feature_columns,
        "dataset_id": dataset_manifest.get("dataset_id"),
        "dataset_version": dataset_manifest.get("dataset_version", dataset_manifest.get("data_version")),
        "dataset_path": dataset_manifest.get("dataset_path"),
        "target": "target_5d = close.shift(-5) / close - 1", "target_version": TARGET_VERSION,
        "target_horizon_observations": 5, "adjustment": dataset_manifest.get("adjustment"),
        "provider": dataset_manifest.get("provider"), "price_basis": dataset_manifest.get("price_basis"),
        "development_cutoff": DEVELOPMENT_END, "legacy_observed_test_not_used": True,
        "validation_periods": ["2021", "2022", "2023"], "model": "lightgbm.LGBMRegressor",
        "model_parameters": params, "random_shuffle": False, "folds": fold_results,
        "cross_fold_aggregate": _aggregate_fold_metrics(fold_results),
        "feature_importance_stability": _importance_stability(fold_results),
        "prediction_distribution_stability": _prediction_stability(fold_results),
        "overlap_diagnostic": "Newey-West lag=5 adjusts the daily mean diagnostic for overlapping 5-observation labels; not formal significance or investment evidence.",
    }
    _write_json(experiment_dir / "manifest.json", manifest)
    return manifest, fold_results


def _common_sample_results(v1_folds: list[dict[str, object]], v2_folds: list[dict[str, object]]) -> dict[str, object]:
    output = {}
    for v1_result, v2_result in zip(v1_folds, v2_folds, strict=True):
        if v1_result["fold_id"] != v2_result["fold_id"]:
            raise ValueError("Feature versions returned mismatched walk-forward fold order.")
        left = pd.DataFrame(v1_result["native_predictions"])
        right = pd.DataFrame(v2_result["native_predictions"])
        keys = ["symbol", "prediction_date"]
        joined = left.merge(right, on=keys, suffixes=("_v1", "_v2"), how="inner", validate="one_to_one")
        if not np.allclose(joined["target_5d_v1"], joined["target_5d_v2"], rtol=0, atol=1e-12):
            raise ValueError(f"{v1_result['fold_id']} v1/v2 common sample targets differ.")
        if not joined["label_end_date_v1"].equals(joined["label_end_date_v2"]):
            raise ValueError(f"{v1_result['fold_id']} v1/v2 common sample label-end dates differ.")
        common = pd.DataFrame({
            "symbol": joined["symbol"], "prediction_date": joined["prediction_date"],
            "label_end_date": joined["label_end_date_v1"], "target_5d": joined["target_5d_v1"],
            "lightgbm_v1": joined["lightgbm_v1"], "lightgbm_v2": joined["lightgbm_v2"],
        })
        common_daily = daily_information_coefficients(common, ["lightgbm_v1", "lightgbm_v2"])
        metrics = {}
        for model_key in ("lightgbm_v1", "lightgbm_v2"):
            daily = common_daily.loc[common_daily["model"] == model_key]
            actual = common["target_5d"].to_numpy(dtype=float)
            pred = common[model_key].to_numpy(dtype=float)
            pearson = _ic_summary(daily["pearson_ic"])
            rank = _ic_summary(daily["spearman_rank_ic"])
            metrics[model_key] = {
                "rows": int(len(common)), "mae": float(mean_absolute_error(actual, pred)),
                "rmse": float(np.sqrt(mean_squared_error(actual, pred))),
                "pearson_ic": pearson, "rank_ic": rank,
                "newey_west_lag5": {
                    "pearson_ic": newey_west_mean_diagnostic(daily["pearson_ic"], 5),
                    "rank_ic": newey_west_mean_diagnostic(daily["spearman_rank_ic"], 5),
                },
            }
        output[v1_result["fold_id"]] = {
            "same_sample_keys": True, "common_validation_rows": int(len(common)),
            "native_v1_rows": len(left), "native_v2_rows": len(right), "metrics": metrics,
            "sample_keys_sha256": hashlib.sha256(
                common[keys].astype(str).to_csv(index=False).encode("utf-8")
            ).hexdigest(),
        }
        common.to_parquet(Path(v1_result["common_output_path"]), index=False)
        common_daily.to_csv(Path(v1_result["common_daily_output_path"]), index=False)
    return output


def _native_outputs(manifest: dict[str, object], fold_results: list[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    root = Path(manifest["experiment_path"])
    for fold in fold_results:
        prediction_path = Path(fold["files"]["validation_predictions.parquet"])
        prediction = pd.read_parquet(prediction_path)
        fold_root = prediction_path.parent
        output.append({
            "fold_id": fold["fold_id"], "metrics": fold["metrics"],
            "native_predictions": prediction.to_dict("records"),
            "common_output_path": str((root / fold["fold_id"] / "common_sample_predictions.parquet").resolve()),
            "common_daily_output_path": str((root / fold["fold_id"] / "common_sample_daily_ic.csv").resolve()),
            "fold_path": str(fold_root.resolve()),
        })
    return output


def _candidate_assessment(
    v1: dict[str, object], v2: dict[str, object], common_aggregate: dict[str, object],
    common_folds: dict[str, object],
) -> dict[str, object]:
    agg1, agg2 = common_aggregate["lightgbm_v1"], common_aggregate["lightgbm_v2"]
    v2_rank_folds = [fold["metrics"]["lightgbm_v2"]["rank_ic"]["mean"] for fold in common_folds.values()]
    v2_pos_fold_count = sum(value is not None and value > 0 for value in v2_rank_folds)
    v1_sign, v2_sign = agg1["rank_ic_mean"]["sign_consistency"], agg2["rank_ic_mean"]["sign_consistency"]
    v1_rank_std, v2_rank_std = agg1["rank_ic_mean"]["std"], agg2["rank_ic_mean"]["std"]
    v1_pos_days = agg1["positive_rank_ic_day_ratio"]["mean"]
    v2_pos_days = agg2["positive_rank_ic_day_ratio"]["mean"]
    v2_distribution_stable = all(
        fold["metrics"]["prediction_to_target_std_ratio"] is not None
        and fold["metrics"]["prediction_to_target_std_ratio"] >= 0.1
        for fold in v2["folds"]
    )
    conditions = {
        "positive_rank_ic_in_at_least_two_folds": v2_pos_fold_count >= 2,
        "rank_ic_sign_consistency_not_worse_than_v1": v2_sign is not None and v1_sign is not None and v2_sign >= v1_sign,
        "cross_fold_rank_ic_mean_dispersion_not_worse_than_v1": v2_rank_std is not None and v1_rank_std is not None and v2_rank_std <= v1_rank_std,
        "positive_rank_ic_day_ratio_not_worse_than_v1": v2_pos_days is not None and v1_pos_days is not None and v2_pos_days >= v1_pos_days,
        "feature_importance_rank_stability_not_worse_than_v1": (
            _mean_rank_stability(v2["feature_importance_stability"])
            >= _mean_rank_stability(v1["feature_importance_stability"])
        ),
        "prediction_distribution_not_near_constant": v2_distribution_stable,
        "absolute_scale_features_excluded_by_v2_schema": True,
    }
    accepted = all(conditions.values())
    return {
        "status": "candidate_for_larger_universe" if accepted else "development_only",
        "conditions": conditions,
        "interpretation": "Research screening only. No IC magnitude threshold is used; no test-set selection was performed.",
    }


def _mean_rank_stability(importance: dict[str, object]) -> float:
    correlations = [value for value in importance["pairwise_rank_correlations"].values() if value is not None]
    return float(np.mean(correlations)) if correlations else 0.0


def _common_cross_fold_aggregate(common: dict[str, object]) -> dict[str, object]:
    output = {}
    for model in ("lightgbm_v1", "lightgbm_v2"):
        blocks = [fold["metrics"][model] for fold in common.values()]
        fields = {
            "mae": lambda block: block["mae"], "rmse": lambda block: block["rmse"],
            "pearson_ic_mean": lambda block: block["pearson_ic"]["mean"],
            "rank_ic_mean": lambda block: block["rank_ic"]["mean"],
            "icir": lambda block: block["pearson_ic"]["icir"],
            "rank_icir": lambda block: block["rank_ic"]["icir"],
            "positive_ic_day_ratio": lambda block: block["pearson_ic"]["positive_day_ratio"],
            "positive_rank_ic_day_ratio": lambda block: block["rank_ic"]["positive_day_ratio"],
        }
        values_out = {}
        for name, getter in fields.items():
            values = np.asarray([getter(block) for block in blocks], dtype=float)
            values = values[np.isfinite(values)]
            pos, neg = int((values > 0).sum()), int((values < 0).sum())
            values_out[name] = {
                "mean": float(values.mean()) if len(values) else None,
                "median": float(np.median(values)) if len(values) else None,
                "std": float(np.std(values, ddof=1)) if len(values) > 1 else (0.0 if len(values) else None),
                "min": float(values.min()) if len(values) else None,
                "max": float(values.max()) if len(values) else None,
                "sign_consistency": max(pos, neg) / len(values) if len(values) else None,
                "fold_count": int(len(values)),
            }
        output[model] = values_out
    return output


def run_walk_forward_experiment(
    *, dataset_v1_path: Path | str, manifest_v1_path: Path | str,
    dataset_v2_path: Path | str, manifest_v2_path: Path | str,
    root: Path | str | None = None,
) -> dict[str, object]:
    """Compare v1/v2 on pre-2024 expanding folds; never access or score observed holdout rows."""
    root = Path(root) if root else project_root()
    paths = []
    for value in (dataset_v1_path, manifest_v1_path, dataset_v2_path, manifest_v2_path):
        path = Path(value)
        paths.append(path if path.is_absolute() else root / path)
    v1_data_path, v1_manifest_path, v2_data_path, v2_manifest_path = paths
    manifests = [_read_json(v1_manifest_path), _read_json(v2_manifest_path)]
    for manifest, expected in zip(manifests, ("ashare_daily_v1", "ashare_daily_v2_scale_invariant"), strict=True):
        if manifest.get("feature_version") != expected:
            raise ValueError(f"Expected dataset feature_version={expected!r}.")
        if manifest.get("provider") != "baostock" or manifest.get("adjustment") != "qfq":
            raise ValueError("Walk-forward comparison requires the same BaoStock qfq research basis.")
        if manifest.get("target_horizon_observations") != 5:
            raise ValueError("Walk-forward comparison requires the existing five-observation target.")
    if manifests[0].get("pool_id") != manifests[1].get("pool_id"):
        raise ValueError("Feature versions must use the same frozen research universe.")
    if not (v1_data_path.is_file() and v1_manifest_path.is_file() and v2_data_path.is_file() and v2_manifest_path.is_file()):
        raise FileNotFoundError("Both feature-version datasets and manifests must exist.")

    # v1 is a legacy dataset containing the observed test; Arrow filters materialize only development rows.
    v1 = _read_sealed_development_parquet(v1_data_path)
    v2 = _read_sealed_development_parquet(v2_data_path)
    if (pd.to_datetime(v2["prediction_date"]) >= DEVELOPMENT_CUTOFF).any():
        raise ValueError("Feature v2 data extends into the sealed legacy observed test.")
    columns_v1, columns_v2 = manifests[0]["feature_columns"], manifests[1]["feature_columns"]
    expected_v2 = {
        "Return_1d", "Return_5d", "Return_10d", "Return_20d", "Close_to_MA5", "Close_to_MA10",
        "Close_to_MA20", "Close_to_MA60", "RSI_14", "MACD_to_Close", "MACD_Signal_to_Close",
        "Volatility_5d", "Volatility_20d", "Volume_to_MA5", "Volume_to_MA20", "Volume_Change_1d",
    }
    if set(columns_v2) != expected_v2 or set(columns_v2) & set(FEATURE_V1_SCALE_DEPENDENT):
        raise ValueError("Feature v2 manifest violates the scale-invariant feature contract.")
    if manifests[0].get("target") != manifests[1].get("target"):
        raise ValueError("Feature v1 and v2 must share the same target definition.")
    v1_development_manifest = _write_v1_development_snapshot(v1, manifests[0], root)
    params = fixed_lightgbm_parameters(root / "config" / "research.json")
    calendar = AShareTradingCalendar("2018-01-01", DEVELOPMENT_END)
    folds_v1 = split_walk_forward(v1, columns_v1, calendar, embargo_sessions=5)
    folds_v2 = split_walk_forward(v2, columns_v2, calendar, embargo_sessions=5)
    common_alignment = {}
    for left, right in zip(folds_v1, folds_v2, strict=True):
        common_train = left.train.merge(right.train, on=["symbol", "prediction_date"], suffixes=("_v1", "_v2"),
                                        how="inner", validate="one_to_one")
        common_val = left.validation.merge(right.validation, on=["symbol", "prediction_date"], suffixes=("_v1", "_v2"),
                                           how="inner", validate="one_to_one")
        if not np.allclose(common_train["target_5d_v1"], common_train["target_5d_v2"], rtol=0, atol=1e-12):
            raise ValueError(f"{left.fold_id} v1/v2 common training targets differ.")
        if not np.allclose(common_val["target_5d_v1"], common_val["target_5d_v2"], rtol=0, atol=1e-12):
            raise ValueError(f"{left.fold_id} v1/v2 common validation targets differ.")
        if not common_train["label_end_date_v1"].equals(common_train["label_end_date_v2"]):
            raise ValueError(f"{left.fold_id} common training label end dates differ.")
        if not common_val["label_end_date_v1"].equals(common_val["label_end_date_v2"]):
            raise ValueError(f"{left.fold_id} common validation label end dates differ.")
        if common_train.empty or common_val.empty:
            raise ValueError(f"{left.fold_id} has no common v1/v2 train and validation sample keys.")
        common_alignment[left.fold_id] = {
            "common_train_rows": int(len(common_train)), "common_validation_rows": int(len(common_val)),
            "same_train_targets_and_label_end_dates": True,
            "same_validation_targets_and_label_end_dates": True,
            "model_training_samples": "Each feature model is fit on its own feature-complete train rows; common metrics below evaluate identical validation keys.",
        }

    experiments_root = root / "artifacts" / "experiments"
    manifests_out = []
    fold_outs = []
    for label, version, columns, dataset, source_manifest, fold_splits in (
        ("WalkForward-v1", manifests[0]["feature_version"], columns_v1, v1, v1_development_manifest, folds_v1),
        ("WalkForward-v2-scale-invariant", manifests[1]["feature_version"], columns_v2, v2, manifests[1], folds_v2),
    ):
        manifest, fold_results = _fit_feature_experiment(
            label=label, version=version, feature_columns=columns, dataset=dataset,
            dataset_manifest=source_manifest, params=params, folds=fold_splits, output_dir=experiments_root,
        )
        experiment_dir = experiments_root / manifest["experiment_id"]
        manifest["experiment_path"] = str(experiment_dir.resolve())
        _write_json(experiment_dir / "manifest.json", manifest)
        manifests_out.append(manifest)
        fold_outs.append(_native_outputs(manifest, fold_results))

    common = _common_sample_results(fold_outs[0], fold_outs[1])
    common_aggregate = _common_cross_fold_aggregate(common)
    scale_audit = _feature_scale_audit(v1, root=root)
    v1_manifest = manifests_out[0]
    v2_manifest = manifests_out[1]
    comparison_id = uuid.uuid4().hex[:12]
    report = {
        "comparison_id": comparison_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "development_walk_forward", "legacy_observed_test_id": LEGACY_BASELINE_ID,
        "legacy_observed_test_role": "legacy_observed_test",
        "legacy_observed_test_accessed": False,
        "future_sealed_holdout": {"start": "2026-01-01", "role": "future_sealed_holdout", "used": False},
        "development_date_cap": DEVELOPMENT_END,
        "feature_scale_audit": scale_audit,
        "target_version": TARGET_VERSION,
        "model_parameters_identical": v1_manifest["model_parameters"] == v2_manifest["model_parameters"],
        "feature_v1": {key: v1_manifest[key] for key in (
            "experiment_id", "feature_version", "dataset_version", "cross_fold_aggregate",
            "feature_importance_stability", "prediction_distribution_stability", "folds")},
        "feature_v2": {key: v2_manifest[key] for key in (
            "experiment_id", "feature_version", "dataset_version", "cross_fold_aggregate",
            "feature_importance_stability", "prediction_distribution_stability", "folds")},
        "common_sample_metrics": common,
        "common_sample_alignment": common_alignment,
        "common_sample_cross_fold_aggregate": common_aggregate,
        "feature_importance_distribution_comparison": {
            "v1_mean_top5_gain_share": v1_manifest["feature_importance_stability"]["mean_top5_gain_share"],
            "v2_mean_top5_gain_share": v2_manifest["feature_importance_stability"]["mean_top5_gain_share"],
            "lower_top5_share_means_gain_is_more_diffuse_across_features": True,
        },
        "candidate_assessment": _candidate_assessment(v1_manifest, v2_manifest, common_aggregate, common),
        "point_in_time_warning": manifests[1].get("point_in_time_limit"),
        "no_symbol_identity_feature": True,
        "no_2024_or_later_predictions_or_metrics": True,
        "interpretation": "Development validation diagnostics only; monthly, quarterly, NW, and importance outputs are not used for month/feature selection.",
    }
    report_path = root / "artifacts" / "reports" / f"walkforward_comparison_{comparison_id}.json"
    _write_json(report_path, report)
    return {"status": "complete", "report_path": str(report_path.resolve()), "report": report}
