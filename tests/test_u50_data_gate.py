from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantlab.data.schema import normalize_daily_bars
from quantlab.data.store import MarketDataStore
from quantlab.research.calendar import AShareTradingCalendar
from quantlab.research.dataset import build_research_dataset
from quantlab.research.features import FEATURE_VERSION, feature_spec
from quantlab.research.u50_breadth import evaluate_u50_breadth


class U50DataGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(__file__).parent / "_testdata" / "u50" / self._testMethodName
        self.temp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_additions_preserve_frozen_u50_order_and_u11_anchors(self):
        root = Path(__file__).resolve().parents[1]
        u50 = json.loads((root / "config/ashare_universe_breadth_50.json").read_text(encoding="utf-8"))
        additions = json.loads((root / "config/ashare_universe_breadth_50_additions_2018_2023_v1.json").read_text(encoding="utf-8"))
        anchors = json.loads((root / "config/ashare_universe_qfq_pilot.json").read_text(encoding="utf-8"))
        anchor_symbols = {item["symbol"] for item in anchors["members"]}
        expected = [item for item in u50["members"] if item["symbol"] not in anchor_symbols]
        self.assertEqual(u50["universe_hash"], "d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218")
        self.assertEqual(len(u50["members"]), 50)
        self.assertTrue(anchor_symbols.issubset({item["symbol"] for item in u50["members"]}))
        self.assertEqual(additions["parent_universe_hash"], u50["universe_hash"])
        self.assertEqual(additions["members"], expected)

    def test_dataset_uses_listing_date_and_excludes_nontrading_or_unknown_status(self):
        start, end = "2018-01-01", "2018-06-29"
        calendar = AShareTradingCalendar(start, end)
        listing = pd.Timestamp("2018-03-01")
        sessions = calendar.sessions_between(listing, end)
        missing_day, unknown_day, no_trade_day = sessions[8], sessions[13], sessions[18]
        included = sessions.difference(pd.DatetimeIndex([missing_day]))
        close = 10 + np.arange(len(included)) * 0.05
        raw = pd.DataFrame({
            "trade_date": included,
            "open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
            "volume": np.full(len(included), 1000.0), "amount": close * 1000,
        })
        raw.loc[raw.trade_date == no_trade_day, ["volume", "amount"]] = 0
        canonical = normalize_daily_bars(raw, market="CN_A", exchange="SSE", symbol="600001.SH",
            adjustment="qfq", currency="CNY", source="baostock", provider_version="fixture",
            source_interface="fixture")
        canonical["ingest_id"] = "fixture-ingest"
        canonical["downloaded_at"] = pd.Timestamp("2026-01-01", tz="UTC")
        metadata = pd.DataFrame({
            "provider": "baostock", "symbol": "600001.SH", "trade_date": included.date,
            "provider_version": "fixture", "source_interface": "fixture",
            "metadata_json": [json.dumps({"tradestatus": "0" if d == no_trade_day else "1"}) for d in included],
            "downloaded_at": pd.Timestamp("2026-01-01", tz="UTC"), "ingest_id": "fixture-ingest",
        })
        metadata = metadata.loc[metadata.trade_date != unknown_day.date()].copy()
        store = MarketDataStore(self.temp / "data")
        store.merge_validated(canonical, metadata)

        pool_path = self.temp / "pool.json"
        pool_path.write_text(json.dumps({
            "pool_id": "fixture", "selection_date": "2026-01-01", "research_start": start,
            "research_end": end, "survivorship_bias_note": "fixture",
            "members": [{"symbol": "600001.SH", "exchange": "SSE", "listing_date": "2018-03-01"}],
        }), encoding="utf-8")
        config_path = self.temp / "research.json"
        config_path.write_text(json.dumps({"adjustment": "qfq", "provider": "baostock",
            "date_range": {"start": start, "end": end}, "maximum_missing_session_ratio": 0.02}), encoding="utf-8")
        result = build_research_dataset(store=store, pool_path=pool_path, research_config_path=config_path,
            start=start, end=end, adjustment="qfq", provider="baostock",
            output_path=self.temp / "dataset.parquet")
        row = result.coverage.iloc[0]
        self.assertEqual(row.expected_sessions, len(sessions))
        self.assertEqual(row.missing_sessions, 1)
        self.assertEqual(row.missing_session_classification, "unknown_suspension_or_provider_gap")
        self.assertEqual(row.provider_reported_nontrading_sessions, 1)
        self.assertEqual(row.unknown_trade_status_sessions, 1)
        self.assertNotIn(no_trade_day, pd.DatetimeIndex(result.dataset.prediction_date))
        self.assertLessEqual(pd.to_datetime(result.dataset.label_end_date).dropna().max(), pd.Timestamp(end))
        self.assertEqual(result.manifest["feature_version"], FEATURE_VERSION)
        self.assertEqual(result.manifest["target_version"], "target_5d_h5_v1")
        self.assertEqual(len(result.manifest["dataset_sha256"]), 64)

    def test_breadth_metrics_and_development_cap(self):
        start, end = "2022-10-01", "2023-03-31"
        calendar = AShareTradingCalendar(start, end)
        columns, _, _ = feature_spec(FEATURE_VERSION)
        rows = []
        sessions = calendar.sessions
        for symbol_index in range(50):
            symbol = f"{symbol_index:06d}.SZ"
            for index, day in enumerate(sessions):
                if index + 5 >= len(sessions):
                    continue
                row = {column: 0.01 for column in columns}
                row.update({"symbol": symbol, "prediction_date": day,
                    "target_5d": 0.01, "label_end_date": sessions[index + 5]})
                rows.append(row)
        dataset = pd.DataFrame(rows)
        coverage = pd.DataFrame({"symbol": [f"{i:06d}.SZ" for i in range(50)],
                                 "rows": [100] * 50, "missing_ratio": [0.0] * 50})
        report = evaluate_u50_breadth(dataset, coverage, start=start, end=end)
        self.assertEqual(report["status"], "u50_data_ready")
        self.assertEqual(report["symbols_with_valid_labels"], 50)
        self.assertEqual(report["daily_cross_section"]["minimum"], 50)
        self.assertEqual(report["daily_cross_section"]["ratio_dates_at_or_above_90pct_width"], 1.0)
        self.assertEqual(report["holdout_access"], "not accessed; no 2024+ data supplied to this gate")

        with self.assertRaisesRegex(ValueError, "sealed at 2023-12-31"):
            evaluate_u50_breadth(dataset, coverage, start="2023-01-01", end="2024-01-01")


if __name__ == "__main__":
    unittest.main()
