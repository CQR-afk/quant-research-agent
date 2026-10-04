from __future__ import annotations

import tempfile
import shutil
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager

import pandas as pd

from quantlab.data.service import download_market_data, update_market_data
from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.data.validation import validate_daily_bars
from quantlab.providers.akshare import AKShareProvider


def raw_fixture(code="000001"):
    return pd.DataFrame({
        "日期": ["2024-01-02", "2024-01-03"],
        "开盘": [10.0, 10.2], "最高": [10.5, 10.7], "最低": [9.8, 10.0],
        "收盘": [10.2, 10.4], "成交量": [100, 120], "成交额": [100000.0, 120000.0],
    })


def bars_fixture(adjustment="qfq", close=10.2):
    raw = pd.DataFrame({"trade_date": ["2024-01-02"], "open": [10.0], "high": [10.5],
                        "low": [9.8], "close": [close], "volume": [10000], "amount": [100000.]})
    return AKShareProvider().normalize_raw_daily_bars(raw.rename(columns={
        "trade_date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量", "amount": "成交额"
    }), "000001.SZ", adjustment)


class ASharePipelineTests(unittest.TestCase):
    @contextmanager
    def temporary_directory(self):
        path = Path(__file__).parent / "_testdata" / self._testMethodName
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        try:
            yield str(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_symbol_mapping_keeps_zero_and_checks_exchange(self):
        self.assertEqual(normalize_ashare_symbol("000001").symbol, "000001.SZ")
        self.assertEqual(normalize_ashare_symbol("600519.SH").exchange, "SSE")
        with self.assertRaises(ValueError):
            normalize_ashare_symbol("000001.SH")

    def test_akshare_field_mapping_and_volume_unit(self):
        provider = AKShareProvider()
        bars = provider.normalize_raw_daily_bars(raw_fixture(), "000001.SZ", "qfq")
        self.assertEqual(bars.volume.tolist(), [10000, 12000])
        self.assertEqual(bars.amount.tolist(), [100000., 120000.])
        self.assertEqual(bars.source_interface.iloc[0], "stock_zh_a_hist")

    def test_adjustment_partitions_are_isolated_and_upsert_idempotent(self):
        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            qfq = bars_fixture("qfq")
            hfq = bars_fixture("hfq")
            self.assertEqual(store.merge_validated(qfq), 1)
            self.assertEqual(store.merge_validated(qfq), 0)
            self.assertEqual(store.merge_validated(hfq), 1)
            self.assertEqual(len(store.query(symbol="000001.SZ", adjustment="qfq")), 1)
            self.assertEqual(len(store.query(symbol="000001.SZ", adjustment="hfq")), 1)
            self.assertEqual(len(list((Path(temp) / "validated").rglob("*.parquet"))), 2)

    def test_invalid_ohlc_is_rejected_with_issue(self):
        bars = bars_fixture()
        bars.loc[0, "high"] = 8.0
        result = validate_daily_bars(bars)
        self.assertFalse(result.is_valid)
        self.assertIn("invalid_ohlc", result.issues.issue_type.tolist())
        self.assertEqual(len(result.rejected), 1)

    def test_missing_amount_is_not_synthesized_or_accepted(self):
        bars = bars_fixture().copy()
        bars["amount"] = float("nan")
        result = validate_daily_bars(bars, require_amount=True)
        self.assertFalse(result.is_valid)
        self.assertIn("missing_field", result.issues.issue_type.tolist())

    def test_exchange_mismatch_and_wrong_market_are_flagged(self):
        bars = bars_fixture()
        bars.loc[0, "exchange"] = "SSE"
        bars.loc[0, "market"] = "US"
        result = validate_daily_bars(bars, expected_exchange="SZSE")
        self.assertFalse(result.is_valid)
        self.assertTrue({"wrong_market_mapping", "wrong_exchange_mapping"}.intersection(result.issues.issue_type))

    def test_out_of_order_dates_and_missing_sessions_remain_explicit(self):
        bars = bars_fixture()
        second = bars.copy()
        second["trade_date"] = pd.Timestamp("2024-01-04")
        bars = pd.concat([second, bars], ignore_index=True)
        dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        result = validate_daily_bars(bars, expected_trade_dates=dates)
        self.assertFalse(result.is_valid)
        self.assertIn("out_of_order_date", result.issues.issue_type.tolist())
        self.assertIn("missing_expected_session_unknown", result.issues.issue_type.tolist())
        self.assertEqual(result.suspension_status, "Unknown")

    def test_parquet_and_duckdb_round_trip_query(self):
        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            store.merge_validated(bars_fixture())
            parquet = next((Path(temp) / "validated").rglob("*.parquet"))
            self.assertEqual(len(pd.read_parquet(parquet)), 1)
            result = store.query(symbol="000001.SZ", start=date(2024, 1, 1), end=date(2024, 1, 31))
            self.assertEqual(len(result), 1)
            self.assertEqual(result.symbol.iloc[0], "000001.SZ")

    def test_historical_revision_keeps_previous_version(self):
        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            store.merge_validated(bars_fixture(close=10.2))
            revised = bars_fixture(close=10.3)
            self.assertEqual(store.merge_validated(revised), 1)
            result = store.query(symbol="000001.SZ")
            self.assertEqual(result.record_version.iloc[0], 2)
            with store._connect() as con:
                old_revision = con.execute("select revision_id from bar_revisions").fetchone()[0]
                self.assertEqual(con.execute("select count(*) from bar_revisions").fetchone()[0], 1)
            self.assertEqual(result.revision_id.iloc[0], old_revision)

    def test_failed_provider_does_not_replace_valid_existing_rows(self):
        class FailingProvider(AKShareProvider):
            name = "akshare"
            interface = "mock"
            def fetch_raw_daily_bars(self, *args, **kwargs):
                raise RuntimeError("mock upstream failure")

        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            store.merge_validated(bars_fixture())
            with self.assertRaisesRegex(RuntimeError, "mock upstream failure"):
                download_market_data("000001.SZ", provider=FailingProvider(), store=store)
            self.assertEqual(len(store.query(symbol="000001.SZ")), 1)
            with store._connect() as con:
                self.assertEqual(con.execute("select status from ingest_runs order by started_at desc limit 1").fetchone()[0], "failed")

    def test_parquet_write_failure_keeps_previous_catalog_and_rows(self):
        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            store.merge_validated(bars_fixture(close=10.2))
            with store._connect() as con:
                previous_manifest = con.execute("select file_path from parquet_manifest").fetchone()[0]
            with patch("quantlab.data.store.os.replace", side_effect=OSError("simulated disk failure")):
                with self.assertRaisesRegex(OSError, "simulated disk failure"):
                    store.merge_validated(bars_fixture(close=10.3))
            current = store.query(symbol="000001.SZ")
            self.assertEqual(current.close.iloc[0], 10.2)
            self.assertEqual(current.record_version.iloc[0], 1)
            with store._connect() as con:
                self.assertEqual(con.execute("select file_path from parquet_manifest").fetchone()[0], previous_manifest)

    def test_incremental_update_overlaps_last_45_calendar_days(self):
        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            store.merge_validated(bars_fixture())
            provider = AKShareProvider()
            provider.fetch_raw_daily_bars = lambda symbol, start, end, adjustment="qfq": raw_fixture()
            provider.normalize_raw_daily_bars = lambda raw, symbol, adjustment, strict=False: bars_fixture(adjustment)
            with patch("quantlab.data.service.AKShareProvider", return_value=provider):
                with patch.object(provider, "fetch_raw_daily_bars", wraps=provider.fetch_raw_daily_bars) as fetch:
                    result = update_market_data("000001.SZ", store=store)
                    self.assertEqual(fetch.call_args.args[1], date(2023, 11, 18))
                    self.assertEqual(result["updated_rows"], 0)
                    self.assertEqual(len(store.query(symbol="000001.SZ")), 1)

    def test_source_failure_retries_are_bounded_and_timeout_passed(self):
        provider = AKShareProvider(retries=2, retry_delay=0, sleeper=lambda _: None)
        with patch("quantlab.providers.akshare.ak.stock_zh_a_hist", side_effect=TimeoutError("timeout")) as fetch:
            with self.assertRaisesRegex(RuntimeError, "after 2 attempts"):
                provider.fetch_raw_daily_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 31))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.kwargs["timeout"], 20.0)

    def test_raw_data_archived_before_validation_reject(self):
        class BadProvider(AKShareProvider):
            def fetch_raw_daily_bars(self, *args, **kwargs):
                return raw_fixture()
            def normalize_raw_daily_bars(self, raw, symbol, adjustment, strict=False):
                bars = bars_fixture()
                bars.loc[0, "high"] = 8.0
                return bars

        with self.temporary_directory() as temp:
            store = MarketDataStore(temp)
            result = download_market_data("000001.SZ", provider=BadProvider(), store=store)
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(len(list((Path(temp) / "raw").rglob("*.parquet"))), 1)
            self.assertTrue(store.query(symbol="000001.SZ").empty)


if __name__ == "__main__":
    unittest.main()
