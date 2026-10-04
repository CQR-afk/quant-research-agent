"""Fixed LightGBM target comparison on the sealed pre-2024 A-share development panel."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from lightgbm import LGBMRegressor
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error

from quantlab.experiments import daily_information_coefficients
from quantlab.research.dataset import project_root
from quantlab.research.features import FEATURE_COLUMNS as FEATURE_V1_COLUMNS
from quantlab.research.targets import (
    CS_RANK_TARGET_VERSION,
    DEFAULT_MINIMUM_TARGET_CROSS_SECTION,
    POOL_EXCESS_TARGET_VERSION,
    RAW_TARGET_VERSION,
    TARGET_COLUMNS,
    TARGET_VERSIONS,
    build_cross_sectional_targets,
    target_definitions,
)
from quantlab.research.walk_forward import (
    DEVELOPMENT_END,
    _ic_summary,
    _period_ic,
    fixed_lightgbm_parameters,
    newey_west_mean_diagnostic,
)
from quantlab.research.walkforward_splits import (
    DEVELOPMENT_CUTOFF,
    split_walk_forward,
    validate_development_dates,
)
from quantlab.research.calendar import AShareTradingCalendar

LEGACY_BASELINE_ID = "20261004T120849Z_2cb6448746"
TARGET_FILE_STEMS = {
    RAW_TARGET_VERSION: "ashare_research_v1_target_raw_dev",
    POOL_EXCESS_TARGET_VERSION: "ashare_research_v1_target_pool_excess_dev",
    CS_RANK_TARGET_VERSION: "ashare_research_v1_target_cs_rank_dev",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _stable_dataset_hash(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
    content = ordered.to_csv(index=False, na_rep="", date_format="%Y-%m-%d").encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _read_development_source(dataset_path: Path, manifest_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = _read_json(manifest_path)
    if manifest.get("feature_version") != "ashare_daily_v1":
        raise ValueError("Target study fixes Primary Feature Set=ashare_daily_v1.")
    if manifest.get("target_version") != RAW_TARGET_VERSION:
        raise ValueError(f"Target study control must be {RAW_TARGET_VERSION}.")
    if manifest.get("evaluation_role") != "development_walk_forward":
        raise ValueError("Target study requires an explicitly materialized development-only dataset.")
    if manifest.get("legacy_observed_test_rows_materialized") is not False:
        raise ValueError("Target study refuses source datasets that may contain legacy test rows.")
    if manifest.get("provider") != "baostock" or manifest.get("adjustment") != "qfq":
        raise ValueError("Target study requires the frozen BaoStock qfq development data basis.")
    end = pd.Timestamp(manifest.get("research_end", "1900-01-01"))
    if end > pd.Timestamp(DEVELOPMENT_END):
        raise ValueError("Target study source manifest extends beyond the 2023 development cap.")
    frame = pq.read_table(dataset_path, filters=[
        ("prediction_date", "<", DEVELOPMENT_CUTOFF.to_pydatetime()),
        ("label_end_date", "<", DEVELOPMENT_CUTOFF.to_pydatetime()),
    ]).to_pandas()
    validate_development_dates(frame)
    if (pd.to_datetime(frame["prediction_date"]) >= DEVELOPMENT_CUTOFF).any():
        raise ValueError("Target study refuses 2024+ prediction rows.")
    if manifest.get("feature_columns") != FEATURE_V1_COLUMNS:
        raise ValueError("Target study accepts only the frozen ashare_daily_v1 feature whitelist.")
    if not set(FEATURE_V1_COLUMNS).isdisjoint(set(TARGET_COLUMNS.values()) | {"target_5d", "label_end_date", "raw_target_5d"}):
        raise ValueError("Target labels or label metadata overlap the frozen model feature set.")
    if not set(FEATURE_V1_COLUMNS) <= set(frame.columns):
        raise ValueError("Target study source is missing declared feature columns.")
    return frame, manifest


def _target_frame_for_version(enriched: pd.DataFrame, version: str, feature_columns: list[str]) -> pd.DataFrame:
    column = TARGET_COLUMNS[version]
    output = enriched.copy()
    # `study_target` is the only selected label used by fit/evaluation code.
    output["study_target"] = output[column]
    selected_columns = [
        *feature_columns, "symbol", "prediction_date", "label_end_date", "target_5d",
        *[name for name in TARGET_COLUMNS.values() if name != "target_5d"],
        "study_target",
    ]
    # Keep provenance and PIT context needed for diagnostics, while excluding unrelated columns.
    for extra in ("market", "exchange", "source", "adjustment", "currency", "bar_record_version",
                  "ingest_id", "downloaded_at", "feature_version", "feature_parameters"):
        if extra in output.columns:
            selected_columns.append(extra)
    return output.loc[:, list(dict.fromkeys(selected_columns))].sort_values(
        ["prediction_date", "symbol"], kind="stable"
    ).reset_index(drop=True)


def materialize_target_datasets(
    *,
    dataset_path: Path | str,
    manifest_path: Path | str,
    root: Path | str | None = None,
    minimum_cross_section: int = DEFAULT_MINIMUM_TARGET_CROSS_SECTION,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build immutable target-versioned Parquet panels from development-only v1 data."""
    root = Path(root) if root else project_root()
    source_path = Path(dataset_path)
    source_manifest_path = Path(manifest_path)
    if not source_path.is_absolute():
        source_path = root / source_path
    if not source_manifest_path.is_absolute():
        source_manifest_path = root / source_manifest_path
    if not source_path.is_file() or not source_manifest_path.is_file():
        raise FileNotFoundError("The development v1 source Parquet and manifest must exist.")
    source, source_manifest = _read_development_source(source_path, source_manifest_path)
    feature_columns = list(source_manifest["feature_columns"])
    built = build_cross_sectional_targets(
        source, feature_columns=feature_columns, minimum_cross_section=minimum_cross_section,
    )
    label_end_original = pd.to_datetime(source["label_end_date"], errors="coerce")
    label_end_built = pd.to_datetime(built.dataset["label_end_date"], errors="coerce")
    if not label_end_original.equals(label_end_built):
        raise ValueError("Target versions must preserve the raw target label_end_date exactly.")
    root_data = Path(output_dir) if output_dir else root / "data" / "processed"
    results: dict[str, Any] = {"daily_diagnostics": built.daily_diagnostics, "datasets": {}}
    for version in TARGET_VERSIONS:
        target_frame = _target_frame_for_version(built.dataset, version, feature_columns)
        dataset_version = _stable_dataset_hash(target_frame)
        stem = TARGET_FILE_STEMS[version]
        output = root_data / f"{stem}.parquet"
        manifest_path_out = root_data / f"{stem}.manifest.json"
        if output.exists():
            existing = pd.read_parquet(output)
            if _stable_dataset_hash(existing) != dataset_version:
                raise FileExistsError(
                    f"Refusing to overwrite immutable target dataset {output}; use a new output path/version."
                )
        else:
            root_data.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.parquet")
            target_frame.to_parquet(temporary, index=False)
            temporary.replace(output)
        definition = built.target_definitions[version]
        manifest: dict[str, Any] = {
            "dataset_id": f"target_dataset_{dataset_version[:16]}",
            "dataset_version": dataset_version,
            "source_dataset_version": source_manifest.get("dataset_version"),
            "source_dataset_path": str(source_path.resolve()),
            "dataset_path": str(output.resolve()),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "market": source_manifest.get("market"), "provider": source_manifest.get("provider"),
            "adjustment": source_manifest.get("adjustment"),
            "price_basis": source_manifest.get("price_basis"),
            "pool_id": source_manifest.get("pool_id"),
            "pool_size": source_manifest.get("pool_size"),
            "feature_version": "ashare_daily_v1", "feature_columns": feature_columns,
            "target_version": version, "target_column": TARGET_COLUMNS[version],
            "study_target_column": "study_target", "raw_target_column": "target_5d",
            "target_definition": definition["definition"],
            "target_horizon_observations": 5,
            "target_cross_section_method": definition["target_cross_section_method"],
            "cross_section_center": definition.get("cross_section_center"),
            "rank_method": definition.get("rank_method"), "rank_pct": definition.get("rank_pct"),
            "rank_scaling": definition.get("rank_scaling"),
            "minimum_target_cross_section": definition["minimum_target_cross_section"],
            "development_date_cap": DEVELOPMENT_END,
            "evaluation_role": "development_walk_forward",
            "legacy_observed_test_rows_materialized": False,
            "future_sealed_holdout": {"start": "2026-01-01", "used": False},
            "dataset_rows": int(len(target_frame)),
            "native_target_rows": int(target_frame["study_target"].notna().sum()),
            "unavailable_target_rows": int(target_frame["study_target"].isna().sum()),
            "unavailable_cross_section_dates": int((~built.daily_diagnostics["cross_section_target_available"]).sum()),
            "minimum_cross_section_policy": "Targets B/C are NaN for all rows on dates with fewer than minimum valid realized raw returns; no fill is applied.",
            "label_end_date_invariant": True,
            "point_in_time_limit": source_manifest.get("point_in_time_limit"),
            "survivorship_bias_note": "Frozen 11-stock engineering universe; not a market-wide pool.",
        }
        if manifest_path_out.exists():
            old = _read_json(manifest_path_out)
            if old.get("dataset_version") != dataset_version or old.get("target_version") != version:
                raise FileExistsError(f"Refusing to replace mismatched immutable manifest {manifest_path_out}.")
        else:
            _write_json_atomic(manifest_path_out, manifest)
        results["datasets"][version] = {
            "dataset": target_frame, "manifest": manifest,
            "dataset_path": output, "manifest_path": manifest_path_out,
        }
    return results


def _distribution(values: pd.Series | np.ndarray) -> dict[str, float | int | None]:
    series = pd.to_numeric(pd.Series(values), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if series.empty:
        return {key: None for key in ("mean", "std", "min", "p01", "p10", "median", "p90", "p99", "max")} | {"count": 0}
    return {
        "count": int(len(series)), "mean": float(series.mean()), "std": float(series.std(ddof=1)) if len(series) > 1 else 0.0,
        "min": float(series.min()), "p01": float(series.quantile(.01)), "p10": float(series.quantile(.10)),
        "median": float(series.median()), "p90": float(series.quantile(.90)),
        "p99": float(series.quantile(.99)), "max": float(series.max()),
    }


def _daily_summary(predictions: pd.DataFrame, score_column: str = "model_score") -> tuple[pd.DataFrame, dict[str, Any]]:
    evaluation = predictions.loc[:, ["symbol", "prediction_date", "raw_target_5d", score_column]].rename(
        columns={"raw_target_5d": "target_5d", score_column: "lightgbm"}
    )
    daily = daily_information_coefficients(evaluation, ["lightgbm"])
    pearson = _ic_summary(daily["pearson_ic"])
    rank = _ic_summary(daily["spearman_rank_ic"])
    metrics = {
        "pearson_ic": pearson,
        "rank_ic": rank,
        "newey_west_lag5": {
            "pearson_ic": newey_west_mean_diagnostic(daily["pearson_ic"], 5),
            "rank_ic": newey_west_mean_diagnostic(daily["spearman_rank_ic"], 5),
            "interpretation": "Diagnostic only; not formal statistical proof or investment validity.",
        },
        "monthly_rank_ic": _period_ic(daily, "M").loc[:, ["period", "rank_mean", "rank_positive_day_ratio", "rank_valid_days"]].to_dict("records"),
        "quarterly_rank_ic": _period_ic(daily, "Q").loc[:, ["period", "rank_mean", "rank_positive_day_ratio", "rank_valid_days"]].to_dict("records"),
        "daily_ic_rows": int(len(daily)),
    }
    return daily, metrics


def _native_fit_metrics(target: pd.Series, score: pd.Series, version: str) -> dict[str, Any]:
    truth = pd.to_numeric(target, errors="coerce").to_numpy(dtype=float)
    predicted = pd.to_numeric(score, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(truth) & np.isfinite(predicted)
    if not valid.any():
        raise ValueError("Native target fit diagnostics have no finite observations.")
    truth, predicted = truth[valid], predicted[valid]
    return {
        "units": version,
        "comparability": "NOT CROSS-TARGET COMPARABLE",
        "mae": float(mean_absolute_error(truth, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(truth, predicted))),
        "target_distribution": _distribution(truth),
        "prediction_distribution": _distribution(predicted),
    }


def _align_common_validation_samples(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Inner-align A/B/C on security, prediction day and unchanged label-end date."""
    ordered_versions = list(frames)
    if not ordered_versions:
        raise ValueError("At least one validation prediction frame is required.")
    key_columns = ["symbol", "prediction_date", "label_end_date"]
    reference = frames[ordered_versions[0]][[*key_columns, "raw_target_5d"]].copy()
    if reference.duplicated(key_columns).any():
        raise ValueError("Validation predictions contain duplicate sample keys.")
    for version in ordered_versions[1:]:
        other = frames[version][[*key_columns, "raw_target_5d"]].copy()
        if other.duplicated(key_columns).any():
            raise ValueError("Validation predictions contain duplicate sample keys.")
        aligned = reference.merge(other, on=key_columns, how="inner", validate="one_to_one", suffixes=("_ref", "_other"))
        if not np.allclose(aligned["raw_target_5d_ref"], aligned["raw_target_5d_other"], rtol=0, atol=1e-12):
            raise ValueError(f"Common validation keys have different raw target_5d values ({version}).")
        reference = aligned[[*key_columns, "raw_target_5d_ref"]].rename(columns={"raw_target_5d_ref": "raw_target_5d"})
    return reference.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)


def _verify_unused_holdout(governance: dict[str, Any]) -> None:
    holdout = governance.get("sealed_holdout", {})
    if holdout.get("role") != "future_sealed_holdout" or holdout.get("used") is not False or holdout.get("data_requested") is not False:
        raise ValueError("Governance check failed: future sealed holdout must remain unused and unrequested.")


def _fit_target_experiment(
    *, version: str, dataset: pd.DataFrame, source_manifest: dict[str, Any], params: dict[str, Any],
    root: Path, calendar: AShareTradingCalendar,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    features = list(source_manifest["feature_columns"])
    target_column = "study_target"
    if set(features) & set(TARGET_COLUMNS.values()) or target_column in features:
        raise ValueError("Target values cannot enter the LightGBM feature matrix.")
    if version == RAW_TARGET_VERSION and not dataset[target_column].equals(dataset["target_5d"]):
        raise ValueError("Raw Target A must remain identical to the original target_5d control.")
    split_frame = dataset.copy()
    split_frame["raw_target_5d"] = split_frame["target_5d"]
    split_frame["target_5d"] = split_frame[target_column]
    folds = split_walk_forward(split_frame, features, calendar, embargo_sessions=5)

    run_id = uuid.uuid4().hex[:12]
    slug = {
        RAW_TARGET_VERSION: "raw_v1", POOL_EXCESS_TARGET_VERSION: "pool_excess_v1",
        CS_RANK_TARGET_VERSION: "cs_rank_v1",
    }[version]
    experiment_id = f"walkforward_target_{slug}_{run_id}"
    experiment_dir = root / "artifacts" / "experiments" / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)
    fold_manifests: list[dict[str, Any]] = []
    predictions_by_fold: dict[str, pd.DataFrame] = {}
    for fold in folds:
        train, validation = fold.train, fold.validation
        if train.empty or validation.empty:
            raise ValueError(f"{version} {fold.fold_id} has no native train/validation rows.")
        model = LGBMRegressor(**params)
        model.fit(train[features], train[target_column])
        predictions = validation[["symbol", "prediction_date", "label_end_date", "raw_target_5d", target_column]].copy()
        predictions = predictions.rename(columns={target_column: "native_target"})
        predictions["model_score"] = model.predict(validation[features])
        native_fit = _native_fit_metrics(predictions["native_target"], predictions["model_score"], version)
        daily, common_metrics_native = _daily_summary(predictions)
        fold_dir = experiment_dir / fold.fold_id
        fold_dir.mkdir(parents=True, exist_ok=False)
        predictions.to_parquet(fold_dir / "validation_predictions.parquet", index=False)
        daily.to_csv(fold_dir / "daily_ic_vs_raw_return.csv", index=False)
        joblib.dump(model, fold_dir / "lightgbm.joblib")
        importance = pd.DataFrame({
            "feature": features,
            "gain": model.booster_.feature_importance(importance_type="gain"),
            "split": model.booster_.feature_importance(importance_type="split"),
        }).sort_values("gain", ascending=False)
        importance.to_csv(fold_dir / "feature_importance.csv", index=False)
        fold_manifests.append({
            "fold_id": fold.fold_id,
            "split": fold.metadata,
            "native_validation_rows": int(len(predictions)),
            "native_fit_metrics": native_fit,
            "score_vs_raw_return_metrics_native_samples": common_metrics_native,
            "feature_importance": importance.to_dict("records"),
            "prediction_file": str((fold_dir / "validation_predictions.parquet").resolve()),
            "daily_ic_file": str((fold_dir / "daily_ic_vs_raw_return.csv").resolve()),
            "importance_file": str((fold_dir / "feature_importance.csv").resolve()),
        })
        predictions_by_fold[fold.fold_id] = predictions
    manifest = {
        "experiment_id": experiment_id,
        "name": f"walkforward_target_{slug}",
        "evaluation_role": "development_walk_forward",
        "feature_version": "ashare_daily_v1", "feature_columns": features,
        "target_version": version,
        "dataset_version": source_manifest["dataset_version"],
        "target_definition": source_manifest["target_definition"],
        "target_horizon_observations": 5,
        "target_cross_section_method": source_manifest.get("target_cross_section_method"),
        "minimum_target_cross_section": source_manifest.get("minimum_target_cross_section"),
        "development_date_cap": DEVELOPMENT_END,
        "legacy_observed_test_accessed": False,
        "future_sealed_holdout": {"start": "2026-01-01", "used": False},
        "model": "LightGBM LGBMRegressor",
        "model_parameters": params,
        "native_metrics_notice": "MAE/RMSE are in this target's units and are NOT CROSS-TARGET COMPARABLE.",
        "folds": fold_manifests,
        "experiment_path": str(experiment_dir.resolve()),
    }
    _write_json_atomic(experiment_dir / "manifest.json", manifest)
    return manifest, predictions_by_fold


def _common_prediction_metrics(predictions: pd.DataFrame) -> dict[str, Any]:
    daily, metrics = _daily_summary(predictions)
    raw = predictions["raw_target_5d"].to_numpy(dtype=float)
    score = predictions["model_score"].to_numpy(dtype=float)
    metrics.update({
        "sample_rows": int(len(predictions)),
        "symbols": int(predictions["symbol"].nunique()),
        "prediction_dates": int(predictions["prediction_date"].nunique()),
        "raw_target_distribution": _distribution(raw),
        "score_distribution": _distribution(score),
        "mae_rmse": "NOT REPORTED: score is not a return estimate for cross-sectional targets.",
    })
    return metrics


def _fold_aggregate(common_metrics: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "rank_ic_mean": lambda x: x["rank_ic"]["mean"],
        "rank_icir": lambda x: x["rank_ic"]["icir"],
        "positive_rank_ic_day_ratio": lambda x: x["rank_ic"]["positive_day_ratio"],
        "pearson_ic_mean": lambda x: x["pearson_ic"]["mean"],
        "icir": lambda x: x["pearson_ic"]["icir"],
        "positive_pearson_ic_day_ratio": lambda x: x["pearson_ic"]["positive_day_ratio"],
    }
    result = {}
    for key, getter in fields.items():
        values = np.asarray([getter(value) for value in common_metrics.values()], dtype=float)
        values = values[np.isfinite(values)]
        result[key] = {
            "mean": float(values.mean()) if len(values) else None,
            "median": float(np.median(values)) if len(values) else None,
            "std": float(np.std(values, ddof=1)) if len(values) > 1 else (0.0 if len(values) else None),
            "min": float(values.min()) if len(values) else None,
            "max": float(values.max()) if len(values) else None,
            "sign_consistency": float(max((values > 0).sum(), (values < 0).sum()) / len(values)) if len(values) else None,
            "fold_count": int(len(values)),
        }
    ranks = np.asarray([getter(common_metrics[fold]) for fold in common_metrics
                        for getter in [lambda x: x["rank_ic"]["mean"]]], dtype=float)
    result["positive_rank_ic_folds"] = int(np.sum(ranks > 0))
    result["negative_rank_ic_folds"] = int(np.sum(ranks < 0))
    return result


def _candidate_assessment(
    common_by_target: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw = common_by_target[RAW_TARGET_VERSION]
    raw_agg = _fold_aggregate(raw)
    candidate_checks: dict[str, dict[str, Any]] = {}
    for version in (POOL_EXCESS_TARGET_VERSION, CS_RANK_TARGET_VERSION):
        aggregate = _fold_aggregate(common_by_target[version])
        conditions = {
            "positive_rank_ic_in_at_least_two_folds": aggregate["positive_rank_ic_folds"] >= 2,
            "mean_rank_ic_not_below_raw_control": (
                aggregate["rank_ic_mean"]["mean"] is not None
                and raw_agg["rank_ic_mean"]["mean"] is not None
                and aggregate["rank_ic_mean"]["mean"] >= raw_agg["rank_ic_mean"]["mean"]
            ),
            "positive_rank_ic_day_ratio_not_below_raw_control": (
                aggregate["positive_rank_ic_day_ratio"]["mean"] is not None
                and raw_agg["positive_rank_ic_day_ratio"]["mean"] is not None
                and aggregate["positive_rank_ic_day_ratio"]["mean"] >= raw_agg["positive_rank_ic_day_ratio"]["mean"]
            ),
            "not_two_negative_folds_dominated_by_one_fold": aggregate["negative_rank_ic_folds"] < 2,
        }
        candidate_checks[version] = {
            "conditions": conditions,
            "eligible": all(conditions.values()),
            "cross_fold_metrics": aggregate,
        }
    eligible = [version for version, record in candidate_checks.items() if record["eligible"]]
    if eligible:
        selected = max(eligible, key=lambda version: (
            _fold_aggregate(common_by_target[version])["rank_ic_mean"]["mean"],
            _fold_aggregate(common_by_target[version])["positive_rank_ic_day_ratio"]["mean"],
        ))
        status = "pool_excess_candidate" if selected == POOL_EXCESS_TARGET_VERSION else "cs_rank_candidate"
    else:
        selected = RAW_TARGET_VERSION
        status = "no_target_upgrade"
    return {
        "status": status,
        "selected_target": selected,
        "raw_target_retained": selected == RAW_TARGET_VERSION,
        "raw_control_cross_fold_metrics": raw_agg,
        "candidate_checks": candidate_checks,
        "selection_rule": "Only B/C are eligible for selection; all four preregistered conditions must pass. If neither passes, retain raw control. No fixed IC magnitude threshold is used.",
        "interpretation": "Development screening only; no observed test or future holdout was evaluated.",
    }


def run_target_study(
    *,
    dataset_path: Path | str | None = None,
    manifest_path: Path | str | None = None,
    target_version: str = "all",
    root: Path | str | None = None,
    minimum_cross_section: int = DEFAULT_MINIMUM_TARGET_CROSS_SECTION,
) -> dict[str, Any]:
    """Run the fixed three-target study or explicitly materialize/run one target."""
    if target_version != "all" and target_version not in TARGET_VERSIONS:
        raise ValueError(f"target-version must be 'all' or one of: {', '.join(TARGET_VERSIONS)}")
    root = Path(root) if root else project_root()
    source_data = Path(dataset_path) if dataset_path else root / "data" / "processed" / "ashare_research_dataset_baostock_qfq_v1_development_2018_2023.parquet"
    source_manifest = Path(manifest_path) if manifest_path else source_data.with_suffix(".manifest.json")
    materialized = materialize_target_datasets(
        dataset_path=source_data, manifest_path=source_manifest, root=root,
        minimum_cross_section=minimum_cross_section,
    )
    selected = TARGET_VERSIONS if target_version == "all" else (target_version,)
    governance = _read_json(root / "config" / "research_governance.json")
    _verify_unused_holdout(governance)
    params = fixed_lightgbm_parameters(root / "config" / "research.json")
    calendar = AShareTradingCalendar("2018-01-01", DEVELOPMENT_END)
    experiments: dict[str, Any] = {}
    predictions_by_version: dict[str, dict[str, pd.DataFrame]] = {}
    for version in selected:
        dataset_record = materialized["datasets"][version]
        frame, manifest = dataset_record["dataset"], dataset_record["manifest"]
        # Fail closed on PIT horizon and label-end consistency before fitting.
        if manifest.get("target_horizon_observations") != 5 or manifest.get("evaluation_role") != "development_walk_forward":
            raise ValueError("Target dataset manifest violates the fixed development target protocol.")
        if (pd.to_datetime(frame["label_end_date"], errors="coerce") >= DEVELOPMENT_CUTOFF).any():
            raise ValueError("Target labels extend into 2024; refusing to fit.")
        experiment, predictions = _fit_target_experiment(
            version=version, dataset=frame, source_manifest=manifest,
            params=params, root=root, calendar=calendar,
        )
        experiments[version] = experiment
        predictions_by_version[version] = predictions

    comparison_report: dict[str, Any] | None = None
    report_path: Path | None = None
    if target_version == "all":
        if not all(experiments[version]["model_parameters"] == params for version in TARGET_VERSIONS):
            raise AssertionError("Target experiment LightGBM parameters differ.")
        fold_protocol = {
            fold_id: {
                version: next(item["split"] for item in experiments[version]["folds"] if item["fold_id"] == fold_id)
                for version in TARGET_VERSIONS
            }
            for fold_id in ("fold_1", "fold_2", "fold_3")
        }
        expected_schedule_fields = (
            "train_configured_start", "train_configured_end", "validation_configured_start",
            "validation_configured_end", "embargo_sessions",
        )
        for fold_id, splits in fold_protocol.items():
            reference_split = splits[TARGET_VERSIONS[0]]
            for version in TARGET_VERSIONS[1:]:
                if any(splits[version][field] != reference_split[field] for field in expected_schedule_fields):
                    raise AssertionError(f"{fold_id} time boundaries/embargo differ for {version}.")
        common_by_fold: dict[str, dict[str, pd.DataFrame]] = {}
        common_by_target: dict[str, dict[str, Any]] = {version: {} for version in TARGET_VERSIONS}
        alignment: dict[str, Any] = {}
        for fold_id in ("fold_1", "fold_2", "fold_3"):
            frames = {version: predictions_by_version[version][fold_id] for version in TARGET_VERSIONS}
            common_keys = _align_common_validation_samples(frames)
            if common_keys.empty:
                raise ValueError(f"{fold_id} has no A/B/C common validation sample keys.")
            alignment[fold_id] = {
                "native_rows": {version: int(len(frames[version])) for version in TARGET_VERSIONS},
                "native_train_rows": {
                    version: next(item["split"]["train_rows"] for item in experiments[version]["folds"]
                                  if item["fold_id"] == fold_id)
                    for version in TARGET_VERSIONS
                },
                "common_rows": int(len(common_keys)),
                "common_symbols": int(common_keys["symbol"].nunique()),
                "common_prediction_dates": int(common_keys["prediction_date"].nunique()),
                "common_prediction_date_start": pd.Timestamp(common_keys["prediction_date"].min()).date().isoformat(),
                "common_prediction_date_end": pd.Timestamp(common_keys["prediction_date"].max()).date().isoformat(),
                "native_symbols": {version: int(frames[version]["symbol"].nunique()) for version in TARGET_VERSIONS},
                "native_prediction_dates": {version: int(frames[version]["prediction_date"].nunique()) for version in TARGET_VERSIONS},
                "same_symbol_prediction_date_label_end_and_raw_target": True,
            }
            common_by_fold[fold_id] = {}
            for version in TARGET_VERSIONS:
                common = common_keys.merge(
                    frames[version], on=["symbol", "prediction_date", "label_end_date", "raw_target_5d"],
                    how="inner", validate="one_to_one",
                )
                if len(common) != len(common_keys):
                    raise AssertionError("Common prediction alignment lost or duplicated sample keys.")
                common = common.sort_values(["prediction_date", "symbol"], kind="stable").reset_index(drop=True)
                common_by_fold[fold_id][version] = common
                common_by_target[version][fold_id] = _common_prediction_metrics(common)
                common.to_parquet(
                    root / "artifacts" / "experiments" / experiments[version]["experiment_id"] / fold_id / "common_validation_predictions.parquet",
                    index=False,
                )
        target_distribution = {}
        neutrality = {}
        for version in TARGET_VERSIONS:
            col = TARGET_COLUMNS[version]
            version_dataset = materialized["datasets"][version]["dataset"]
            target_distribution[version] = {}
            for fold_id, common in common_by_fold.items():
                common_samples = common
                model_rows = predictions_by_version[version][fold_id]
                target_col = "native_target"
                target_distribution[version][fold_id] = _distribution(model_rows[target_col])
            if version == POOL_EXCESS_TARGET_VERSION:
                available = materialized["daily_diagnostics"].loc[
                    materialized["daily_diagnostics"]["cross_section_target_available"]
                ]
                neutrality[version] = {
                    "available_dates": int(len(available)),
                    "max_absolute_daily_mean": float(available["pool_excess_abs_mean"].max()) if len(available) else None,
                    "minimum_target_cross_section": minimum_cross_section,
                    "all_available_date_means_within_1e-12": bool((available["pool_excess_abs_mean"] <= 1e-12).all()) if len(available) else False,
                }
            elif version == CS_RANK_TARGET_VERSION:
                available = materialized["daily_diagnostics"].loc[
                    materialized["daily_diagnostics"]["cross_section_target_available"]
                ]
                neutrality[version] = {
                    "available_dates": int(len(available)),
                    "all_available_dates_monotonic_with_raw_target": bool(available["rank_target_monotonic_with_raw"].all()) if len(available) else False,
                    "rank_definition": target_definitions(minimum_cross_section)[version],
                }
        comparison_id = uuid.uuid4().hex[:12]
        daily_diagnostic_path = root / "artifacts" / "reports" / f"target_cross_section_diagnostics_{comparison_id}.csv"
        daily_diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        materialized["daily_diagnostics"].to_csv(daily_diagnostic_path, index=False)
        comparison_report = {
            "comparison_id": comparison_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "evaluation_role": "development_walk_forward",
            "development_date_cap": DEVELOPMENT_END,
            "legacy_observed_test_id": LEGACY_BASELINE_ID,
            "legacy_observed_test_accessed": False,
            "future_sealed_holdout": {"start": "2026-01-01", "used": False},
            "feature_version": "ashare_daily_v1",
            "feature_columns": materialized["datasets"][RAW_TARGET_VERSION]["manifest"]["feature_columns"],
            "model": "LightGBM LGBMRegressor",
            "model_parameters_identical_across_targets": all(
                experiments[version]["model_parameters"] == params for version in TARGET_VERSIONS
            ),
            "target_definitions": target_definitions(minimum_cross_section),
            "target_dataset_manifests": {version: materialized["datasets"][version]["manifest"] for version in TARGET_VERSIONS},
            "target_construction_daily_diagnostics_path": str(daily_diagnostic_path.resolve()),
            "experiments": {
                version: {
                    "experiment_id": experiments[version]["experiment_id"],
                    "experiment_path": experiments[version]["experiment_path"],
                    "manifest_path": str((Path(experiments[version]["experiment_path"]) / "manifest.json").resolve()),
                }
                for version in TARGET_VERSIONS
            },
            "sample_alignment": alignment,
            "fold_protocol": {
                fold_id: {
                    version: {field: splits[version][field] for field in (
                        "train_configured_start", "train_configured_end", "validation_configured_start",
                        "validation_configured_end", "train_prediction_start", "train_prediction_end",
                        "validation_prediction_start", "validation_prediction_end", "train_rows",
                        "validation_rows", "purged_train_rows", "purged_validation_rows", "embargo_sessions",
                    )}
                    for version in TARGET_VERSIONS
                }
                for fold_id, splits in fold_protocol.items()
            },
            "common_sample_metrics_vs_raw_return": common_by_target,
            "common_sample_cross_fold_aggregate": {
                version: _fold_aggregate(common_by_target[version]) for version in TARGET_VERSIONS
            },
            "native_target_fit_metrics": {
                version: {
                    fold["fold_id"]: {
                        "rows": fold["native_validation_rows"],
                        "symbols": fold["split"]["validation_symbols"],
                        "prediction_dates": fold["split"]["validation_dates"],
                        **fold["native_fit_metrics"],
                    }
                    for fold in experiments[version]["folds"]
                }
                for version in TARGET_VERSIONS
            },
            "target_distribution_on_native_validation": target_distribution,
            "cross_section_neutrality": neutrality,
            "candidate_assessment": _candidate_assessment(common_by_target),
            "interpretation": "Primary metric is daily Spearman Rank IC between each model score and realized raw target_5d on identical A∩B∩C validation samples. Native MAE/RMSE use each target's own units and are not comparable across targets.",
            "known_limitations": [
                "BaoStock qfq is a vendor-adjusted snapshot, not point-in-time corporate-action reconstruction.",
                "The 11-stock engineering universe has survivorship and selection bias and is not market-wide.",
                "Feature v1 still contains absolute price and volume scale features; this stage changes targets only.",
                "Overlapping five-session labels mean Newey-West lag-5 values are diagnostic only, not proof of significance or investment validity.",
            ],
        }
        report_path = root / "artifacts" / "reports" / f"target_comparison_{comparison_report['comparison_id']}.json"
        _write_json_atomic(report_path, comparison_report)
    return {
        "status": "complete",
        "target_versions_run": list(selected),
        "datasets": {
            version: {
                "dataset_path": str(materialized["datasets"][version]["dataset_path"].resolve()),
                "manifest_path": str(materialized["datasets"][version]["manifest_path"].resolve()),
                "dataset_version": materialized["datasets"][version]["manifest"]["dataset_version"],
            }
            for version in selected
        },
        "experiments": {version: record["experiment_id"] for version, record in experiments.items()},
        "report_path": str(report_path.resolve()) if report_path else None,
        "candidate_assessment": comparison_report.get("candidate_assessment") if comparison_report else None,
        "report": comparison_report,
    }
