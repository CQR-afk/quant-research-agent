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
from quantlab.data.service import download_market_data, get_data_status, query_market_data, update_market_data, validate_market_data


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
        item.add_argument("--adjustment", choices=("none", "qfq", "hfq"), default="qfq")
        if command == "update":
            item.add_argument("--full-refresh", action="store_true")
        if command == "query":
            item.set_defaults(ticker=None)
    data_commands.add_parser("status", help="show locally stored market-data status")
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
        for ticker in args.ticker:
            if args.data_command == "download":
                result = download_market_data(ticker, market=args.market, start=args.start, end=args.end, adjustment=args.adjustment)
            else:
                result = update_market_data(ticker, market=args.market, start=args.start, end=args.end,
                                            adjustment=args.adjustment, full_refresh=args.full_refresh)
            printable = {key: value for key, value in result.items() if key != "issues"}
            print(json.dumps(printable, default=str, indent=2))
            if result["status"] != "success":
                raise ValueError(f"Data validation rejected {ticker}; raw data and validation issues were retained.")
    elif args.data_command == "validate":
        for ticker in [args.ticker] if isinstance(args.ticker, str) else args.ticker:
            result = validate_market_data(ticker, adjustment=args.adjustment)
            print(f"{ticker}: valid={result.is_valid}, accepted={len(result.accepted)}, rejected={len(result.rejected)}")
            if not result.issues.empty:
                print(result.issues.to_string(index=False))
    elif args.data_command == "query":
        print(query_market_data(ticker=args.ticker, market=args.market, start=args.start, end=args.end,
                                adjustment=args.adjustment).to_string(index=False))


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
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

