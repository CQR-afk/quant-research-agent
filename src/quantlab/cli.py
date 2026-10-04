"""Command-line interface for the QuantLab research scaffold."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone

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
        if command == "update":
            item.add_argument("--full-refresh", action="store_true")
        if command == "query":
            item.set_defaults(ticker=None)
    data_commands.add_parser("status", help="show locally stored market-data status")
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
    data_commands.add_parser("backfill-status", help="show resumable backfill jobs")
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
        print(get_data_status().to_string(index=False))
    elif args.data_command in {"download", "update"}:
        router = ProviderRouter()
        outcomes = []
        for ticker in args.ticker:
            if args.data_command == "update":
                outcomes.append(update_market_data_routed(
                    ticker, market=args.market, start=args.start, end=args.end, adjustment=args.adjustment,
                    provider=args.provider, full_refresh=args.full_refresh, router=router,
                ))
            else:
                outcomes.append(download_market_data_routed(
                    ticker, market=args.market, start=args.start, end=args.end, adjustment=args.adjustment,
                    provider=args.provider, router=router,
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
                                      retry_failed_only=args.retry_failed_only, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    elif args.data_command == "backfill-status":
        print(backfill_status().to_json(orient="records", force_ascii=False, date_format="iso", indent=2))
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
                                        feature_version=args.feature_version)
        print(json.dumps({"dataset_path": str(result.dataset_path), "manifest_path": str(result.manifest_path),
                          "manifest": {key: value for key, value in result.manifest.items() if key != "coverage"},
                          "coverage": result.coverage.to_dict("records")}, ensure_ascii=False, default=str, indent=2))
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

