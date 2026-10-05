"""Frozen U11/U50/U100 development-only breadth comparison."""

from __future__ import annotations

import hashlib
import json
import platform
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

from quantlab.data.store import MarketDataStore
from quantlab.experiments import daily_information_coefficients
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import project_root
from quantlab.research.features import FEATURE_COLUMNS, FEATURE_VERSION, FeatureBuilder
from quantlab.research.walk_forward import (
    _ic_summary, _importance_stability, fixed_lightgbm_parameters,
    newey_west_mean_diagnostic,
)
from quantlab.research.walkforward_splits import split_walk_forward, validate_development_dates

START = pd.Timestamp("2018-01-01")
END = pd.Timestamp("2023-12-31")
TARGET_VERSION = "target_5d_h5_v1"
UNIVERSES = ("U11", "U50", "U100")
DATASET_NAMES = {
    "U11": "ashare_research_u11_qfq_v1_dev_current_2018_2023",
    "U50": "ashare_research_u50_qfq_v1_dev_2018_2023",
    "U100": "ashare_research_u100_qfq_v1_dev_2018_2023",
}
UNIVERSE_FILES = {
    "U11": "ashare_universe_qfq_pilot.json",
    "U50": "ashare_universe_breadth_50.json",
    "U100": "ashare_universe_breadth_100.json",
}
FROZEN_HASHES = {
    "U50": "d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218",
    "U100": "05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def validate_frozen_universes(config_dir: Path) -> dict:
    configs = {name: _json(config_dir / UNIVERSE_FILES[name]) for name in UNIVERSES}
    symbols = {name: [str(m["symbol"]) for m in configs[name]["members"]] for name in UNIVERSES}
    for name, expected in (("U11", 11), ("U50", 50), ("U100", 100)):
        if len(symbols[name]) != expected or len(set(symbols[name])) != expected:
            raise ValueError(f"{name} size/duplicate check failed.")
    if not set(symbols["U11"]) <= set(symbols["U50"]) <= set(symbols["U100"]):
        raise ValueError("Frozen U11/U50/U100 nested relationship failed.")
    hashes = {name: hashlib.sha256("\n".join(symbols[name]).encode()).hexdigest() for name in UNIVERSES}
    for name in ("U50", "U100"):
        if hashes[name] != FROZEN_HASHES[name] or configs[name].get("universe_hash") != hashes[name]:
            raise ValueError(f"{name} frozen hash changed.")
    if configs["U50"].get("selection_seed") != configs["U100"].get("selection_seed") or configs["U50"].get("candidate_source_hash") != configs["U100"].get("candidate_source_hash"):
        raise ValueError("Frozen selection seed or candidate source changed.")
    for name in ("U50", "U100"):
        exchange_counts = pd.Series([m["exchange"] for m in configs[name]["members"]]).value_counts().to_dict()
        expected = len(symbols[name]) // 2
        if exchange_counts != {"SSE": expected, "SZSE": expected}:
            raise ValueError(f"{name} exchange balance changed.")
    return {"symbols": symbols, "hashes": hashes, "selection_seed": configs["U100"]["selection_seed"],
            "candidate_source_hash": configs["U100"]["candidate_source_hash"]}


def validate_dataset_manifest(name: str, manifest: dict, path: Path, universe_hash: str) -> None:
    expected = {
        "pool_size": int(name[1:]), "provider": "baostock", "adjustment": "qfq",
        "feature_version": FEATURE_VERSION, "target_version": TARGET_VERSION,
        "target_horizon_observations": 5, "research_start": "2018-01-01", "research_end": "2023-12-31",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"{name} manifest {key} violates the frozen development protocol.")
    if manifest.get("feature_columns") != FEATURE_COLUMNS or manifest.get("universe_hash") not in (None if name == "U11" else universe_hash,):
        raise ValueError(f"{name} feature columns or universe hash changed.")
    if name == "U11" and manifest.get("universe_hash") is not None:
        raise ValueError("U11 control manifest unexpectedly declares a frozen U50/U100 hash.")
    if name != "U11" and manifest.get("development_only") is not True:
        raise ValueError(f"{name} dataset is not development-only.")
    if name == "U11" and manifest.get("research_sample_policy", "").find("tradestatus=1") < 0:
        raise ValueError("Current U11 control lacks the no-trade exclusion policy.")
    if not path.is_file() or manifest.get("dataset_path") != str(path.resolve()):
        raise ValueError(f"{name} dataset path is missing or does not match its manifest.")
    checksum = manifest.get("dataset_sha256")
    if checksum and _sha256(path) != checksum:
        raise ValueError(f"{name} dataset checksum differs from its manifest.")


def compare_nested_frames(left: pd.DataFrame, right: pd.DataFrame, columns: list[str]) -> dict:
    """Require every smaller-universe key and research value to match the larger universe."""
    key = ["symbol", "prediction_date"]
    if left.duplicated(key).any() or right.duplicated(key).any():
        raise ValueError("Duplicate research sample keys.")
    a, b = left.set_index(key), right.set_index(key)
    absent = a.index.difference(b.index)
    if len(absent):
        raise ValueError(f"Nested dataset is missing {len(absent)} smaller-universe keys.")
    differences = {}
    for column in columns:
        if column not in a or column not in b:
            raise ValueError(f"Missing consistency field {column}.")
        x, y = a[column], b.loc[a.index, column]
        if pd.api.types.is_numeric_dtype(x):
            equal = np.isclose(x.to_numpy(dtype=float), y.to_numpy(dtype=float), rtol=1e-12, atol=1e-12, equal_nan=True)
        else:
            equal = (x.eq(y) | (x.isna() & y.isna())).to_numpy()
        count = int((~equal).sum())
        if count:
            differences[column] = count
    if differences:
        raise ValueError(f"Nested research samples disagree: {differences}")
    return {"matched_keys": len(a), "missing_keys": 0, "value_differences": 0,
            "compared_fields": columns}


def _validate_rows(name: str, frame: pd.DataFrame, frozen_symbols: list[str]) -> dict:
    validate_development_dates(frame)
    dates = pd.to_datetime(frame["prediction_date"], errors="coerce")
    labels = pd.to_datetime(frame["label_end_date"], errors="coerce")
    if dates.lt(START).any() or dates.gt(END).any() or labels.dropna().gt(END).any():
        raise ValueError(f"{name} has rows outside the development period.")
    if set(frame["symbol"]) != set(frozen_symbols) or frame.duplicated(["symbol", "prediction_date"]).any():
        raise ValueError(f"{name} sample keys or member list violate its frozen universe.")
    for column, wanted in (("source", "baostock"), ("adjustment", "qfq"), ("feature_version", FEATURE_VERSION)):
        if set(frame[column].dropna().astype(str)) != {wanted} or frame[column].isna().any():
            raise ValueError(f"{name} contains unexpected {column} values.")
    return {"rows": int(len(frame)), "symbols": int(frame.symbol.nunique()),
            "prediction_start": dates.min().date().isoformat(), "prediction_end": dates.max().date().isoformat(),
            "max_label_end": labels.max().date().isoformat()}


def validate_trade_status(sample_keys: pd.MultiIndex, metadata: pd.DataFrame) -> dict:
    statuses = {}
    for row in metadata.itertuples():
        value = json.loads(row.metadata_json).get("tradestatus")
        statuses[(str(row.symbol), pd.Timestamp(row.trade_date).normalize())] = str(value) if value is not None else None
    counts = {"trading": 0, "provider_reported_nontrading": 0, "unknown": 0}
    for key in sample_keys:
        value = statuses.get((str(key[0]), pd.Timestamp(key[1]).normalize()))
        category = "trading" if value == "1" else "provider_reported_nontrading" if value == "0" else "unknown"
        counts[category] += 1
    if counts["provider_reported_nontrading"] or counts["unknown"]:
        raise ValueError(f"Research dataset includes no-trade or unknown sessions: {counts}.")
    return counts


def compare_legacy_u11(old: pd.DataFrame, current: pd.DataFrame, metadata: pd.DataFrame) -> dict:
    """Describe the immutable Step 4.3 snapshot without changing it or using it for training."""
    key = ["symbol", "prediction_date"]
    a, b = old.set_index(key), current.set_index(key)
    common = a.index.intersection(b.index)
    old_only, new_only = a.index.difference(b.index), b.index.difference(a.index)
    statuses = {(str(row.symbol), pd.Timestamp(row.trade_date).normalize()):
                str(json.loads(row.metadata_json).get("tradestatus")) for row in metadata.itertuples()}
    old_only_no_trade = sum(statuses.get((str(symbol), pd.Timestamp(day).normalize())) == "0"
                            for symbol, day in old_only)
    differences = {}
    for column in [*FEATURE_COLUMNS, "target_5d", "label_end_date"]:
        x, y = a.loc[common, column], b.loc[common, column]
        if pd.api.types.is_numeric_dtype(x):
            equal = np.isclose(x.to_numpy(dtype=float), y.to_numpy(dtype=float),
                               rtol=1e-12, atol=1e-12, equal_nan=True)
        else:
            equal = (x.eq(y) | (x.isna() & y.isna())).to_numpy()
        count = int((~equal).sum())
        if count:
            differences[column] = count
    return {"old_rows": len(old), "current_rows": len(current), "common_keys": len(common),
            "old_only_keys": len(old_only), "old_only_provider_no_trade": int(old_only_no_trade),
            "current_only_keys": len(new_only),
            "current_only_unlabeled": int(b.loc[new_only, "target_5d"].isna().sum()),
            "common_row_hash_differences": int(a.loc[common, "row_hash"].ne(b.loc[common, "row_hash"]).sum()),
            "common_feature_target_differences": differences,
            "interpretation": "Historical Step 4.3 snapshot retains provider no-trade bars and excluded five unlabeled tail rows per symbol. Current U11 uses tradestatus=1 and a bounded 2018-2023 target build; downstream rolling features and some five-observation labels therefore differ. The historical experiment is left immutable."}


def consistency_gate(root: Path, store: MarketDataStore | None = None) -> tuple[dict, dict[str, pd.DataFrame], dict[str, dict]]:
    frozen = validate_frozen_universes(root / "config")
    paths = {name: root / "data" / "processed" / f"{DATASET_NAMES[name]}.parquet" for name in UNIVERSES}
    manifests = {name: _json(paths[name].with_suffix(".manifest.json")) for name in UNIVERSES}
    for name in UNIVERSES:
        validate_dataset_manifest(name, manifests[name], paths[name], frozen["hashes"][name])
    frames = {name: pd.read_parquet(paths[name]) for name in UNIVERSES}
    rows = {name: _validate_rows(name, frames[name], frozen["symbols"][name]) for name in UNIVERSES}
    compare_fields = FEATURE_COLUMNS + ["target_5d", "label_end_date", "row_hash", "source", "adjustment"]
    nested = {"U11_in_U50": compare_nested_frames(frames["U11"], frames["U50"], compare_fields),
              "U50_in_U100": compare_nested_frames(frames["U50"], frames["U100"], compare_fields)}
    store = store or MarketDataStore(root / "data")
    bars = store.query(market="CN_A", start=START.date(), end=END.date(), adjustment="qfq", source="baostock")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.normalize()
    keyed_bars = bars.set_index(["symbol", "trade_date"])
    keyed_data = frames["U100"].set_index(["symbol", "prediction_date"])
    if len(keyed_data.index.difference(keyed_bars.index)):
        raise ValueError("U100 research prices are missing from bounded canonical bars.")
    price_hash_equal = keyed_data["row_hash"].eq(keyed_bars.loc[keyed_data.index, "row_hash"])
    if not price_hash_equal.all():
        raise ValueError("U100 research price/OHLC/volume lineage differs from canonical bars.")
    all_metadata = []
    for symbol in frozen["symbols"]["U100"]:
        all_metadata.append(store.query_provider_metadata(provider="baostock", symbol=symbol,
                                                          start=START.date(), end=END.date()))
    metadata = pd.concat(all_metadata, ignore_index=True)
    status = validate_trade_status(keyed_data.index, metadata)
    no_trade_metadata = metadata[metadata["metadata_json"].map(
        lambda raw: str(json.loads(raw).get("tradestatus")) == "0")]
    if len(keyed_data.index.intersection(pd.MultiIndex.from_frame(no_trade_metadata[["symbol", "trade_date"]].assign(
            trade_date=lambda x: pd.to_datetime(x["trade_date"]).dt.normalize())))):
        raise ValueError("Provider-reported no-trade rows entered U100 research samples.")
    legacy_path = root / "data" / "processed" / "ashare_research_dataset_baostock_qfq_v1_development_2018_2023.parquet"
    legacy_comparison = compare_legacy_u11(pd.read_parquet(legacy_path), frames["U11"], metadata)
    trading_bars = keyed_bars.loc[keyed_data.index].reset_index()
    rebuilt_features = FeatureBuilder().build(trading_bars)
    compare_nested_frames(frames["U100"], rebuilt_features, FEATURE_COLUMNS)
    reconstructed_targets = []
    for symbol, group in trading_bars.groupby("symbol", sort=False):
        ordered = group.sort_values("trade_date")
        reconstructed_targets.append(pd.DataFrame({
            "symbol": symbol, "prediction_date": ordered["trade_date"],
            "target_5d": ordered["close"].shift(-5).to_numpy() / ordered["close"].to_numpy() - 1,
            "label_end_date": ordered["trade_date"].shift(-5).to_numpy(),
        }))
    compare_nested_frames(frames["U100"], pd.concat(reconstructed_targets, ignore_index=True),
                          ["target_5d", "label_end_date"])
    gate = {"status": "dataset_consistency_pass", "frozen_hashes": frozen["hashes"],
            "selection_seed": frozen["selection_seed"], "candidate_source_hash": frozen["candidate_source_hash"],
            "datasets": {name: {**rows[name], "sha256": _sha256(paths[name])} for name in UNIVERSES},
            "nested": nested, "price_lineage_matched_rows": int(price_hash_equal.sum()),
            "trade_status": status, "provider_reported_no_trade_sessions": int(len(no_trade_metadata)),
            "feature_recomputed_rows": len(rebuilt_features), "target_recomputed_rows": len(keyed_data),
            "immutable_step43_u11_reference_comparison": legacy_comparison}
    return gate, frames, manifests


def _metrics(predictions: pd.DataFrame, score: str) -> tuple[dict, pd.DataFrame]:
    daily = daily_information_coefficients(predictions, [score])
    actual = predictions["target_5d"].to_numpy(dtype=float)
    pred = predictions[score].to_numpy(dtype=float)
    valid = np.isfinite(actual) & np.isfinite(pred)
    breadth = daily.loc[daily["spearman_rank_ic"].notna(), "sample_count"]
    result = {
        "rows": int(valid.sum()), "mae": float(mean_absolute_error(actual[valid], pred[valid])),
        "rmse": float(np.sqrt(mean_squared_error(actual[valid], pred[valid]))),
        "rank_ic": _ic_summary(daily["spearman_rank_ic"]),
        "pearson_ic": _ic_summary(daily["pearson_ic"]),
        "average_effective_breadth": float(breadth.mean()) if len(breadth) else None,
        "newey_west_lag5": newey_west_mean_diagnostic(daily["spearman_rank_ic"], lag=5),
        "prediction_distribution": pd.Series(pred).describe(percentiles=[.01, .1, .5, .9, .99]).to_dict(),
    }
    return result, daily


def _dispersion(daily: pd.DataFrame) -> dict:
    values = pd.to_numeric(daily["spearman_rank_ic"], errors="coerce").dropna()
    breadth = pd.to_numeric(daily.loc[values.index, "sample_count"], errors="coerce")
    output = {"rank_ic": _ic_summary(values), "iqr": float(values.quantile(.75) - values.quantile(.25)) if len(values) else None,
              "p10": float(values.quantile(.1)) if len(values) else None,
              "p90": float(values.quantile(.9)) if len(values) else None,
              "average_breadth": float(breadth.mean()) if len(breadth) else None}
    return output


def _period_dispersion(daily: pd.DataFrame, period: str) -> list[dict]:
    frame = daily.copy()
    frame["period"] = pd.to_datetime(frame["prediction_date"]).dt.to_period(period).astype(str)
    return [{"period": key, **_dispersion(group)} for key, group in frame.groupby("period", sort=True)]


def _breadth_relationship(daily: pd.DataFrame) -> dict:
    rows = daily.dropna(subset=["spearman_rank_ic"])
    x = rows["sample_count"].to_numpy(dtype=float)
    y = rows["spearman_rank_ic"].to_numpy(dtype=float)
    if len(rows) < 3 or np.ptp(x) == 0:
        return {"valid_dates": len(rows), "breadth_rank_ic_spearman": None,
                "breadth_absolute_rank_ic_spearman": None, "reason": "insufficient variation in effective breadth"}
    return {"valid_dates": len(rows), "breadth_rank_ic_spearman": float(spearmanr(x, y).statistic),
            "breadth_absolute_rank_ic_spearman": float(spearmanr(x, np.abs(y)).statistic),
            "reason": "descriptive association; broader cross-sections can reduce IC sampling noise"}


def summarize_common_dates(by_universe: dict[str, pd.DataFrame]) -> dict:
    valid_dates = {name: set(by_universe[name].loc[by_universe[name]["spearman_rank_ic"].notna(), "prediction_date"])
                   for name in UNIVERSES}
    common = sorted(set.intersection(*(valid_dates[name] for name in UNIVERSES)))
    result = {name: {"native": _dispersion(by_universe[name]),
                     "common": _dispersion(by_universe[name].loc[
                         by_universe[name].prediction_date.isin(common)]),
                     "dropped_dates": len(valid_dates[name]) - len(common)} for name in UNIVERSES}
    result["common_date_count"] = len(common)
    return result


def align_anchor_predictions(by_universe: dict[str, pd.DataFrame], anchor_symbols: set[str]) -> dict[str, pd.DataFrame]:
    keyed = {name: frame.loc[frame.symbol.isin(anchor_symbols)].set_index(["symbol", "prediction_date"])
             for name, frame in by_universe.items()}
    common = keyed["U11"].index.intersection(keyed["U50"].index).intersection(keyed["U100"].index)
    if len(common) == 0:
        raise ValueError("Anchor-only common prediction keys are empty.")
    raw = keyed["U11"].loc[common, "target_5d"]
    result = {}
    for name in UNIVERSES:
        frame = keyed[name].loc[common].reset_index()
        if not np.allclose(frame["target_5d"], raw.to_numpy(), rtol=1e-12, atol=1e-12):
            raise ValueError("Anchor-only raw target differs across universes.")
        result[name] = frame
    return result


def assess_breadth(u100_folds: list[dict], gate_status: str) -> dict:
    ranks = [fold["metrics"]["lightgbm"]["rank_ic"]["mean"] for fold in u100_folds]
    ratios = [fold["metrics"]["lightgbm"]["rank_ic"]["positive_day_ratio"] for fold in u100_folds]
    if len(ranks) != 3 or any(x is None or not np.isfinite(x) for x in ranks):
        return {"status": "breadth_inconclusive", "reason": "invalid fold Rank IC", "fold_rank_ic": ranks}
    mean = float(np.mean(ranks))
    if sum(x < 0 for x in ranks) >= 2 or mean <= 0:
        status, reason = "breadth_not_supported", "U100 has multiple negative folds or nonpositive fold mean Rank IC"
    else:
        positive = [x for x in ranks if x > 0]
        largest_share = max(positive) / sum(positive) if positive else 1.0
        supported = (sum(x > 0 for x in ranks) >= 2 and float(np.mean(ratios)) > .5
                     and gate_status == "u100_data_ready" and largest_share <= .75)
        status = "breadth_supported" if supported else "breadth_inconclusive"
        reason = "all preregistered U100 conditions passed" if supported else "positive evidence fails breadth, consistency, day ratio or fold concentration criterion"
    return {"status": status, "reason": reason, "fold_rank_ic": ranks,
            "fold_mean_rank_ic": mean, "mean_positive_day_ratio": float(np.mean(ratios)),
            "positive_folds": sum(x > 0 for x in ranks),
            "largest_positive_fold_share_limit": .75, "u100_data_gate": gate_status}


def verify_saved_breadth_report(report_path: Path) -> dict:
    """Replay saved validation inputs through each saved model without reading market data."""
    report = _json(report_path)
    checked = []
    for name in UNIVERSES:
        manifest = _json(Path(report["experiments"][name]["manifest_path"]))
        if manifest["evaluation_role"] != "development_walk_forward":
            raise ValueError("Saved breadth experiment role changed.")
        for fold in manifest["folds"]:
            files = fold["files"]
            for kind, expected in fold["file_sha256"].items():
                if _sha256(Path(files[kind])) != expected:
                    raise ValueError(f"Saved {name} {fold['fold_id']} {kind} checksum changed.")
            inputs = pd.read_parquet(files["validation_inputs"])
            predictions = pd.read_parquet(files["predictions"])
            model = joblib.load(files["model"])
            if list(inputs.columns) != FEATURE_COLUMNS or len(inputs) != len(predictions):
                raise ValueError("Saved validation input schema or row count changed.")
            if not np.allclose(model.predict(inputs), predictions["lightgbm"], rtol=0, atol=1e-12):
                raise ValueError(f"Reloaded {name} {fold['fold_id']} predictions differ.")
            replay, _ = _metrics(predictions, "lightgbm")
            expected = fold["metrics"]["lightgbm"]
            if not np.isclose(replay["rank_ic"]["mean"], expected["rank_ic"]["mean"], rtol=0, atol=1e-12):
                raise ValueError(f"Recomputed {name} {fold['fold_id']} Rank IC differs.")
            checked.append({"universe": name, "fold": fold["fold_id"], "rows": len(inputs)})
    return {"status": "replay_verified", "folds": len(checked), "checks": checked}


def run_breadth_study(*, root: Path | None = None) -> dict:
    root = (root or project_root()).resolve()
    gate, frames, manifests = consistency_gate(root)
    gate_path = root / "artifacts" / "reports" / f"breadth_consistency_{uuid.uuid4().hex[:12]}.json"
    _write_json_new(gate_path, gate)
    data_gate = _json(root / "data" / "processed" / "ashare_u100_breadth_gate_2018_2023.json")
    if data_gate.get("status") != "u100_data_ready":
        raise ValueError("U100 development breadth gate is not ready.")
    params = fixed_lightgbm_parameters(root / "config" / "research.json")
    calendar = AShareTradingCalendar(START, END)
    folds = {name: split_walk_forward(frames[name], FEATURE_COLUMNS, calendar, embargo_sessions=5)
             for name in UNIVERSES}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
    experiment_root = root / "artifacts" / "experiments"
    experiments = {}
    all_predictions: dict[str, dict[str, pd.DataFrame]] = {name: {} for name in UNIVERSES}
    all_daily: dict[str, dict[str, pd.DataFrame]] = {name: {} for name in UNIVERSES}
    for name in UNIVERSES:
        experiment_id = f"breadth_{name.lower()}_{run_id}"
        directory = experiment_root / experiment_id
        directory.mkdir(parents=True, exist_ok=False)
        fold_reports = []
        for fold in folds[name]:
            train, validation = fold.train, fold.validation
            if train.empty or validation.empty:
                raise ValueError(f"{name} {fold.fold_id} has no train or validation rows.")
            model = LGBMRegressor(**params)
            model.fit(train[FEATURE_COLUMNS], train["target_5d"])
            prediction = validation[["symbol", "prediction_date", "label_end_date", "target_5d"]].copy()
            prediction["lightgbm"] = model.predict(validation[FEATURE_COLUMNS])
            prediction["train_mean"] = float(train["target_5d"].mean())
            prediction["momentum_5d"] = validation["Return_5d"].to_numpy(dtype=float)
            fold_dir = directory / fold.fold_id
            fold_dir.mkdir(parents=True, exist_ok=False)
            input_path = fold_dir / "validation_inputs.parquet"
            validation[FEATURE_COLUMNS].to_parquet(input_path, index=False)
            model_path = fold_dir / "lightgbm.joblib"
            joblib.dump(model, model_path)
            reloaded = joblib.load(model_path)
            reload_equal = bool(np.allclose(prediction["lightgbm"], reloaded.predict(pd.read_parquet(input_path)), rtol=0, atol=1e-12))
            if not reload_equal:
                raise ValueError(f"{name} {fold.fold_id} reloaded model predictions changed.")
            scores = {}
            daily_parts = []
            for score in ("lightgbm", "train_mean", "momentum_5d"):
                scores[score], daily = _metrics(prediction, score)
                daily_parts.append(daily)
                if score == "train_mean" and daily["spearman_rank_ic"].notna().any():
                    raise ValueError("Constant train-mean baseline must have NA Rank IC.")
            daily_all = pd.concat(daily_parts, ignore_index=True)
            model_daily = daily_parts[0]
            importance = pd.DataFrame({"feature": FEATURE_COLUMNS,
                                       "gain": model.booster_.feature_importance(importance_type="gain"),
                                       "split": model.booster_.feature_importance(importance_type="split")}).sort_values("gain", ascending=False)
            prediction.to_parquet(fold_dir / "validation_predictions.parquet", index=False)
            daily_all.to_csv(fold_dir / "daily_ic.csv", index=False)
            importance.to_csv(fold_dir / "feature_importance.csv", index=False)
            monthly = _period_dispersion(model_daily, "M")
            quarterly = _period_dispersion(model_daily, "Q")
            _write_json_new(fold_dir / "ic_dispersion.json", {"monthly": monthly, "quarterly": quarterly,
                             "breadth_relationship": _breadth_relationship(model_daily)})
            fold_reports.append({"fold_id": fold.fold_id, "split": fold.metadata, "metrics": scores,
                                 "rank_ic_dispersion": _dispersion(model_daily),
                                 "monthly": monthly, "quarterly": quarterly,
                                 "breadth_relationship": _breadth_relationship(model_daily),
                                 "feature_importance": importance.to_dict("records"),
                                 "top5_gain_share": float(importance.head(5)["gain"].sum() / importance["gain"].sum()),
                                 "reload_prediction_equal": reload_equal,
                                 "files": {"model": str(model_path), "validation_inputs": str(input_path),
                                           "predictions": str(fold_dir / "validation_predictions.parquet"),
                                           "daily_ic": str(fold_dir / "daily_ic.csv"),
                                           "feature_importance": str(fold_dir / "feature_importance.csv")},
                                 "file_sha256": {"model": _sha256(model_path),
                                                 "validation_inputs": _sha256(input_path),
                                                 "predictions": _sha256(fold_dir / "validation_predictions.parquet"),
                                                 "daily_ic": _sha256(fold_dir / "daily_ic.csv"),
                                                 "feature_importance": _sha256(fold_dir / "feature_importance.csv")}})
            all_predictions[name][fold.fold_id] = prediction
            all_daily[name][fold.fold_id] = model_daily
        manifest = {"experiment_id": experiment_id, "universe": name, "universe_hash": gate["frozen_hashes"][name],
                    "dataset_sha256": gate["datasets"][name]["sha256"], "dataset_path": manifests[name]["dataset_path"],
                    "feature_version": FEATURE_VERSION, "feature_columns": FEATURE_COLUMNS,
                    "target_version": TARGET_VERSION, "target_horizon_observations": 5,
                    "provider": "baostock", "adjustment": "qfq", "model": "lightgbm.LGBMRegressor",
                    "model_parameters": params, "evaluation_role": "development_walk_forward",
                    "development_start": str(START.date()), "development_end": str(END.date()),
                    "random_shuffle": False, "folds": fold_reports,
                    "feature_importance_stability": _importance_stability(fold_reports),
                    "dataset_consistency_report": str(gate_path), "legacy_observed_test_accessed": False,
                    "future_sealed_holdout_accessed": False,
                    "environment": {package: version(package) for package in ("pandas", "numpy", "scikit-learn", "lightgbm", "duckdb", "pyarrow", "exchange-calendars")}
                    | {"python": platform.python_version()},
                    "created_at_utc": datetime.now(timezone.utc).isoformat()}
        _write_json_new(directory / "manifest.json", manifest)
        experiments[name] = manifest
    common_dates = {}
    anchors = {}
    anchor_symbols = set(validate_frozen_universes(root / "config")["symbols"]["U11"])
    for fold_id in ("fold_1", "fold_2", "fold_3"):
        common_dates[fold_id] = summarize_common_dates({name: all_daily[name][fold_id] for name in UNIVERSES})
        aligned = align_anchor_predictions({name: all_predictions[name][fold_id] for name in UNIVERSES},
                                           anchor_symbols)
        anchors[fold_id] = {name: {"label": "ANCHOR_ONLY_DIAGNOSTIC", "common_keys": len(aligned[name]),
                                  "metrics": _metrics(aligned[name], "lightgbm")[0]} for name in UNIVERSES}
    assessment = assess_breadth(experiments["U100"]["folds"], data_gate["status"])
    report = {"run_id": run_id, "status": assessment["status"], "assessment": assessment,
              "consistency_gate": str(gate_path), "frozen_hashes": gate["frozen_hashes"],
              "dataset_sha256": {name: gate["datasets"][name]["sha256"] for name in UNIVERSES},
              "experiments": {name: {"experiment_id": experiments[name]["experiment_id"],
                                     "manifest_path": str(experiment_root / experiments[name]["experiment_id"] / "manifest.json"),
                                     "folds": experiments[name]["folds"]} for name in UNIVERSES},
              "common_dates": common_dates, "anchor_only": anchors,
              "data_gate": data_gate["status"],
              "native_comparison_notice": "Native training pools and evaluation cross-sections both change with universe size; differences are not attributable to training sample count alone.",
              "ic_dispersion_notice": "Larger cross-sections can mechanically reduce daily IC sampling noise; lower IC std alone does not demonstrate stronger prediction.",
              "point_in_time_limit": "BaoStock qfq is a mixed-time vendor-adjusted snapshot, not point-in-time corporate action history.",
              "survivorship_limit": "Frozen members derive from a current exchange listing snapshot, so historical delisted securities are absent."}
    report_path = root / "artifacts" / "reports" / f"breadth_robustness_{run_id}.json"
    _write_json_new(report_path, report)
    replay = verify_saved_breadth_report(report_path)
    return {"report_path": str(report_path), "report": report, "replay_verification": replay}
