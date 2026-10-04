from __future__ import annotations

import shutil
import unittest
from pathlib import Path

import pandas as pd

from quantlab.data.schema import normalize_daily_bars
from quantlab.data.store import MarketDataStore
from quantlab.data.symbols import normalize_ashare_symbol
from quantlab.research.price_basis import compare_price_bases


class PriceBasisDiagnosticsTests(unittest.TestCase):
    def test_none_qfq_comparison_reports_ratios_and_mechanical_jumps(self):
        root = Path(__file__).parent / "_testdata" / self._testMethodName
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        try:
            store = MarketDataStore(root / "data")
            identity = normalize_ashare_symbol("000001.SZ")
            dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
            for adjustment, close in (("none", [10., 10.5, 13.]), ("qfq", [5., 5.25, 6.5])):
                raw = pd.DataFrame({"trade_date": dates, "open": close, "high": [x + .1 for x in close],
                                    "low": [x - .1 for x in close], "close": close,
                                    "volume": [100., 110., 120.], "amount": [1000., 1155., 1560.]})
                bars = normalize_daily_bars(raw, market="CN_A", exchange=identity.exchange,
                    symbol=identity.symbol, adjustment=adjustment, currency="CNY", source="baostock",
                    provider_version="fixture", source_interface="fixture")
                store.merge_validated(bars)
            report = compare_price_bases([identity.symbol], provider="baostock", start="2024-01-01",
                                         end="2024-01-31", jump_threshold=.20, root=root)
            symbol = report["symbols"][identity.symbol]
            self.assertEqual(symbol["common_dates"], 3)
            self.assertAlmostEqual(symbol["close_qfq_to_none_ratio"]["median"], .5)
            self.assertAlmostEqual(symbol["ohlc_qfq_to_none_ratio"]["open"]["median"], .5)
            self.assertEqual(symbol["volume_comparison"]["different_rows"], 0)
            self.assertEqual(len(symbol["mechanical_jump_diagnostics"]["none"]), 1)
            self.assertIn("cause not inferred", symbol["mechanical_jump_diagnostics"]["none"][0]["diagnostic"])
            self.assertTrue(Path(report["report_path"]).exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
