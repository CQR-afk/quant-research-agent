"""Command-line interface for the QuantLab research scaffold."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from quantlab.config import settings
from quantlab.market_data import download_daily_prices
from quantlab.modeling import train_and_evaluate
from quantlab.news import format_news_note, research_news
from quantlab.data.service import (
    download_adjustment_factors, download_market_data_routed, get_data_status,
    query_market_data, update_market_data_routed, validate_market_data,
)
from quantlab.data.backfill import backfill_market_data, backfill_status
from quantlab.data.reconciliation import reconcile_providers
from quantlab.data.adjusted import build_adjusted_price_view
from quantlab.providers.router import ProviderRouter
from quantlab.experiments import evaluate_saved_experiment, list_experiments, train_lightgbm
from quantlab.research.dataset import build_research_dataset, dataset_status
from quantlab.research.gate import evaluate_dataset_readiness
from quantlab.research.price_basis import compare_price_bases
from quantlab.research.walk_forward import run_walk_forward_experiment
from quantlab.research.features import FEATURE_VERSION, FEATURE_V2_VERSION
from quantlab.research.target_study import run_target_study
from quantlab.research.targets import DEFAULT_MINIMUM_TARGET_CROSS_SECTION, TARGET_VERSIONS
from quantlab.research.u100_quality import audit_u100_data
from quantlab.research.u100_breadth import evaluate_u100_breadth
from quantlab.research.breadth_study import run_breadth_study, verify_saved_breadth_report
from quantlab.research.signal_diagnostics import run_signal_diagnostics, write_signal_diagnostics


def _safe_ticker(ticker: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ticker.strip())
    if not safe:
        raise ValueError("Ticker cannot be empty.")
    return safe.upper()


def run_research(tickers: list[str], period: str) -> None:
    output_dir = settings.resolved_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for ticker in tickers:
        safe = _safe_ticker(ticker)
        print(f"Downloading daily prices for {safe} ({period})...")
        prices = download_daily_prices(safe, period)
        result = train_and_evaluate(prices, safe)
        result.metrics["next_session_probability_up"] = result.next_session_probability_up
        result.metrics["generated_at_utc"] = datetime.now(timezone.utc).isoformat()

        metrics_path = output_dir / f"{safe.lower()}_metrics.json"
        predictions_path = output_dir / f"{safe.lower()}_holdout_predictions.csv"
        metrics_path.write_text(json.dumps(result.metrics, indent=2), encoding="utf-8")
        result.holdout_predictions.to_csv(predictions_path)
        print(json.dumps(result.metrics, indent=2))
        print(f"Saved {metrics_path} and {predictions_path}")


def run_news(ticker: str) -> None:
    safe = _safe_ticker(ticker)
    result = research_news(safe)
    output_dir = settings.resolved_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    note_path = output_dir / f"{safe.lower()}_news.md"
    note = format_news_note(safe, result)
    note_path.write_text(note, encoding="utf-8")
    print(note)
    print(f"Saved {note_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantlab", description="Local daily quantitative research prototype")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="download prices and train a local daily direction model")
    run_parser.add_argument("--ticker", nargs="+", default=[settings.default_ticker])
    run_parser.add_argument("--period", default=settings.data_period)

    news_parser = subparsers.add_parser("news", help="research current ticker news with the OpenAI API")
    news_parser.add_argument("--ticker", default=settings.default_ticker)

    data = subparsers.add_parser("data", help="manage validated local market data")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    for command in ("download", "update", "validate", "query"):
        item = data_commands.add_parser(command, help=f"{command} A-share market data")
        item.add_argument("--market", choices=("ashare",), default="ashare")
        item.add_argument("--ticker", nargs="+" if command in {"download", "update"} else None,
                          required=command != "query")
        item.add_argument("--start", type=_parse_date, default=None)
        item.add_argument("--end", type=_parse_date, default=None)
        item.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default="none")
        item.add_argument("--provider", default="auto", help="provider name or auto")
        item.add_argument("--development-only", action="store_true",
                          help="cap this request and any incremental status query at 2023-12-31")
        if command == "update":
            item.add_argument("--full-refresh", action="store_true")
        if command == "query":
            item.set_defaults(ticker=None)
    status_parser = data_commands.add_parser("status", help="show locally stored market-data status")
    status_parser.add_argument("--universe", default=None)
    status_parser.add_argument("--start", type=_parse_date, default=None)
    status_parser.add_argument("--end", type=_parse_date, default=None)
    status_parser.add_argument("--provider", default=None)
    status_parser.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default=None)
    status_parser.add_argument("--development-only", action="store_true")
    data_commands.add_parser("providers", help="show provider availability and capabilities")
    backfill = data_commands.add_parser("backfill", help="run one resumable historical-data batch")
    backfill.add_argument("--market", choices=("ashare",), default="ashare")
    backfill.add_argument("--universe", default=None)
    backfill.add_argument("--start", type=_parse_date, default=date(2018, 1, 1))
    backfill.add_argument("--end", type=_parse_date, default=date(2025, 12, 31))
    backfill.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default="none")
    backfill.add_argument("--provider", default="auto")
    backfill.add_argument("--retry-failed-only", action="store_true")
    backfill.add_argument("--limit", type=int, default=None)
    backfill.add_argument("--development-only", action="store_true",
                          help="reject backfills beyond 2023-12-31")
    backfill_status_parser = data_commands.add_parser("backfill-status", help="show resumable backfill jobs")
    backfill_status_parser.add_argument("--universe", default=None)
    backfill_status_parser.add_argument("--start", type=_parse_date, default=None)
    backfill_status_parser.add_argument("--end", type=_parse_date, default=None)
    backfill_status_parser.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default=None)
    backfill_status_parser.add_argument("--provider", default=None)
    backfill_status_parser.add_argument("--development-only", action="store_true")
    adjusted_view = data_commands.add_parser("adjusted-view", help="read one provider's stored price basis and provenance")
    adjusted_view.add_argument("--ticker", required=True)
    adjusted_view.add_argument("--start", type=_parse_date, default=None)
    adjusted_view.add_argument("--end", type=_parse_date, default=None)
    adjusted_view.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default="none")
    adjusted_view.add_argument("--provider", default=None)
    factors = data_commands.add_parser("factors", help="download independent adjustment factors")
    factors.add_argument("--ticker", required=True)
    factors.add_argument("--start", type=_parse_date, default=None)
    factors.add_argument("--end", type=_parse_date, default=None)
    reconcile = data_commands.add_parser("reconcile", help="compare providers over a small window")
    reconcile.add_argument("--ticker", required=True)
    reconcile.add_argument("--provider-a", required=True)
    reconcile.add_argument("--provider-b", required=True)
    reconcile.add_argument("--start", type=_parse_date, required=True)
    reconcile.add_argument("--end", type=_parse_date, required=True)
    reconcile.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default="none")
    compare = data_commands.add_parser("compare-bases", help="compare none/qfq prices and flag mechanical jumps")
    compare.add_argument("--ticker", nargs="+", required=True)
    compare.add_argument("--provider", required=True)
    compare.add_argument("--start", type=_parse_date, default=None)
    compare.add_argument("--end", type=_parse_date, default=None)
    compare.add_argument("--jump-threshold", type=float, default=0.20)

    research = subparsers.add_parser("research", help="build and inspect model-ready datasets")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    build_dataset = research_commands.add_parser("build-dataset", help="build features and five-observation labels from DuckDB")
    build_dataset.add_argument("--start", type=_parse_date, default=None)
    build_dataset.add_argument("--end", type=_parse_date, default=None)
    build_dataset.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default=None)
    build_dataset.add_argument("--provider", default=None)
    build_dataset.add_argument("--universe", default=None)
    build_dataset.add_argument("--output", default=None, help="optional output Parquet path")
    build_dataset.add_argument("--development-only", action="store_true",
                               help="reject dataset scopes beyond 2023-12-31")
    build_dataset.add_argument("--reused-universe-hash", default=None,
                               help="record the frozen parent universe reused for this dataset")
    build_dataset.add_argument("--summary-only", action="store_true",
                               help="omit the per-symbol coverage table from terminal output")
    build_dataset.add_argument("--feature-version", choices=(FEATURE_VERSION, FEATURE_V2_VERSION), default=FEATURE_VERSION)
    research_commands.add_parser("dataset-status", help="show model-ready dataset coverage and lineage")
    readiness = research_commands.add_parser("readiness", help="evaluate the data gate without fitting a model")
    readiness.add_argument("--dataset", default=None)
    readiness.add_argument("--manifest", default=None)
    walk_forward = research_commands.add_parser(
        "walk-forward", help="compare v1 and scale-invariant v2 on expanding pre-2024 folds only"
    )
    walk_forward.add_argument("--dataset-v1", required=True)
    walk_forward.add_argument("--manifest-v1", required=True)
    walk_forward.add_argument("--dataset-v2", required=True)
    walk_forward.add_argument("--manifest-v2", required=True)
    target_study = research_commands.add_parser(
        "target-study", help="compare raw, pool-excess, and cross-sectional rank targets on fixed pre-2024 folds"
    )
    target_study.add_argument("--dataset", default=None, help="development-only ashare_daily_v1 Parquet; defaults to Step 4.3 snapshot")
    target_study.add_argument("--manifest", default=None, help="matching development-only dataset manifest")
    target_study.add_argument("--target-version", required=True, choices=("all", *TARGET_VERSIONS))
    target_study.add_argument("--minimum-target-cross-section", type=int,
                               default=DEFAULT_MINIMUM_TARGET_CROSS_SECTION)
    u100_quality = research_commands.add_parser(
        "u100-quality", help="audit frozen U100 none/qfq data in a date-bounded development scope"
    )
    u100_quality.add_argument("--universe", default="config/ashare_universe_breadth_100.json")
    u100_quality.add_argument("--start", type=_parse_date, default=date(2018, 1, 1))
    u100_quality.add_argument("--end", type=_parse_date, default=date(2023, 12, 31))
    u100_quality.add_argument("--output-csv", default="data/processed/ashare_u100_symbol_quality_2018_2023.csv")
    u100_quality.add_argument("--output-json", default="data/processed/ashare_u100_data_quality_2018_2023.json")
    u100_breadth = research_commands.add_parser(
        "u100-breadth", help="evaluate the fixed development-only U100 breadth gate"
    )
    u100_breadth.add_argument("--dataset", required=True)
    u100_breadth.add_argument("--manifest", default=None)
    u100_breadth.add_argument("--output", default="data/processed/ashare_u100_breadth_gate_2018_2023.json")
    research_commands.add_parser(
        "breadth-study", help="compare frozen U11/U50/U100 LightGBM on 2018-2023 development folds"
    )
    breadth_verify = research_commands.add_parser(
        "breadth-verify", help="reload saved breadth models on their saved development inputs"
    )
    breadth_verify.add_argument("--report", required=True)
    research_commands.add_parser(
        "signal-diagnostics", help="post-hoc attribution of the frozen Step 4.5C development experiments; no training"
    )

    model = subparsers.add_parser("model", help="train and inspect research models")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    train = model_commands.add_parser("train", help="run the readiness-gated LightGBM baseline")
    train.add_argument("--model", choices=("lightgbm",), default="lightgbm")
    train.add_argument("--dataset", default=None, help="explicit model-ready Parquet dataset")
    train.add_argument("--manifest", default=None, help="matching dataset manifest JSON")
    train.add_argument("--name", default="Baseline Real Market Experiment v1")
    evaluate = model_commands.add_parser("evaluate", help="reload and re-evaluate a saved experiment")
    evaluate.add_argument("--model", choices=("lightgbm",), default="lightgbm")
    evaluate.add_argument("--experiment-id", default=None)
    model_commands.add_parser("experiments", help="list immutable local experiments")
    return parser


def _parse_date(value: str) -> date:
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("Date must use YYYYMMDD or YYYY-MM-DD.")


def _run_data(args) -> None:
    if args.data_command == "status":
        symbols = None
        if args.universe:
            universe_path = Path(args.universe)
            if not universe_path.is_absolute():
                universe_path = Path(__file__).resolve().parents[2] / universe_path
            universe_payload = json.loads(universe_path.read_text(encoding="utf-8-sig"))
            symbols = [str(member["symbol"]) for member in universe_payload.get("members", [])]
        print(get_data_status(start=args.start, end=args.end, symbols=symbols,
                              source=args.provider, adjustment=args.adjustment,
                              development_only=args.development_only).to_string(index=False))
    elif args.data_command in {"download", "update"}:
        router = ProviderRouter()
        outcomes = []
        for ticker in args.ticker:
            if args.data_command == "update":
                outcomes.append(update_market_data_routed(
                    ticker, market=args.market, start=args.start, end=args.end, adjustment=args.adjustment,
                    provider=args.provider, full_refresh=args.full_refresh, router=router,
                    development_only=args.development_only,
                ))
            else:
                outcomes.append(download_market_data_routed(
                    ticker, market=args.market, start=args.start, end=args.end, adjustment=args.adjustment,
                    provider=args.provider, router=router, development_only=args.development_only,
                ))
        for result in outcomes:
            printable = {key: value for key, value in result.items() if key != "issues"}
            print(json.dumps(printable, default=str, indent=2))
        failed = [result for result in outcomes if result["status"] in {"failed", "rejected"}]
        if failed:
            raise RuntimeError(f"{len(failed)} ticker task(s) failed or were rejected; other symbols were processed and outcomes recorded.")
    elif args.data_command == "validate":
        for ticker in [args.ticker] if isinstance(args.ticker, str) else args.ticker:
            result = validate_market_data(ticker, adjustment=args.adjustment, provider=None if args.provider == "auto" else args.provider)
            print(f"{ticker}: valid={result.is_valid}, accepted={len(result.accepted)}, rejected={len(result.rejected)}")
            if not result.issues.empty:
                print(result.issues.to_string(index=False))
    elif args.data_command == "query":
        print(query_market_data(ticker=args.ticker, market=args.market, start=args.start, end=args.end,
                                adjustment=args.adjustment, source=None if args.provider == "auto" else args.provider).to_string(index=False))
    elif args.data_command == "providers":
        print(json.dumps(ProviderRouter().statuses(), ensure_ascii=False, indent=2))
    elif args.data_command == "backfill":
        result = backfill_market_data(universe=args.universe, start=args.start, end=args.end,
                                      adjustment=args.adjustment, provider=args.provider,
                                      retry_failed_only=args.retry_failed_only, limit=args.limit,
                                      development_only=args.development_only)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    elif args.data_command == "backfill-status":
        print(backfill_status(universe=args.universe, start=args.start, end=args.end,
                              adjustment=args.adjustment, provider=args.provider,
                              development_only=args.development_only).to_json(
                                  orient="records", force_ascii=False, date_format="iso", indent=2))
    elif args.data_command == "adjusted-view":
        result = build_adjusted_price_view(args.ticker, args.start, args.end, args.adjustment, args.provider)
        print(result.to_json(orient="records", force_ascii=False, date_format="iso", indent=2))
    elif args.data_command == "factors":
        print(json.dumps(download_adjustment_factors(args.ticker, start=args.start, end=args.end),
                         ensure_ascii=False, default=str, indent=2))
    elif args.data_command == "reconcile":
        print(json.dumps(reconcile_providers(args.ticker, provider_a=args.provider_a,
                         provider_b=args.provider_b, start=args.start, end=args.end,
                         adjustment=args.adjustment), ensure_ascii=False, default=str, indent=2))
    elif args.data_command == "compare-bases":
        print(json.dumps(compare_price_bases(args.ticker, provider=args.provider, start=args.start,
                         end=args.end, jump_threshold=args.jump_threshold), ensure_ascii=False, indent=2, default=str))


def _run_research(args) -> None:
    if args.research_command == "build-dataset":
        result = build_research_dataset(start=args.start, end=args.end, adjustment=args.adjustment,
                                        provider=args.provider, pool_path=args.universe, output_path=args.output,
                                        feature_version=args.feature_version,
                                        development_only=args.development_only,
                                        reused_universe_hash=args.reused_universe_hash)
        payload = {"dataset_path": str(result.dataset_path), "manifest_path": str(result.manifest_path),
                   "manifest": {key: value for key, value in result.manifest.items() if key != "coverage"}}
        if args.summary_only:
            payload["coverage_symbols"] = int(result.coverage["symbol"].nunique()) if not result.coverage.empty else 0
        else:
            payload["coverage"] = result.coverage.to_dict("records")
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "dataset-status":
        result = dataset_status()
        if result.get("status") == "available":
            result = {key: value for key, value in result.items() if key != "coverage"}
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "readiness":
        print(json.dumps(evaluate_dataset_readiness(dataset_path=args.dataset, manifest_path=args.manifest),
                         ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "walk-forward":
        result = run_walk_forward_experiment(
            dataset_v1_path=args.dataset_v1, manifest_v1_path=args.manifest_v1,
            dataset_v2_path=args.dataset_v2, manifest_v2_path=args.manifest_v2,
        )
        report = result["report"]
        print(json.dumps({
            "status": result["status"], "report_path": result["report_path"],
            "feature_v1_experiment_id": report["feature_v1"]["experiment_id"],
            "feature_v2_experiment_id": report["feature_v2"]["experiment_id"],
            "candidate_assessment": report["candidate_assessment"],
        }, ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "target-study":
        result = run_target_study(
            dataset_path=args.dataset, manifest_path=args.manifest,
            target_version=args.target_version,
            minimum_cross_section=args.minimum_target_cross_section,
        )
        print(json.dumps({
            "status": result["status"],
            "target_versions_run": result["target_versions_run"],
            "datasets": result["datasets"],
            "experiments": result["experiments"],
            "report_path": result["report_path"],
            "candidate_assessment": result["candidate_assessment"],
        }, ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "u100-quality":
        quality, summary = audit_u100_data(universe_path=args.universe, start=args.start, end=args.end)
        csv_path, json_path = Path(args.output_csv), Path(args.output_json)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        quality.to_csv(csv_path, index=False, encoding="utf-8-sig")
        json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({**{key: value for key, value in summary.items() if key != "per_symbol_snapshot_times"},
                          "quality_csv": str(csv_path), "quality_json": str(json_path)},
                         ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "u100-breadth":
        dataset_path = Path(args.dataset)
        manifest_path = Path(args.manifest) if args.manifest else dataset_path.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame = pd.read_parquet(dataset_path)
        report = evaluate_u100_breadth(frame, pd.DataFrame(manifest.get("coverage", [])),
                                       start=manifest["research_start"], end=manifest["research_end"])
        report["dataset_path"] = str(dataset_path)
        report["dataset_sha256"] = manifest.get("dataset_sha256")
        report["universe_hash"] = manifest.get("universe_hash")
        report["feature_version"] = manifest.get("feature_version")
        report["target_version"] = manifest.get("target_version")
        report["provider"] = manifest.get("provider")
        report["adjustment"] = manifest.get("adjustment")
        report["mixed_vendor_snapshot_times"] = True
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "coverage_by_symbol"},
                         ensure_ascii=False, default=str, indent=2))
    elif args.research_command == "breadth-study":
        result = run_breadth_study()
        print(json.dumps({"status": result["report"]["status"],
                          "report_path": result["report_path"],
                          "experiments": {name: item["experiment_id"]
                                          for name, item in result["report"]["experiments"].items()}},
                         ensure_ascii=False, indent=2))
    elif args.research_command == "breadth-verify":
        print(json.dumps(verify_saved_breadth_report(Path(args.report)), ensure_ascii=False, indent=2))
    elif args.research_command == "signal-diagnostics":
        result = run_signal_diagnostics()
        outputs = write_signal_diagnostics(result)
        print(json.dumps({"status": result["payload"]["status"],
                          "diagnostic_role": result["payload"]["diagnostic_role"], **outputs},
                         ensure_ascii=False, indent=2))


def _run_model(args) -> None:
    if args.model_command == "train":
        result = train_lightgbm(dataset_path=args.dataset, manifest_path=args.manifest, experiment_name=args.name)
    elif args.model_command == "evaluate":
        result = evaluate_saved_experiment(args.experiment_id)
    else:
        result = list_experiments()
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "run":
            run_research(args.ticker, args.period)
        elif args.command == "news":
            run_news(args.ticker)
        elif args.command == "data":
            _run_data(args)
        elif args.command == "research":
            _run_research(args)
        elif args.command == "model":
            _run_model(args)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

