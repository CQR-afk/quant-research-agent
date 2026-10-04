"""First LightGBM five-observation return experiment with immutable outputs."""

from __future__ import annotations

import json
import platform
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error

from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import dataset_status, project_root
from quantlab.research.features import FEATURE_COLUMNS
from quantlab.research.readiness import evaluate_readiness, format_readiness_report
from quantlab.research.splits import FixedPeriodSplitter


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _metric_block(actual: np.ndarray, prediction: np.ndarray, daily: pd.DataFrame) -> dict[str, object]:
    daily_pearson = pd.to_numeric(daily["pearson_ic"], errors="coerce").dropna()
    daily_spearman = pd.to_numeric(daily["spearman_rank_ic"], errors="coerce").dropna()
    date_count = int(daily["prediction_date"].nunique()) if len(daily) else 0
    def ic_summary(values: pd.Series) -> dict[str, object]:
        values = pd.to_numeric(values, errors="coerce").dropna()
        std = float(values.std(ddof=1)) if len(values) > 1 else None
        mean = float(values.mean()) if len(values) else None
        return {"mean": mean, "median": float(values.median()) if len(values) else None,
                "std": std, "icir": float(mean / std) if mean is not None and std and std > 0 else None,
                "valid_days": int(len(values))}
    cross_sections = pd.to_numeric(daily.get("sample_count", pd.Series(dtype=float)), errors="coerce")
    return {
        "mae": float(mean_absolute_error(actual, prediction)),
        "rmse": float(np.sqrt(mean_squared_error(actual, prediction))),
        "pearson_ic_summary": ic_summary(daily["pearson_ic"]),
        "spearman_rank_ic_summary": ic_summary(daily["spearman_rank_ic"]),
        "pearson_ic": float(daily_pearson.mean()) if len(daily_pearson) else None,
        "spearman_rank_ic": float(daily_spearman.mean()) if len(daily_spearman) else None,
        "pearson_ic_coverage": float(len(daily_pearson) / date_count) if date_count else 0.0,
        "spearman_ic_coverage": float(len(daily_spearman) / date_count) if date_count else 0.0,
        "prediction_dates": date_count,
        "valid_observations": int(np.isfinite(actual).sum()),
        "na_observations": int(len(actual) - np.isfinite(actual).sum()),
        "average_cross_section": float(cross_sections.mean()) if len(cross_sections) else None,
        "minimum_cross_section": int(cross_sections.min()) if len(cross_sections) else None,
        "dates_with_valid_pearson_ic": int(len(daily_pearson)),
        "dates_with_valid_spearman_ic": int(len(daily_spearman)),
        "pearson_na_reasons": daily.loc[daily["pearson_ic"].isna(), "pearson_reason"].value_counts().to_dict(),
        "spearman_na_reasons": daily.loc[daily["spearman_rank_ic"].isna(), "spearman_reason"].value_counts().to_dict(),
    }


def daily_information_coefficients(predictions: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for day, group in predictions.groupby("prediction_date", sort=True):
        actual = group["target_5d"].to_numpy(dtype=float)
        for model_name in models:
            predicted = group[model_name].to_numpy(dtype=float)
            valid = np.isfinite(actual) & np.isfinite(predicted)
            y, p = actual[valid], predicted[valid]
            row: dict[str, object] = {
                "prediction_date": pd.Timestamp(day),
                "model": model_name,
                "sample_count": len(y),
                "pearson_ic": None,
                "spearman_rank_ic": None,
                "pearson_reason": None,
                "spearman_reason": None,
            }
            if len(y) < 3:
                row["pearson_reason"] = row["spearman_reason"] = "fewer than 3 cross-sectional samples"
            elif np.ptp(y) == 0:
                row["pearson_reason"] = row["spearman_reason"] = "constant realized returns"
            elif np.ptp(p) == 0:
                row["pearson_reason"] = row["spearman_reason"] = "constant predictions"
            else:
                row["pearson_ic"] = float(np.corrcoef(y, p)[0, 1])
                row["spearman_rank_ic"] = float(spearmanr(y, p).statistic)
                if not np.isfinite(row["pearson_ic"]):
                    row["pearson_ic"] = None
                    row["pearson_reason"] = "undefined Pearson correlation"
                if not np.isfinite(row["spearman_rank_ic"]):
                    row["spearman_rank_ic"] = None
                    row["spearman_reason"] = "undefined Spearman correlation"
            records.append(row)
    return pd.DataFrame(records)


def _score_predictions(test: pd.DataFrame, prediction_columns: list[str]) -> tuple[dict[str, object], pd.DataFrame]:
    actual = test["target_5d"].to_numpy(dtype=float)
    daily = daily_information_coefficients(test, prediction_columns)
    result = {
        name: _metric_block(actual, test[name].to_numpy(dtype=float),
                            daily.loc[daily["model"] == name].copy())
        for name in prediction_columns
    }
    return result, daily


def _period_metrics(predictions: pd.DataFrame, columns: list[str], period: str) -> dict[str, object]:
    frame = predictions.copy()
    dates = pd.to_datetime(frame["prediction_date"])
    frame["period"] = dates.dt.year.astype(str) if period == "year" else dates.dt.to_period("Q").astype(str)
    output: dict[str, object] = {}
    for key, group in frame.groupby("period", sort=True):
        metrics, _ = _score_predictions(group, columns)
        output[str(key)] = metrics
    return output


def _symbol_diagnostics(predictions: pd.DataFrame, columns: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for symbol, group in predictions.groupby("symbol", sort=True):
        actual = group["target_5d"].to_numpy(dtype=float)
        result[str(symbol)] = {
            "sample_count": int(len(group)), "actual_mean": float(np.mean(actual)),
            **{f"{name}_mean": float(group[name].mean()) for name in columns},
            **{f"{name}_mae": float(mean_absolute_error(actual, group[name])) for name in columns},
            **{f"{name}_rmse": float(np.sqrt(mean_squared_error(actual, group[name]))) for name in columns},
        }
    return result


def _prediction_distribution(predictions: pd.DataFrame, columns: list[str]) -> dict[str, object]:
    return {name: {"count": int(predictions[name].count()), "mean": float(predictions[name].mean()),
                   "std": float(predictions[name].std()), "min": float(predictions[name].min()),
                   "p01": float(predictions[name].quantile(.01)), "median": float(predictions[name].median()),
                   "p99": float(predictions[name].quantile(.99)), "max": float(predictions[name].max())}
            for name in columns}


def _latest_experiment_id(root: Path) -> str | None:
    manifests = sorted((root / "artifacts" / "experiments").glob("*/manifest.json"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    return manifests[0].parent.name if manifests else None


def train_lightgbm(*, root: Path | str | None = None, dataset_path: Path | str | None = None,
                   manifest_path: Path | str | None = None,
                   experiment_name: str = "Baseline Real Market Experiment v1") -> dict[str, object]:
    root = Path(root) if root else project_root()
    config = _read_json(root / "config" / "research.json")
    default_data_path = root / "data" / "processed" / "ashare_research_dataset_v1.parquet"
    selected_data_path = Path(dataset_path) if dataset_path else default_data_path
    if not selected_data_path.is_absolute():
        selected_data_path = root / selected_data_path
    selected_manifest_path = Path(manifest_path) if manifest_path else selected_data_path.with_suffix(".manifest.json")
    if not selected_manifest_path.is_absolute():
        selected_manifest_path = root / selected_manifest_path
    ds = dataset_status(selected_manifest_path, selected_data_path)
    gate_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    gate_path = root / "artifacts" / "reports" / f"readiness_{gate_id}.json"
    if ds.get("status") != "available":
        report = {"passed": False, "checks": [], "training_blockers": [
            {"name": "dataset_available", "passed": False, "reason": "Run quantlab research build-dataset first."}
        ]}
        _write_json(gate_path, report)
        return {"status": "blocked", "reason": "Research dataset is missing.", "readiness_report": str(gate_path)}

    data_path = Path(ds["dataset_path"])
    if not data_path.is_absolute():
        data_path = root / data_path
    dataset = pd.read_parquet(data_path)
    coverage = pd.DataFrame(ds.get("coverage", []))
    calendar = AShareTradingCalendar(ds["research_start"], ds["research_end"])
    config["adjustment"] = str(ds.get("adjustment", config["adjustment"]))
    splitter = FixedPeriodSplitter(config, calendar)
    gate = evaluate_readiness(dataset, coverage, config, splitter)
    _write_json(gate_path, gate.report)
    print(format_readiness_report(gate.report))
    if not gate.passed:
        return {"status": "blocked", "reason": "Data Readiness Gate failed; no model was fitted.",
                "readiness_report": str(gate_path), "report": gate.report}

    started = time.perf_counter()
    experiment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
    experiment_dir = root / "artifacts" / "experiments" / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)
    model_path = root / "artifacts" / "models" / "lightgbm" / f"{experiment_id}.joblib"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = root / "artifacts" / "reports" / f"{experiment_id}.json"
    features = FEATURE_COLUMNS
    train, validation, test = gate.splits.train, gate.splits.validation, gate.splits.test
    params = dict(config["lightgbm"])
    params.update({"verbosity": -1, "deterministic": True, "force_col_wise": True})
    model = LGBMRegressor(**params)
    model.fit(train[features], train["target_5d"])

    train_mean = float(train["target_5d"].mean())
    def predict_frame(frame: pd.DataFrame) -> pd.DataFrame:
        output = frame[["symbol", "prediction_date", "label_end_date", "target_5d"]].copy()
        output["lightgbm"] = model.predict(frame[features])
        output["train_mean"] = train_mean
        output["momentum_5d"] = frame["Return_5d"].to_numpy(dtype=float)
        return output

    validation_predictions = predict_frame(validation)
    test_predictions = predict_frame(test)
    model_columns = ["lightgbm", "train_mean", "momentum_5d"]
    validation_metrics, validation_daily = _score_predictions(validation_predictions, model_columns)
    test_metrics, daily = _score_predictions(test_predictions, model_columns)

    joblib.dump(model, model_path)
    reloaded = joblib.load(model_path)
    repeat_prediction = reloaded.predict(test[features])
    reload_consistent = bool(np.allclose(test_predictions["lightgbm"].to_numpy(), repeat_prediction, rtol=0, atol=1e-12))
    if not reload_consistent:
        raise RuntimeError("Persisted LightGBM model did not reproduce its fixed-input predictions.")
    test_predictions.to_parquet(experiment_dir / "test_predictions.parquet", index=False)
    validation_predictions.to_parquet(experiment_dir / "validation_predictions.parquet", index=False)
    test.loc[:, ["symbol", "prediction_date", "target_5d", *features]].to_parquet(
        experiment_dir / "test_model_inputs.parquet", index=False
    )
    daily.to_csv(experiment_dir / "daily_ic.csv", index=False)
    validation_daily.to_csv(experiment_dir / "validation_daily_ic.csv", index=False)
    experiment = {
        "experiment_id": experiment_id,
        "experiment_name": experiment_name,
        "status": "complete",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.perf_counter() - started,
        "model": "lightgbm.LGBMRegressor",
        "parameters": params,
        "random_seed": params.get("random_state"),
        "device_type": "cpu",
        "training_scope": "fit on Train only; Validation reports fixed-parameter diagnostics; Test was evaluated after fit and did not select parameters.",
        "target": ds["target"],
        "horizon_observations": ds["target_horizon_observations"],
        "adjustment": ds["adjustment"],
        "price_basis": ds.get("price_basis"),
        "adjustment_snapshot_time_by_symbol": ds.get("adjustment_snapshot_time_by_symbol", {}),
        "source": ds["source"],
        "provider": ds.get("provider"),
        "dataset_id": ds.get("dataset_id"),
        "dataset_version": ds.get("dataset_version", ds.get("data_version")),
        "dataset_path": str(data_path.resolve()),
        "dataset_manifest_path": str(selected_manifest_path.resolve()),
        "point_in_time_limit": ds["point_in_time_limit"],
        "data_version": ds["data_version"],
        "pool_id": ds["pool_id"],
        "pool_selection_date": ds["pool_selection_date"],
        "survivorship_bias_note": ds["survivorship_bias_note"],
        "feature_version": ds["feature_version"],
        "target_version": ds.get("target_version", "target_5d_h5_v1"),
        "features": features,
        "feature_parameters": ds["feature_parameters"],
        "calendar": ds["calendar"],
        "split_plan": gate.splits.metadata,
        "readiness_report": str(gate_path),
        "metrics": {"validation": validation_metrics, "test": test_metrics,
                    "test_yearly": _period_metrics(test_predictions, model_columns, "year"),
                    "test_quarterly": _period_metrics(test_predictions, model_columns, "quarter"),
                    "test_by_symbol": _symbol_diagnostics(test_predictions, model_columns),
                    "test_prediction_distribution": _prediction_distribution(test_predictions, model_columns)},
        "daily_test_ic_path": str((experiment_dir / "daily_ic.csv").resolve()),
        "model_path": str(model_path.resolve()),
        "test_predictions_path": str((experiment_dir / "test_predictions.parquet").resolve()),
        "test_model_inputs_path": str((experiment_dir / "test_model_inputs.parquet").resolve()),
        "validation_predictions_path": str((experiment_dir / "validation_predictions.parquet").resolve()),
        "validation_daily_ic_path": str((experiment_dir / "validation_daily_ic.csv").resolve()),
        "test_daily_ic_path": str((experiment_dir / "daily_ic.csv").resolve()),
        "feature_importance_path": str((experiment_dir / "feature_importance.csv").resolve()),
        "model_reload_same_input_consistent": reload_consistent,
        "python_version": platform.python_version(),
        "package_versions": {
            name: version(name) for name in (
                "numpy", "pandas", "scikit-learn", "lightgbm", "duckdb",
                "pyarrow", "akshare", "exchange-calendars",
            )
        },
    }
    importance = pd.DataFrame({"feature": features,
                               "gain": model.booster_.feature_importance(importance_type="gain"),
                               "split": model.booster_.feature_importance(importance_type="split")})
    importance.sort_values("gain", ascending=False).to_csv(experiment_dir / "feature_importance.csv", index=False)
    _write_json(experiment_dir / "manifest.json", experiment)
    _write_json(report_path, experiment)
    return {"status": "complete", "experiment_id": experiment_id, "report_path": str(report_path),
            "experiment_path": str(experiment_dir), "metrics": experiment["metrics"],
            "model_path": str(model_path), "reload_consistent": reload_consistent}


def evaluate_saved_experiment(experiment_id: str | None = None, *, root: Path | str | None = None) -> dict[str, object]:
    root = Path(root) if root else project_root()
    experiments_root = root / "artifacts" / "experiments"
    experiment_id = experiment_id or _latest_experiment_id(root)
    if not experiment_id:
        return {"status": "missing", "reason": "No completed experiment exists."}
    directory = experiments_root / experiment_id
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Experiment {experiment_id!r} was not found.")
    manifest = _read_json(manifest_path)
    model_path = Path(manifest["model_path"])
    model = joblib.load(model_path)
    inputs = pd.read_parquet(manifest["test_model_inputs_path"])
    repeated = model.predict(inputs[manifest["features"]])
    saved = pd.read_parquet(manifest["test_predictions_path"])
    identical = bool(np.allclose(repeated, saved["lightgbm"].to_numpy(), rtol=0, atol=1e-12))
    return {"status": "evaluated", "experiment_id": experiment_id,
            "test_metrics": manifest["metrics"]["test"],
            "model_reload_same_input_consistent": identical}


def list_experiments(*, root: Path | str | None = None) -> list[dict[str, object]]:
    root = Path(root) if root else project_root()
    records: list[dict[str, object]] = []
    for path in sorted((root / "artifacts" / "experiments").glob("*/manifest.json")):
        record = _read_json(path)
        records.append({
            "experiment_id": record.get("experiment_id"),
            "status": record.get("status"),
            "created_at_utc": record.get("created_at_utc"),
            "target": record.get("target"),
            "adjustment": record.get("adjustment"),
            "metrics": record.get("metrics", {}).get("test"),
            "manifest_path": str(path),
        })
    return records
