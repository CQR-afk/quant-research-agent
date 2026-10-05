import unittest
from pathlib import Path

from quantlab.research.historical_universe_coverage import (
    CoverageEvidenceError,
    compare_pilot_with_current,
    date_coverage_report,
    deterministic_pilot,
    listing_interval_status,
    normalize_delisted,
    normalize_symbol,
    tushare_credential_status,
)


def record(symbol="000001.SZ", exchange="SZSE", **changes):
    base = {
        "symbol": symbol,
        "exchange": exchange,
        "security_type": "A_share_main_board",
        "listing_date": "2000-01-01",
        "delisting_date": "2020-01-01",
        "source_ids": ["exchange_notice", "provider_basic"],
    }
    base.update(changes)
    return base


class HistoricalUniverseCoverageTests(unittest.TestCase):
    def test_normalization_preserves_leading_zeroes(self):
        self.assertEqual(normalize_symbol("sz.000001"), ("000001.SZ", "SZSE"))

    def test_exchange_mapping_rejects_mismatch(self):
        with self.assertRaises(CoverageEvidenceError):
            normalize_symbol("sh.000001")

    def test_delisted_normalization_keeps_interval_and_unknown_trade_status(self):
        result = normalize_delisted(record())
        self.assertEqual(result["listing_interval_status"], "bounded_interval_evidence")
        self.assertFalse(result["trading_status_known"])

    def test_listing_interval_is_not_tradability(self):
        row = normalize_delisted(record())
        self.assertEqual(listing_interval_status(row, "2018-12-28"), "member_of_listing_interval")
        self.assertFalse(row["trading_status_known"])

    def test_missing_delisting_date_remains_unknown(self):
        row = normalize_delisted(record(delisting_date=None))
        self.assertEqual(row["listing_interval_status"], "open_end_unknown_historical_status")
        self.assertEqual(listing_interval_status(row, "2018-12-28"), "unknown")

    def test_multi_source_provenance_is_required(self):
        with self.assertRaises(CoverageEvidenceError):
            normalize_delisted(record(source_ids=[]))
        with self.assertRaises(CoverageEvidenceError):
            normalize_delisted(record(source_ids=["same", "same"]))

    def test_deterministic_historical_pilot_order(self):
        rows = [record("300001.SZ", "SZSE"), record("600001.SH", "SSE"),
                record("002618.SZ", "SZSE")]
        one = [row["symbol"] for row in deterministic_pilot(rows)]
        two = [row["symbol"] for row in deterministic_pilot(list(reversed(rows)))]
        self.assertEqual(one, ["600001.SH", "002618.SZ", "300001.SZ"])
        self.assertEqual(one, two)

    def test_duplicate_security_is_rejected(self):
        with self.assertRaises(CoverageEvidenceError):
            deterministic_pilot([record("600001.SH", "SSE"), record("600001.SH", "SSE")])

    def test_current_vs_historical_roster_comparison_scope(self):
        result = compare_pilot_with_current({"600001.SH", "000001.SZ"}, {"000001.SZ", "600002.SH"})
        self.assertEqual(result["historical_only_symbols"], ["600001.SH"])
        self.assertEqual(result["overlap_symbols"], ["000001.SZ"])
        self.assertEqual(result["unknown_historical_population_count"], None)

    def test_unavailable_daily_source_coverage_is_unknown(self):
        result = date_coverage_report()[0]
        self.assertEqual(result["status"], "daily_roster_unavailable")
        self.assertIsNone(result["total"])
        self.assertEqual(result["source_coverage"], "not_measured")

    def test_tushare_credential_is_not_assumed(self):
        self.assertEqual(tushare_credential_status(Path.cwd(), environ={}), "credential_unavailable")

    def test_report_scope_excludes_legacy_and_holdout_reads(self):
        import inspect
        from quantlab.research import historical_universe_coverage as module
        source = inspect.getsource(module.build_report)
        self.assertNotIn("LEGACY_OBSERVED_TEST", source)
        self.assertNotIn("SEALED_HOLDOUT", source)
        self.assertNotIn("artifacts/experiments", source)


if __name__ == "__main__":
    unittest.main()
