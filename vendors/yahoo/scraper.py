"""Yahoo Finance scraper, ported from PocketQuant2 (scraper/api_clients/YFinance.py).

Changes from the original:
  * no dependency on PocketQuant2's ``configs.paths`` / ``sys.path`` hacks;
  * no global ``logging.basicConfig`` (libraries shouldn't configure logging);
  * ``save_data``/``save_options`` dropped -- persistence is handled by
    ``VendorStorage`` (data/yahoo/yahoo.db) like every other vendor;
  * ``fetch_options`` takes ``max_days`` instead of a hard-coded 365.
The fetch/retry/clean logic is otherwise unchanged.
"""

from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


class StockScraper:
    def __init__(
        self,
        tickers: list[str],
        start_date: str | None,
        end_date: str | None,
        interval: str = "1d",
        adjusted: bool = False,
        fill_missing: bool = True,
    ):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.interval = interval
        self.adjusted = adjusted
        self.fill_missing = fill_missing
        self.data: dict[str, pd.DataFrame] = {}
        self.options_data: dict[str, dict[str, dict[str, pd.DataFrame]]] = {}

    def fetch_data(self, retries: int = 3) -> None:
        for ticker in self.tickers:
            backoff = 1
            for attempt in range(retries):
                try:
                    log.info("Fetching data for %s", ticker)
                    df = yf.download(
                        tickers=ticker,
                        start=self.start_date,
                        end=self.end_date,
                        interval=self.interval,
                        progress=False,
                        auto_adjust=self.adjusted,
                        threads=True,
                    )
                    if df is None or df.empty:
                        log.warning("No data found for %s, skipping.", ticker)
                        break

                    df.index.name = "Date"
                    df = df.reset_index()
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df = df.rename(columns=lambda x: x.upper())
                    if "DATETIME" in df.columns and "DATE" not in df.columns:  # intraday intervals
                        df = df.rename(columns={"DATETIME": "DATE"})
                    required_cols = ["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
                    if "ADJ CLOSE" in df.columns:
                        required_cols.append("ADJ CLOSE")
                    for col in required_cols:
                        if col not in df.columns:
                            df[col] = pd.NA
                    self.data[ticker] = df[required_cols].copy()
                    break
                except Exception as e:  # yfinance raises a zoo of exception types
                    log.error("Error fetching %s: %s", ticker, e)
                    if attempt < retries - 1:
                        time.sleep(backoff)
                        backoff *= 2
            else:
                log.warning("Failed to fetch data for %s after %d retries.", ticker, retries)

    def clean_data(self) -> None:
        for ticker, df in self.data.items():
            df = df.drop_duplicates()
            df["DATE"] = pd.to_datetime(df["DATE"]).dt.tz_localize(None)
            if df["DATE"].isna().any():
                log.warning("Dropping %d rows with missing DATE for %s", df["DATE"].isna().sum(), ticker)
                df = df.dropna(subset=["DATE"])
            df = df.set_index("DATE")
            if self.fill_missing:
                df = df.interpolate(method="time").bfill()
            df = df.reset_index()
            df["INTERVAL"] = self.interval
            self.data[ticker] = df

    def fetch_options(self, max_days: int = 365) -> None:
        """Fills ``self.options_data = {ticker: {expiration: {'calls': df, 'puts': df}}}``."""
        self.options_data = {}
        today = pd.Timestamp.now()
        max_date = today + pd.Timedelta(days=max_days)
        for ticker in self.tickers:
            try:
                log.info("Fetching options for %s", ticker)
                tk = yf.Ticker(ticker)
                expirations = tk.options
                if not expirations:
                    log.warning("No options found for %s", ticker)
                    continue
                valid = [d for d in expirations if today.normalize() <= pd.to_datetime(d) <= max_date]
                if not valid:
                    log.warning("No options within %d days for %s", max_days, ticker)
                    continue
                self.options_data[ticker] = {}
                for date in valid:
                    opt = tk.option_chain(date)
                    self.options_data[ticker][date] = {"calls": opt.calls, "puts": opt.puts}
            except Exception as e:
                log.error("Error fetching options for %s: %s", ticker, e)

    def get_data(self, ticker: str) -> pd.DataFrame | None:
        return self.data.get(ticker)
