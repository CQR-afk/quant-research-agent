"""Run the research readiness gate without invoking a model trainer."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import project_root
from quantlab.research.readiness import evaluate_readiness, format_readiness_report
from quantlab.research.splits import FixedPeriodSplitter


def evaluate_dataset_readiness(
    *, dataset_path: Path | str | None = None, manifest_path: Path | str | None = None,
    root: Path | str | None = None,
) -> dict[str, object]:
    root = Path(root) if root else project_root()
    data_path = Path(dataset_path) if dataset_path else root / "data" / "processed" / "ashare_research_dataset_v1.parquet"
    if not data_path.is_absolute():
        data_path = root / data_path
    manifest_file = Path(manifest_path) if manifest_path else data_path.with_suffix(".manifest.json")
    if not manifest_file.is_absolute():
        manifest_file = root / manifest_file
    if not data_path.exists() or not manifest_file.exists():
        raise FileNotFoundError("Research dataset or its manifest is missing; build the dataset first.")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    config = json.loads((root / "config" / "research.json").read_text(encoding="utf-8-sig"))
    dataset = pd.read_parquet(data_path)
    coverage = pd.DataFrame(manifest.get("coverage", []))
    calendar = AShareTradingCalendar(manifest["research_start"], manifest["research_end"])
    result = evaluate_readiness(dataset, coverage, config, FixedPeriodSplitter(config, calendar))
    report = dict(result.report)
    report["dataset_path"] = str(data_path.resolve())
    report["manifest_path"] = str(manifest_file.resolve())
    report["dataset_id"] = manifest.get("dataset_id")
    report["dataset_provider"] = manifest.get("provider")
    report["adjustment"] = manifest.get("adjustment")
    report["evaluated_at_utc"] = datetime.now(timezone.utc).isoformat()
    report_path = root / "artifacts" / "reports" / (
        "readiness_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8] + ".json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name("." + report_path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(report_path)
    report["readiness_report"] = str(report_path.resolve())
    print(format_readiness_report(report))
    return {"status": "pass" if result.passed else "fail", "report": report}
