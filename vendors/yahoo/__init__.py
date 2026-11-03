"""Yahoo Finance via ``yfinance`` (scraper ported from PocketQuant2)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd

from vendors.core import Vendor, dataset


def _tickers(value: Sequence[str] | str) -> list[str]:
    if isinstance(value, str):
        return [s.strip().upper() for s in value.split(",") if s.strip()]
    return [t.upper() for t in value]


class YahooVendor(Vendor):
    name = "yahoo"
    description = "Yahoo Finance equities/ETF prices and option chains (yfinance)"

    @dataset(key=["ticker", "date", "interval"])
    def prices(
        self,
        tickers: Sequence[str] | str = "SPY",
        start: str | None = None,
        end: str | None = None,
        interval: str = "1d",
        adjusted: bool = False,
        fill_missing: bool = True,
    ) -> pd.DataFrame:
        """OHLCV bars for one or more tickers (comma-separated string or list)."""
        from .scraper import StockScraper

        s = StockScraper(_tickers(tickers), start, end, interval, adjusted, fill_missing)
        s.fetch_data()
        s.clean_data()
        frames = [df.assign(TICKER=t) for t, df in s.data.items()]
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        return df[["TICKER"] + [c for c in df.columns if c != "TICKER"]]

    @dataset(key=["contract_symbol", "snapshot_date"])
    def options(self, tickers: Sequence[str] | str = "SPY", max_days: int = 60) -> pd.DataFrame:
        """Option-chain snapshot (calls + puts) for expirations within ``max_days``."""
        from .scraper import StockScraper

        s = StockScraper(_tickers(tickers), None, None)
        s.fetch_options(max_days=max_days)
        snap = date.today().isoformat()
        frames = [
            df.assign(ticker=t, expiration=exp, option_type=kind.rstrip("s"), snapshot_date=snap)
            for t, chains in s.options_data.items()
            for exp, sides in chains.items()
            for kind, df in sides.items()
        ]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
