"""Download and normalize daily market data."""

from __future__ import annotations

import pandas as pd

from quantlab.providers.yfinance import YFinanceProvider


def download_daily_prices(ticker: str, period: str = "5y") -> pd.DataFrame:
    """Return adjusted OHLCV for the legacy Logistic Regression command."""
    bars = YFinanceProvider(period=period).fetch_daily_bars(ticker, adjustment="vendor_adjusted")
    frame = bars.set_index("trade_date").loc[:, ["open", "high", "low", "close", "volume"]]
    frame.columns = [name.title() for name in frame.columns]
    frame.index.name = "Date"
    return frame

