from __future__ import annotations

import unittest

import numpy as np
import pandas as pd


class Python314ResearchStackTests(unittest.TestCase):
    def test_numpy_pandas_and_sklearn_fit_offline(self) -> None:
        from sklearn.linear_model import LogisticRegression

        values = np.array([[0.0], [1.0], [2.0], [3.0]])
        labels = np.array([0, 0, 1, 1])
        estimator = LogisticRegression().fit(values, labels)
        self.assertEqual(estimator.predict([[2.5]]).tolist(), [1])
        self.assertEqual(pd.Series(values[:, 0]).sum(), 6.0)

    def test_lightgbm_regressor_fits_offline(self) -> None:
        from lightgbm import LGBMRegressor

        model = LGBMRegressor(n_estimators=4, verbosity=-1, n_jobs=1).fit([[0], [1], [2], [3]], [0.0, 0.2, 0.4, 0.6])
        self.assertEqual(model.predict([[1.5]]).shape, (1,))

    def test_duckdb_and_pyarrow_round_trip_offline(self) -> None:
        import duckdb
        import pyarrow as pa
        import pyarrow.parquet as pq

        self.assertEqual(duckdb.sql("select 6 * 7").fetchone(), (42,))
        table = pa.table({"symbol": ["000001.SZ"], "close": [10.25]})
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        restored = pq.read_table(pa.BufferReader(sink.getvalue()))
        self.assertEqual(restored.column("symbol").to_pylist(), ["000001.SZ"])

    def test_numba_and_vectorbt_execute_offline(self) -> None:
        import numba
        import vectorbt as vbt

        @numba.njit
        def add_one(value: int) -> int:
            return value + 1

        self.assertEqual(add_one(41), 42)
        prices = pd.Series([10.0, 11.0, 12.0], index=pd.bdate_range("2026-01-01", periods=3))
        portfolio = vbt.Portfolio.from_holding(prices)
        self.assertTrue(np.isfinite(float(portfolio.total_return())))

    def test_openai_responses_mock_offline(self) -> None:
        from openai import OpenAI

        with OpenAI(api_key="mock-test-key") as client:
            self.assertTrue(callable(client.responses.create))


if __name__ == "__main__":
    unittest.main()
