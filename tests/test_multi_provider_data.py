from __future__ import annotations

import json
import shutil
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import requests

from quantlab.data.adjusted import build_adjusted_price_view
from quantlab.data.backfill import backfill_market_data
from quantlab.data.reconciliation import reconcile_providers
from quantlab.data.service import download_market_data, download_market_data_routed, get_data_status, query_market_data
from quantlab.data.store import MarketDataStore
from quantlab.providers.baostock import BaoStockProvider
from quantlab.providers.capabilities import ProviderCapabilities
from quantlab.providers.router import ProviderRouter
from quantlab.providers.tushare import TushareProvider


class FakeTushareClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def daily(self, **kwargs):
        self.calls.append(("daily", kwargs))
        return self.frame

    def adj_factor(self, **kwargs):
        self.calls.append(("adj_factor", kwargs))
        return pd.DataFrame({"trade_date": ["20240102"], "adj_factor": [1.25]})


class FakeCursor:
    error_code = "0"
    error_msg = "success"
    fields = ["date", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag", "tradestatus", "isST"]

    def __init__(self):
        self.rows = [["2024-01-02", "sz.000001", "10", "10.5", "9.8", "10.2", "10000", "100000", "2", "1", "0"]]
        self.i = 0

    def next(self):
        self.i += 1
        return self.i <= len(self.rows)

    def get_row_data(self):
        return self.rows[self.i - 1]


class FakeBaoSDK:
    def __init__(self):
        self.query = None

    def login(self):
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_history_k_data_plus(self, *args, **kwargs):
        self.query = (args, kwargs)
        return FakeCursor()

    def logout(self):
        return SimpleNamespace(error_code="0")


class FakeProvider:
    name = "fake"
    interface = "fake_daily"
    version = "1"
    capabilities = ProviderCapabilities(daily_bars=True, raw_prices=True, markets=("CN_A",), adjustments=("none",))
    available = True
    availability_reason = None

    def fetch_raw_daily_bars(self, symbol, start=None, end=None, *, adjustment="none"):
        self.symbol = symbol
        return pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03"], "open": [10, 10.1],
                             "high": [10.5, 10.6], "low": [9.8, 10.0], "close": [10.2, 10.4],
                             "volume": [10000, 11000], "amount": [100000, 110000]})

    def normalize_raw_daily_bars(self, raw, symbol, adjustment, *, strict=False):
        from quantlab.data.schema import normalize_daily_bars
        from quantlab.data.symbols import normalize_ashare_symbol
        identity = normalize_ashare_symbol(symbol)
        return normalize_daily_bars(raw, market="CN_A", exchange=identity.exchange, symbol=identity.symbol,
                                    adjustment=adjustment, currency="CNY", source=self.name,
                                    provider_version=self.version, source_interface=self.interface, strict=strict)

    def normalize_provider_metadata(self, raw, symbol):
        return pd.DataFrame(columns=["trade_date", "metadata_json"])


class MultiProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(__file__).parent / "_testdata" / "multi_provider" / self._testMethodName
        self.temp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_tushare_daily_field_mapping_and_units(self):
        client = FakeTushareClient(pd.DataFrame({"trade_date": ["20240102"], "open": [10], "high": [11],
            "low": [9], "close": [10.5], "vol": [12.5], "amount": [100.0]}))
        provider = TushareProvider("test-only", pro_factory=lambda token, **kwargs: client)
        raw = provider.fetch_raw_daily_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 31))
        bars = provider.normalize_raw_daily_bars(raw, "000001.SZ", "none")
        self.assertEqual(bars.volume.iloc[0], 1250)
        self.assertEqual(bars.amount.iloc[0], 100000)
        self.assertEqual(client.calls[0][1]["ts_code"], "000001.SZ")

    def test_tushare_factors_are_separate_and_revisions_are_versioned(self):
        client = FakeTushareClient(pd.DataFrame())
        provider = TushareProvider("test-only", pro_factory=lambda token, **kwargs: client)
        factor = provider.fetch_adjustment_factors("000001.SZ")
        store = MarketDataStore(self.temp)
        self.assertEqual(store.merge_adjustment_factors(factor), 1)
        self.assertEqual(store.merge_adjustment_factors(factor), 0)
        revised = factor.copy()
        revised["adjustment_factor"] = 1.3
        self.assertEqual(store.merge_adjustment_factors(revised), 1)
        current = store.query_adjustment_factors(symbol="000001.SZ")
        self.assertEqual(current.adjustment_factor.tolist(), [1.3])
        self.assertEqual(current.record_version.tolist(), [2])
        with store._connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM adjustment_factor_revisions").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM market_bars").fetchone()[0], 0)

    def test_tushare_without_token_reports_unavailable_without_calling_cloud(self):
        provider = TushareProvider(token="")
        self.assertFalse(provider.available)
        self.assertEqual(provider.availability_reason, "missing credential")

    def test_tushare_permission_errors_are_sanitized(self):
        class Denied:
            def daily(self, **kwargs):
                raise RuntimeError("permission denied token=do-not-print")
        provider = TushareProvider("do-not-print", pro_factory=lambda *args, **kwargs: Denied())
        with self.assertRaisesRegex(RuntimeError, "permission denied") as error:
            provider.fetch_raw_daily_bars("000001.SZ")
        self.assertNotIn("do-not-print", str(error.exception))

    def test_tushare_timeout_retries_are_bounded_with_exponential_backoff(self):
        class SlowThenGood:
            calls = 0
            def daily(self, **kwargs):
                self.calls += 1
                if self.calls < 3:
                    raise requests.Timeout("provider timed out")
                return pd.DataFrame({"trade_date": ["20240102"], "open": [10], "high": [11],
                    "low": [9], "close": [10.5], "vol": [1], "amount": [1]})
        client = SlowThenGood()
        delays = []
        provider = TushareProvider("test-only", retries=3, retry_delay=0.1,
            sleeper=delays.append, pro_factory=lambda *args, **kwargs: client)
        self.assertEqual(len(provider.fetch_raw_daily_bars("000001.SZ")), 1)
        self.assertEqual(client.calls, 3)
        self.assertEqual(delays, [0.1, 0.2])

    def test_baostock_code_adjustment_and_metadata_mapping(self):
        sdk = FakeBaoSDK()
        provider = BaoStockProvider(sdk=sdk)
        raw = provider.fetch_raw_daily_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 31), adjustment="qfq")
        args, kwargs = sdk.query
        self.assertEqual(args[0], "sz.000001")
        self.assertEqual(kwargs["adjustflag"], "2")
        bars = provider.normalize_raw_daily_bars(raw, "000001.SZ", "qfq")
        self.assertEqual(bars.volume.iloc[0], 10000)
        metadata = provider.normalize_provider_metadata(raw, "000001.SZ")
        self.assertIn('"tradestatus": "1"', metadata.metadata_json.iloc[0])

    def test_development_scope_rejects_post_2023_before_provider_or_store_io(self):
        provider = FakeProvider()
        store = MarketDataStore(self.temp)
        with self.assertRaisesRegex(ValueError, "capped at 2023-12-31"):
            download_market_data("000532.SZ", start=date(2023, 1, 1), end=date(2024, 1, 2),
                                 provider=provider, store=store, development_only=True)
        self.assertFalse(hasattr(provider, "symbol"))
        with self.assertRaisesRegex(ValueError, "capped at 2023-12-31"):
            backfill_market_data(
                universe=Path(__file__).resolve().parents[1] /
                    "config/ashare_universe_breadth_100_additions_2018_2023_v1.json",
                start=date(2018, 1, 1), end=date(2024, 1, 2), provider="baostock",
                store=store, development_only=True,
            )
        with self.assertRaisesRegex(ValueError, "start no earlier than 2018-01-01"):
            download_market_data("000532.SZ", start=date(2017, 12, 31), end=date(2023, 12, 31),
                                 provider=provider, store=store, development_only=True)
        with store._connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM ingest_runs").fetchone()[0], 0)

    def test_market_status_aggregates_only_the_requested_development_dates(self):
        store = MarketDataStore(self.temp)
        provider = FakeProvider()
        bars = provider.normalize_raw_daily_bars(provider.fetch_raw_daily_bars("000001.SZ"), "000001.SZ", "none")
        bars["trade_date"] = [date(2023, 12, 29), date(2024, 1, 2)]
        store.merge_validated(bars)
        status = get_data_status(start=date(2018, 1, 1), end=date(2023, 12, 31),
                                 symbols=["000001.SZ"], source="fake", adjustment="none", store=store,
                                 development_only=True)
        self.assertEqual(len(status), 1)
        self.assertEqual(status.iloc[0].row_count, 1)
        self.assertEqual(status.iloc[0].latest_date.date(), date(2023, 12, 29))

    def test_baostock_transient_sdk_failure_retries_and_restores_socket_timeout(self):
        class TransientBaoSDK(FakeBaoSDK):
            queries = 0
            def query_history_k_data_plus(self, *args, **kwargs):
                self.queries += 1
                if self.queries == 1:
                    return SimpleNamespace(error_code="NETWORK", error_msg="connection timeout")
                return FakeCursor()
        import socket
        previous = socket.getdefaulttimeout()
        sdk = TransientBaoSDK()
        delays = []
        provider = BaoStockProvider(sdk=sdk, retries=2, retry_delay=0.1, sleeper=delays.append)
        raw = provider.fetch_raw_daily_bars("000001.SZ", date(2024, 1, 1), date(2024, 1, 31))
        self.assertEqual(len(raw), 1)
        self.assertEqual(sdk.queries, 2)
        self.assertEqual(delays, [0.1])
        self.assertEqual(socket.getdefaulttimeout(), previous)

    def test_adjusted_view_never_ambiguously_selects_provider(self):
        store = MarketDataStore(self.temp)
        ak = FakeProvider().normalize_raw_daily_bars(FakeProvider().fetch_raw_daily_bars("000001.SZ"),
                                                       "000001.SZ", "none")
        ak["source"] = "akshare"
        bs = ak.copy()
        bs["source"] = "baostock"
        store.merge_validated(ak)
        store.merge_validated(bs)
        with self.assertRaisesRegex(ValueError, "specify provider"):
            build_adjusted_price_view("000001.SZ", None, None, "none", store=store)
        view = build_adjusted_price_view("000001.SZ", None, None, "none", "akshare", store=store)
        self.assertEqual(view.provider.unique().tolist(), ["akshare"])
        self.assertEqual(view.price_basis.iloc[0], "canonical_unadjusted_vendor")

    def test_router_skips_missing_tushare_credential_then_uses_one_fallback_source(self):
        config = self.temp / "sources.json"
        config.write_text(json.dumps({"download": {"providers": ["tushare", "fake"]},
            "historical_backfill": {"providers": ["fake"], "provider_interval_seconds": {"fake": 0}}}))
        router = ProviderRouter(config_path=config, factories={"tushare": lambda: TushareProvider(token=""),
                                                               "fake": FakeProvider}, sleeper=lambda _: None)
        store = MarketDataStore(self.temp / "db")
        result = download_market_data_routed("000001.SZ", start=date(2024, 1, 2), end=date(2024, 1, 3),
                                             adjustment="none", router=router, store=store)
        self.assertEqual(result["selected_provider"], "fake")
        self.assertEqual(len(store.query()), 2)
        self.assertEqual(store.query().source.unique().tolist(), ["fake"])

    def test_router_records_failed_provider_then_falls_back_for_complete_range(self):
        class BrokenProvider(FakeProvider):
            name = "broken"
            def fetch_raw_daily_bars(self, *args, **kwargs):
                raise ConnectionError("fixture upstream failure")
        config = self.temp / "fallback-sources.json"
        config.write_text(json.dumps({"download": {"providers": ["broken", "fake"]},
            "historical_backfill": {"providers": ["fake"], "provider_interval_seconds": {"broken": 0, "fake": 0}}}))
        router = ProviderRouter(config_path=config, factories={"broken": BrokenProvider, "fake": FakeProvider},
                                sleeper=lambda _: None)
        store = MarketDataStore(self.temp / "fallback-db")
        result = download_market_data_routed("000001.SZ", start=date(2024, 1, 2), end=date(2024, 1, 3),
                                             adjustment="none", router=router, store=store)
        self.assertEqual(result["selected_provider"], "fake")
        self.assertEqual([row["provider"] for row in result["attempts"]], ["broken", "fake"])
        self.assertEqual(store.query().source.unique().tolist(), ["fake"])

    def test_data_query_refuses_multi_provider_rows_until_source_is_named(self):
        store = MarketDataStore(self.temp / "db")
        bars = FakeProvider().normalize_raw_daily_bars(FakeProvider().fetch_raw_daily_bars("000001.SZ"),
                                                        "000001.SZ", "none")
        for source in ("akshare", "baostock"):
            copy = bars.copy()
            copy["source"] = source
            store.merge_validated(copy)
        with self.assertRaisesRegex(ValueError, "multiple providers"):
            query_market_data(ticker="000001.SZ", market="CN_A", adjustment="none", store=store)
        result = query_market_data(ticker="000001.SZ", market="CN_A", adjustment="none", source="akshare", store=store)
        self.assertEqual(result.source.unique().tolist(), ["akshare"])

    def test_bad_provider_rows_are_archived_and_issue_is_recorded(self):
        class BadVolume(FakeProvider):
            def fetch_raw_daily_bars(self, symbol, start=None, end=None, *, adjustment="none"):
                frame = super().fetch_raw_daily_bars(symbol, start, end, adjustment=adjustment)
                frame.loc[0, "volume"] = -1
                return frame
        store = MarketDataStore(self.temp / "db")
        result = download_market_data("000001.SZ", start=date(2024, 1, 2), end=date(2024, 1, 3),
                                      adjustment="none", provider=BadVolume(), store=store)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("negative_volume", result["issues"].issue_type.tolist())
        self.assertEqual(len(store.query()), 0)
        self.assertEqual(len(list((self.temp / "db" / "raw").rglob("*.parquet"))), 1)

    def test_provider_reported_nontrading_rows_are_archived_but_not_used_as_bars(self):
        class WithNonTradingRow(FakeProvider):
            def fetch_raw_daily_bars(self, symbol, start=None, end=None, *, adjustment="none"):
                return pd.DataFrame({
                    "trade_date": ["2024-01-02", "2024-01-03"],
                    "open": [10.0, 10.0], "high": [10.5, 10.0],
                    "low": [9.8, 10.0], "close": [10.2, 10.0],
                    "volume": ["10000", ""], "amount": ["100000", ""],
                    "tradestatus": ["1", "0"],
                })

            def normalize_provider_metadata(self, raw, symbol):
                return pd.DataFrame({
                    "trade_date": pd.to_datetime(raw["trade_date"]).dt.date,
                    "metadata_json": [json.dumps({"tradestatus": value}) for value in raw["tradestatus"]],
                })

        store = MarketDataStore(self.temp / "nontrading-db")
        provider = WithNonTradingRow()
        result = download_market_data("000001.SZ", start=date(2024, 1, 2), end=date(2024, 1, 3),
                                      adjustment="none", provider=provider, store=store)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["accepted_rows"], 1)
        self.assertEqual(result["excluded_nontrading_rows"], 1)
        self.assertEqual(store.query().trade_date.astype(str).tolist(), ["2024-01-02"])
        self.assertIn("provider_reported_nontrading", store.validation_issues("000001.SZ").issue_type.tolist())
        self.assertEqual(len(store.query_provider_metadata(provider="fake", symbol="000001.SZ")), 2)
        raw = pd.read_parquet(next((self.temp / "nontrading-db" / "raw").rglob("*.parquet")))
        self.assertEqual(len(raw), 2)
        repeated = download_market_data("000001.SZ", start=date(2024, 1, 2), end=date(2024, 1, 3),
                                        adjustment="none", provider=provider, store=store)
        self.assertEqual(repeated["status"], "skipped")

    def test_other_adjustment_metadata_cannot_certify_requested_partition(self):
        store = MarketDataStore(self.temp / "adjustment-metadata-db")
        raw = pd.DataFrame({"trade_date": ["2024-01-02"], "open": [10], "high": [10.5],
            "low": [9.8], "close": [10.2], "volume": [10000], "amount": [100000]})
        from quantlab.data.schema import normalize_daily_bars
        qfq = normalize_daily_bars(raw, market="CN_A", exchange="SZSE", symbol="000001.SZ",
            adjustment="qfq", currency="CNY", source="fake", provider_version="1", source_interface="fixture")
        qfq["ingest_id"] = "qfq-fixture"
        qfq["downloaded_at"] = pd.Timestamp("2024-01-04", tz="UTC")
        metadata = pd.DataFrame({"provider": ["fake"], "symbol": ["000001.SZ"],
            "trade_date": [pd.Timestamp("2024-01-03").date()], "provider_version": ["1"],
            "source_interface": ["fixture"], "metadata_json": ['{"tradestatus":"0"}'],
            "downloaded_at": [pd.Timestamp("2024-01-04", tz="UTC")], "ingest_id": ["qfq-fixture"]})
        store.merge_validated(qfq, metadata)
        result = download_market_data("000001.SZ", start=date(2024, 1, 2), end=date(2024, 1, 3),
                                      adjustment="none", provider=FakeProvider(), store=store)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(store.query(symbol="000001.SZ", adjustment="none", source="fake")), 2)

    def test_backfill_checkpoint_resumes_next_symbol_without_repeating_completed(self):
        config = self.temp / "sources.json"
        config.write_text(json.dumps({"download": {"providers": ["fake"]},
            "historical_backfill": {"providers": ["fake"], "provider_interval_seconds": {"fake": 0}, "batch_size": 1}}))
        universe = self.temp / "universe.json"
        universe.write_text(json.dumps({"pool_id": "test", "members": [
            {"symbol": "000001.SZ", "exchange": "SZSE"}, {"symbol": "600519.SH", "exchange": "SSE"}]}))
        store = MarketDataStore(self.temp / "db")
        router = ProviderRouter(config_path=config, factories={"fake": FakeProvider}, sleeper=lambda _: None)
        first = backfill_market_data(universe=universe, start=date(2024, 1, 2), end=date(2024, 1, 3),
                                     router=router, store=store, limit=1)
        self.assertEqual(first["processed_this_run"], 1)
        second = backfill_market_data(universe=universe, start=date(2024, 1, 2), end=date(2024, 1, 3),
                                      router=router, store=store, limit=1)
        self.assertEqual(second["processed_this_run"], 1)
        self.assertEqual(set(store.query()["symbol"]), {"000001.SZ", "600519.SH"})
        third = backfill_market_data(universe=universe, start=date(2024, 1, 2), end=date(2024, 1, 3),
                                     router=router, store=store, limit=1)
        self.assertEqual(third["processed_this_run"], 0)
        self.assertEqual(third["status"], "complete")

    def test_backfill_pilot_preserves_frozen_universe_order(self):
        config = self.temp / "ordered-sources.json"
        config.write_text(json.dumps({"download": {"providers": ["fake"]},
            "historical_backfill": {"providers": ["fake"], "provider_interval_seconds": {"fake": 0}}}))
        universe = self.temp / "ordered-universe.json"
        universe.write_text(json.dumps({"pool_id": "ordered", "members": [
            {"symbol": "600519.SH", "exchange": "SSE"},
            {"symbol": "000001.SZ", "exchange": "SZSE"},
        ]}))
        store = MarketDataStore(self.temp / "ordered-db")
        router = ProviderRouter(config_path=config, factories={"fake": FakeProvider}, sleeper=lambda _: None)
        result = backfill_market_data(universe=universe, start=date(2024, 1, 2), end=date(2024, 1, 3),
                                      router=router, store=store, limit=1)
        self.assertEqual(result["outcomes"][0]["symbol"], "600519.SH")

    def test_backfill_continues_after_failure_and_retries_only_failed_symbol(self):
        class FailOnce(FakeProvider):
            attempts = {}
            def fetch_raw_daily_bars(self, symbol, start=None, end=None, *, adjustment="none"):
                seen = self.attempts.get(symbol, 0)
                self.attempts[symbol] = seen + 1
                if symbol == "000001.SZ" and seen == 0:
                    raise ConnectionError("fixture network failure")
                return super().fetch_raw_daily_bars(symbol, start, end, adjustment=adjustment)
        FailOnce.attempts = {}
        config = self.temp / "retry-sources.json"
        config.write_text(json.dumps({"download": {"providers": ["fake"]},
            "historical_backfill": {"providers": ["fake"], "provider_interval_seconds": {"fake": 0}, "batch_size": 2}}))
        universe = self.temp / "retry-universe.json"
        universe.write_text(json.dumps({"pool_id": "retry", "members": [
            {"symbol": "000001.SZ", "exchange": "SZSE"}, {"symbol": "600519.SH", "exchange": "SSE"}]}))
        store = MarketDataStore(self.temp / "retry-db")
        router = ProviderRouter(config_path=config, factories={"fake": FailOnce}, sleeper=lambda _: None)
        first = backfill_market_data(universe=universe, start=date(2024, 1, 2), end=date(2024, 1, 3),
                                     router=router, store=store, limit=2)
        first_status = {row["symbol"]: row["status"] for row in first["items"]}
        self.assertEqual(first_status["000001.SZ"], "failed")
        self.assertEqual(first_status["600519.SH"], "completed")
        retry = backfill_market_data(universe=universe, start=date(2024, 1, 2), end=date(2024, 1, 3),
                                     router=router, store=store, limit=2, retry_failed_only=True)
        self.assertEqual(retry["processed_this_run"], 1)
        self.assertEqual(retry["status"], "complete")
        self.assertEqual(FailOnce.attempts, {"000001.SZ": 2, "600519.SH": 1})

    def test_provider_reconciliation_compares_without_writing_rows(self):
        class DifferentProvider(_NamedProvider):
            def fetch_raw_daily_bars(self, symbol, start=None, end=None, *, adjustment="none"):
                frame = super().fetch_raw_daily_bars(symbol, start, end, adjustment=adjustment).iloc[:1].copy()
                frame.loc[frame.index[0], "close"] = 10.25
                return frame
        config = self.temp / "sources.json"
        config.write_text(json.dumps({"download": {"providers": ["a", "b"]}}))
        router = ProviderRouter(config_path=config, factories={"a": lambda: _NamedProvider("a"),
                                                               "b": lambda: DifferentProvider("b")})
        report = reconcile_providers("000001.SZ", provider_a="a", provider_b="b",
            start=date(2024, 1, 2), end=date(2024, 1, 3), router=router, output_dir=self.temp)
        self.assertEqual(report["common_dates"], 1)
        self.assertEqual(report["row_counts"], {"a": 2, "b": 1})
        self.assertEqual(report["only_in_a_dates"], ["2024-01-03"])
        self.assertGreater(report["field_differences"]["close"]["max_absolute_difference"], 0)
        self.assertTrue(Path(report["report_path"]).exists())
        self.assertIn("not selected", report["interpretation"])

    def test_reconciliation_writes_blocked_report_on_provider_network_failure(self):
        class UnavailableAtRequest(_NamedProvider):
            def fetch_raw_daily_bars(self, *args, **kwargs):
                raise ConnectionError("proxy rejected connection")
        config = self.temp / "blocked-sources.json"
        config.write_text(json.dumps({"download": {"providers": ["a", "b"]}}))
        router = ProviderRouter(config_path=config, factories={"a": lambda: _NamedProvider("a"),
                                                               "b": lambda: UnavailableAtRequest("b")})
        report = reconcile_providers("000001.SZ", provider_a="a", provider_b="b",
            start=date(2024, 1, 2), end=date(2024, 1, 3), router=router, output_dir=self.temp)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("proxy rejected connection", report["provider_errors"]["b"])
        self.assertTrue(Path(report["report_path"]).exists())


class _NamedProvider(FakeProvider):
    def __init__(self, name):
        self.name = name


if __name__ == "__main__":
    unittest.main()
