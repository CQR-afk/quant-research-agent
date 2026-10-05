"""Offline governance tests for the Step 4.7 feasibility audit."""

from __future__ import annotations

import ast
import hashlib
import inspect
import unittest
from pathlib import Path
from uuid import uuid4

from quantlab.research import data_foundation as foundation


class DataFoundationTests(unittest.TestCase):
    def test_listing_interval_is_start_inclusive_end_exclusive(self):
        values = [
            ("2011-09-19", "not_listed"),
            ("2011-09-20", "listed_interval_not_trading_status"),
            ("2022-06-21", "listed_interval_not_trading_status"),
            ("2022-06-22", "not_listed"),
        ]
        for day, expected in values:
            with self.subTest(day=day):
                self.assertEqual(
                    foundation.listed_on(listing_date="2011-09-20", delisting_date="2022-06-22",
                                         research_date=day, status_at_snapshot="delisted"),
                    expected,
                )

    def test_empty_outdate_does_not_prove_historical_listing(self):
        args = {"listing_date": "1991-04-03", "delisting_date": None,
                "status_at_snapshot": "current_listed", "snapshot_date": "2026-10-04"}
        self.assertEqual(foundation.listed_on(research_date="2018-12-31", **args), "unknown")
        self.assertEqual(foundation.listed_on(research_date="2026-10-04", **args), "listed_at_snapshot")
        self.assertEqual(foundation.listed_on(research_date="1990-01-01", **args), "not_listed")

    def test_missing_and_invalid_delisting_metadata(self):
        self.assertEqual(foundation.listed_on(listing_date=None, delisting_date=None,
                                              research_date="2018-01-01", status_at_snapshot="unknown"), "unknown")
        with self.assertRaises(foundation.EvidenceError):
            foundation.listed_on(listing_date="2022-06-22", delisting_date="2022-06-22",
                                 research_date="2022-06-22", status_at_snapshot="delisted")

    def test_deterministic_pilot_selection_ignores_input_order(self):
        rows = [
            {"symbol": "000002.SZ", "selection_class": "current_official_snapshot", "source_ids": ["a"]},
            {"symbol": "000001.SZ", "selection_class": "current_official_snapshot", "source_ids": ["b"]},
            {"symbol": "300001.SZ", "selection_class": "official_documented_delisted", "source_ids": ["c"]},
            {"symbol": "002618.SZ", "selection_class": "official_documented_delisted", "source_ids": ["d"]},
        ]
        expected = ["000001.SZ", "002618.SZ"]
        self.assertEqual([row["symbol"] for row in foundation.deterministic_pilot(rows)], expected)
        self.assertEqual([row["symbol"] for row in foundation.deterministic_pilot(list(reversed(rows)))], expected)

    def test_duplicate_or_unproven_pilot_candidate_rejected(self):
        rows = [
            {"symbol": "000001.SZ", "selection_class": "current_official_snapshot", "source_ids": ["a"]},
            {"symbol": "000001.SZ", "selection_class": "official_documented_delisted", "source_ids": ["b"]},
        ]
        with self.assertRaises(foundation.EvidenceError):
            foundation.deterministic_pilot(rows)
        rows[1]["symbol"] = "002618.SZ"
        rows[1]["source_ids"] = []
        with self.assertRaises(foundation.EvidenceError):
            foundation.deterministic_pilot(rows)

    def test_official_source_requires_hash_and_expected_content(self):
        path = Path(__file__).resolve().parents[1] / "data" / f"evidence_{uuid4().hex}.pdf"
        try:
            path.write_bytes(b"%PDF-1.5\nsmall offline fixture")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            foundation.verify_source(path, expected_sha256=digest, content_type="application/pdf")
            with self.assertRaises(foundation.EvidenceError):
                foundation.verify_source(path, expected_sha256="0" * 64, content_type="application/pdf")
            path.write_bytes(b"<html>access challenge</html>")
            with self.assertRaises(foundation.EvidenceError):
                foundation.verify_source(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                         content_type="application/pdf")
            with self.assertRaises(foundation.EvidenceError):
                foundation.verify_source(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                         content_type="xlsx")
        finally:
            path.unlink(missing_ok=True)

    def test_historical_trade_date_is_not_a_factor_version(self):
        self.assertEqual(
            foundation.factor_version_class(trade_date="2018-01-01", observed_at="2026-10-05T00:00:00+00:00"),
            "historical_factor_snapshot",
        )

    def test_as_of_archive_is_only_a_candidate_until_methodology_verified(self):
        self.assertEqual(
            foundation.factor_version_class(trade_date="2018-01-01", observed_at="2026-10-05T00:00:00+00:00",
                                            as_of_archive="2018-01-02T09:00:00+08:00"),
            "as_of_archive_candidate_requires_methodology_validation",
        )
        with self.assertRaises(foundation.EvidenceError):
            foundation.factor_version_class(trade_date="2018-01-01", observed_at="2026-10-05T00:00:00+00:00",
                                            as_of_archive="2018-01-02T09:00:00")

    def test_audit_has_no_training_or_holdout_read_path(self):
        tree = ast.parse(inspect.getsource(foundation))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for forbidden in ("fit", "LGBMRegressor", "read_csv", "fetch_raw_daily_bars", "read_market_bars"):
            self.assertNotIn(forbidden, names | attrs)
        self.assertEqual(foundation.ROLE, "DATA_FOUNDATION_FEASIBILITY")
        self.assertIn("data_foundation_feasibility", str(foundation.REPORT_PATH))

    def test_report_reproducibility_ignores_only_generation_time(self):
        first = {"created_at_utc": "2026-10-05T00:00:00+00:00", "decision_matrix": {"combined": "partial"}}
        later = {"created_at_utc": "2026-10-05T00:01:00+00:00", "decision_matrix": {"combined": "partial"}}
        self.assertEqual(foundation.stable_payload(first), foundation.stable_payload(later))
        later["decision_matrix"]["combined"] = "feasible"
        self.assertNotEqual(foundation.stable_payload(first), foundation.stable_payload(later))


if __name__ == "__main__":
    unittest.main()
