from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import pandas as pd

from quantlab.research.features import FEATURE_VERSION, feature_spec
from quantlab.research.u100_breadth import evaluate_u100_breadth


class U100DataGateTests(unittest.TestCase):
    def test_frozen_nested_universes_and_additions_order(self):
        root = Path(__file__).resolve().parents[1]
        u100 = json.loads((root / "config/ashare_universe_breadth_100.json").read_text(encoding="utf-8"))
        u50 = json.loads((root / "config/ashare_universe_breadth_50.json").read_text(encoding="utf-8"))
        task = json.loads((root / "config/ashare_universe_breadth_100_additions_2018_2023_v1.json").read_text(encoding="utf-8"))
        s100 = [item["symbol"] for item in u100["members"]]
        s50 = [item["symbol"] for item in u50["members"]]
        self.assertEqual(hashlib.sha256("\n".join(s100).encode()).hexdigest(),
                         "05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9")
        self.assertEqual(hashlib.sha256("\n".join(s50).encode()).hexdigest(),
                         "d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218")
        self.assertEqual(len(s100), 100)
        self.assertEqual(len(set(s100)), 100)
        self.assertEqual({exchange: sum(item["exchange"] == exchange for item in u100["members"])
                          for exchange in ("SSE", "SZSE")}, {"SSE": 50, "SZSE": 50})
        self.assertTrue(set(s50).issubset(s100))
        self.assertEqual(u100["selection_seed"], u50["selection_seed"])
        self.assertEqual(u100["candidate_source_hash"], u50["candidate_source_hash"])
        self.assertEqual(u100["selection_order"], u50["selection_order"])
        expected = [symbol for symbol in s100 if symbol not in set(s50)]
        self.assertEqual(task["addition_symbols"], expected)
        self.assertEqual(task["frozen_candidate_order"], s100)
        self.assertEqual(task["parent_u100_hash"], u100["universe_hash"])
        self.assertEqual(task["reused_u50_hash"], u50["universe_hash"])
        self.assertTrue(task["mixed_vendor_snapshot_times"])

    def test_u100_fixed_daily_breadth_thresholds_and_metrics(self):
        columns, _, _ = feature_spec(FEATURE_VERSION)
        dates = pd.date_range("2023-10-02", periods=10, freq="B")
        rows = []
        for i in range(100):
            for day in dates:
                row = {column: 0.1 for column in columns}
                row.update({"symbol": f"{i:06d}.SZ", "prediction_date": day,
                            "target_5d": 0.01, "label_end_date": day + pd.Timedelta(days=1)})
                rows.append(row)
        frame = pd.DataFrame(rows)
        coverage = pd.DataFrame({"symbol": [f"{i:06d}.SZ" for i in range(100)], "rows": 100})
        report = evaluate_u100_breadth(frame, coverage, start="2023-10-01", end="2023-12-31")
        self.assertEqual(report["status"], "u100_data_ready")
        self.assertEqual(report["daily_cross_section"]["threshold_80pct_symbols"], 80)
        self.assertEqual(report["daily_cross_section"]["threshold_90pct_symbols"], 90)
        self.assertEqual(report["daily_cross_section"]["minimum"], 100)
        self.assertEqual(report["daily_cross_section"]["p10"], 100)
        self.assertTrue(all(report["checks"].values()))
        with self.assertRaisesRegex(ValueError, "scoped to 2018-01-01 through 2023-12-31"):
            evaluate_u100_breadth(frame, coverage, start="2023-01-01", end="2024-01-01")
        frame.loc[0, "label_end_date"] = pd.Timestamp("2024-01-02")
        with self.assertRaisesRegex(ValueError, "label end date exceeds"):
            evaluate_u100_breadth(frame, coverage)
        frame.loc[0, "label_end_date"] = pd.Timestamp("2023-10-03")
        frame.loc[0, "prediction_date"] = pd.Timestamp("2024-01-02")
        with self.assertRaisesRegex(ValueError, "prediction date exceeds"):
            evaluate_u100_breadth(frame, coverage)


if __name__ == "__main__":
    unittest.main()
