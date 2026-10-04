from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantlab.features import FEATURE_COLUMNS, make_feature_frame, make_forward_target
from quantlab.modeling import train_and_evaluate


def synthetic_prices(rows: int = 360) -> pd.DataFrame:
    index = pd.bdate_range("2023-01-02", periods=rows)
    steps = np.where(np.arange(rows) % 4 < 2, 0.008, -0.006)
    close = 100 * np.cumprod(1 + steps)
    volume = 1_000_000 + np.arange(rows) * 113
    return pd.DataFrame({"Close": close, "Volume": volume}, index=index)


class ForwardTargetTests(unittest.TestCase):
    def test_five_observation_target_saves_its_end_date(self) -> None:
        prices = synthetic_prices(8)
        target = make_forward_target(prices, horizon=5)
        expected = prices["Close"].iloc[5] / prices["Close"].iloc[0] - 1
        self.assertAlmostEqual(target["target_5d"].iloc[0], expected)
        self.assertEqual(target["label_end_date"].iloc[0], prices.index[5])
        self.assertTrue(target["target_5d"].tail(5).isna().all())

    def test_future_price_changes_do_not_change_past_features(self) -> None:
        prices = synthetic_prices(100)
        cutoff = prices.index[70]
        original = make_feature_frame(prices)
        changed = prices.copy()
        changed.loc[changed.index > cutoff, "Close"] *= 2
        revised = make_feature_frame(changed)
        pd.testing.assert_frame_equal(
            original.loc[:cutoff, FEATURE_COLUMNS], revised.loc[:cutoff, FEATURE_COLUMNS]
        )

    def test_forward_target_rejects_multiple_symbols(self) -> None:
        prices = synthetic_prices(10)
        prices["symbol"] = ["000001.SZ", "000002.SZ"] * 5
        with self.assertRaisesRegex(ValueError, "separately for each symbol"):
            make_forward_target(prices, horizon=5)


class ChronologicalModelTests(unittest.TestCase):
    def test_training_labels_are_purged_before_holdout(self) -> None:
        result = train_and_evaluate(synthetic_prices(), ticker="TEST")
        self.assertGreaterEqual(result.metrics["purged_boundary_rows"], 1)
        self.assertLess(result.metrics["train_label_end"], result.metrics["holdout_start"])
        self.assertIn("label_end_date", result.holdout_predictions.columns)
        self.assertTrue(0 <= result.next_session_probability_up <= 1)


if __name__ == "__main__":
    unittest.main()
