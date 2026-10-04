from __future__ import annotations

import unittest

import pandas as pd
from unittest.mock import patch

from quantlab.data.schema import DAILY_BAR_COLUMNS, missing_expected_sessions, normalize_daily_bars
from quantlab.providers.yfinance import YFinanceProvider


def sample_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": ["2026-01-05", "2026-01-07"],
            "open": [10.0, 10.2],
            "high": [10.5, 10.7],
            "low": [9.8, 10.0],
            "close": [10.2, 10.4],
            "volume": [1000, 1200],
            "amount": [10100, 12480],
        }
    )


class CanonicalSchemaTests(unittest.TestCase):
    def test_schema_preserves_leading_zero_symbol_and_metadata(self) -> None:
        normalized = normalize_daily_bars(
            sample_bars(),
            market="CN_A",
            exchange="SZSE",
            symbol="000001",
            adjustment="qfq",
            currency="CNY",
            source="mock-provider",
        )
        self.assertEqual(tuple(normalized.columns), DAILY_BAR_COLUMNS)
        self.assertEqual(normalized["symbol"].iloc[0], "000001")
        self.assertEqual(normalized["volume"].iloc[0], 1000)
        self.assertEqual(normalized["currency"].iloc[0], "CNY")
        self.assertEqual(str(normalized["downloaded_at"].dt.tz), "UTC")

    def test_invalid_ohlc_is_rejected(self) -> None:
        bars = sample_bars()
        bars.loc[0, "high"] = 9.0
        with self.assertRaisesRegex(ValueError, "OHLC values are inconsistent"):
            normalize_daily_bars(
                bars,
                market="CN_A",
                exchange="SZSE",
                symbol="000001",
                adjustment="qfq",
                currency="CNY",
                source="mock-provider",
            )

    def test_missing_sessions_are_reported_without_filling(self) -> None:
        normalized = normalize_daily_bars(
            sample_bars(),
            market="CN_A",
            exchange="SZSE",
            symbol="000001",
            adjustment="qfq",
            currency="CNY",
            source="mock-provider",
        )
        expected = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        self.assertEqual(missing_expected_sessions(normalized, expected), [pd.Timestamp("2026-01-06")])
        self.assertEqual(len(normalized), 2)

    def test_yfinance_adapter_maps_a_share_schema_without_network(self) -> None:
        raw = sample_bars().drop(columns="amount").set_index("trade_date")
        raw.index = pd.to_datetime(raw.index)
        raw.columns = pd.MultiIndex.from_product([raw.columns, ["000001.SZ"]])
        with patch("quantlab.providers.yfinance.yf.download", return_value=raw) as download:
            bars = YFinanceProvider().fetch_daily_bars("000001.SZ", adjustment="vendor_adjusted")

        self.assertEqual(tuple(bars.columns), DAILY_BAR_COLUMNS)
        self.assertEqual(bars["market"].iloc[0], "CN_A")
        self.assertEqual(bars["exchange"].iloc[0], "SZSE")
        self.assertEqual(bars["symbol"].iloc[0], "000001.SZ")
        self.assertEqual(bars["adjustment"].iloc[0], "vendor_adjusted")
        self.assertTrue(pd.isna(bars["amount"].iloc[0]))
        self.assertTrue(download.call_args.kwargs["auto_adjust"])


if __name__ == "__main__":
    unittest.main()
