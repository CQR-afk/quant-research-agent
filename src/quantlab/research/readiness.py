"""Hard gate that prevents model training on insufficient or leaky data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantlab.research.features import FEATURE_VERSION, feature_spec
from quantlab.research.splits import FixedPeriodSplitter, SplitBundle


@dataclass(frozen=True)
class ReadinessResult:
    passed: bool
    report: dict[str, object]
    splits: SplitBundle


def evaluate_readiness(
    dataset: pd.DataFrame,
    coverage: pd.DataFrame,
    config: dict[str, object],
    splitter: FixedPeriodSplitter,
) -> ReadinessResult:
    feature_version = str(dataset["feature_version"].dropna().iloc[0]) if (
        "feature_version" in dataset and dataset["feature_version"].notna().any()
    ) else FEATURE_VERSION
    feature_columns, _, _ = feature_spec(feature_version)
    splits = splitter.split(dataset)
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, observed: object, requirement: object, reason: str | None = None) -> None:
        checks.append({
            "name": name, "passed": bool(passed), "observed": observed,
            "requirement": requirement, "reason": reason if not passed else None,
        })

    min_symbols = int(config.get("minimum_data_symbols", 8))
    symbols_with_data = int(coverage.loc[coverage.get("rows", pd.Series(dtype=float)).fillna(0) > 0, "symbol"].nunique()) if not coverage.empty else 0
    check("pool_data_coverage", symbols_with_data >= min_symbols, symbols_with_data,
          f"at least {min_symbols} pool symbols have data",
          "Missing symbols do not count as available model coverage.")

    max_missing = float(config["maximum_missing_session_ratio"])
    if coverage.empty or "missing_ratio" not in coverage:
        coverage_ok, worst_missing = False, None
    else:
        ratios = pd.to_numeric(coverage["missing_ratio"], errors="coerce")
        assessed = coverage.loc[coverage["rows"].fillna(0) > 0, "missing_ratio"]
        coverage_ok = bool(len(assessed) and (pd.to_numeric(assessed, errors="coerce").fillna(1) <= max_missing).all())
        worst_missing = float(pd.to_numeric(assessed, errors="coerce").max()) if len(assessed) else None
    check("calendar_coverage", coverage_ok, worst_missing, f"each available symbol missing ratio <= {max_missing}",
          "Trading-calendar gaps are unknown suspension versus source gaps; no rows were filled.")
    unexpected = int(pd.to_numeric(coverage.get("unexpected_dates", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not coverage.empty else 0
    check("calendar_date_validity", unexpected == 0, unexpected, "zero observations on non-session dates")

    duplicate_count = int(dataset.duplicated(["symbol", "prediction_date"]).sum()) if len(dataset) else 0
    check("duplicate_samples", duplicate_count == 0, duplicate_count, "zero duplicate symbol/prediction_date rows")
    adjustment_values = sorted(dataset["adjustment"].dropna().astype(str).unique().tolist()) if "adjustment" in dataset else []
    expected_adjustment = str(config["adjustment"])
    check("single_adjustment_mode", adjustment_values == [expected_adjustment], adjustment_values, [expected_adjustment])

    feature_complete = dataset[feature_columns].notna().all(axis=1) if len(dataset) else pd.Series(dtype=bool)
    feature_complete_count = int(feature_complete.sum())
    check("feature_samples", feature_complete_count > 0, feature_complete_count, "at least one complete feature row")
    splits = splitter.split(dataset.loc[feature_complete].copy()) if len(dataset) else splits
    target_cols = ["target_5d", "label_end_date"]
    for name, frame, period_key in (
        ("train", splits.train, "train"),
        ("validation", splits.validation, "validation"),
        ("test", splits.test, "test"),
    ):
        usable = frame.loc[frame[feature_columns].notna().all(axis=1) & frame[target_cols].notna().all(axis=1)] if len(frame) else frame
        required_rows = int(config["minimum_rows_per_split"])
        check(f"{name}_rows", len(usable) >= required_rows, len(usable), f"at least {required_rows} rows")
        finite = bool(np.isfinite(usable["target_5d"].to_numpy(dtype=float)).all()) if len(usable) else False
        check(f"{name}_finite_labels", finite, len(usable), "all labels finite and realized")

    boundary_ok = all(bool(value) for value in splits.metadata["boundary_checks"].values())
    check("time_boundary_purge", boundary_ok, splits.metadata["boundary_checks"],
          "all labels end before the next period; no duplicated sample keys")
    test_dates = int(splits.test["prediction_date"].nunique()) if len(splits.test) else 0
    min_test_dates = int(config["minimum_test_dates"])
    check("test_date_coverage", test_dates >= min_test_dates, test_dates, f"at least {min_test_dates} independent test dates")
    min_cross = int(config["minimum_cross_section_symbols"])
    if len(splits.test):
        counts = splits.test.groupby("prediction_date")["symbol"].nunique()
        dates_with_breadth = int((counts >= min_cross).sum())
        breadth_coverage = dates_with_breadth / max(test_dates, 1)
    else:
        dates_with_breadth, breadth_coverage = 0, 0.0
    required_breadth_coverage = float(config.get("minimum_test_cross_section_coverage", 0.8))
    check("test_cross_section_coverage", breadth_coverage >= required_breadth_coverage,
          {"dates_with_required_breadth": dates_with_breadth, "coverage": breadth_coverage},
          f"{min_cross}+ symbols on >= {required_breadth_coverage:.0%} of test dates")

    summary = {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "symbols_with_data": symbols_with_data,
        "coverage_rows": coverage.to_dict("records"),
        "feature_complete_rows": feature_complete_count,
        "labeled_rows": int(dataset["target_5d"].notna().sum()) if len(dataset) else 0,
        "splits": splits.metadata,
        "test_cross_section_dates": dates_with_breadth,
        "test_cross_section_coverage": breadth_coverage,
        "training_blockers": [item for item in checks if not item["passed"]],
    }
    return ReadinessResult(bool(summary["passed"]), summary, splits)


def format_readiness_report(report: dict[str, object]) -> str:
    lines = [f"Data Readiness Gate: {'PASS' if report['passed'] else 'FAIL'}"]
    for item in report["checks"]:
        state = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- {state} {item['name']}: observed={item['observed']!r}; required={item['requirement']!r}")
        if item.get("reason"):
            lines.append(f"  reason: {item['reason']}")
    return "\n".join(lines)
