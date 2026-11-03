"""Kalshi (prediction markets) via the ``external/prediction_market_scraper`` submodule.

Market/series/order-book/trade listing is public. ``stream()`` needs
``KALSHI_API_KEY_ID`` and ``KALSHI_PRIVATE_KEY_PATH`` (see the submodule's docs).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from datetime import datetime, timezone

import pandas as pd

from vendors.core import Vendor, dataset
from vendors.core.submodules import import_external


def _fetch():
    return import_external("Kalshi.fetch_historical")


def _clean():
    return import_external("Kalshi.clean_historical")


def _tickers(value: Sequence[str] | str) -> list[str]:
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return list(value)


class KalshiVendor(Vendor):
    name = "kalshi"
    description = "Kalshi trade-api v2 (event contracts / prediction markets)"

    @dataset(key=["ticker"])
    def series(self, category: str | None = None, limit: int = 200) -> pd.DataFrame:
        """Market series (KXFED, KXBTCD, ...), optionally filtered by category."""
        return pd.DataFrame(_fetch().fetch_series(category=category, limit=limit))

    @dataset(key=["ticker"])
    def markets(
        self,
        series_ticker: str | None = None,
        status: str | None = "open",
        max_pages: int = 10,
    ) -> pd.DataFrame:
        """Markets with all scalar fields plus Kalshi.clean_market's raw_title."""
        clean = _clean()
        rows = []
        for m in _fetch().fetch_markets(series_ticker=series_ticker, status=status, max_pages=max_pages):
            cleaned = clean.clean_market(m)
            cleaned.pop("raw_json", None)
            rows.append({**m, **cleaned})
        return pd.DataFrame(rows)

    @dataset()  # append-only: every fetch is a new point in the book's time series
    def orderbooks(self, tickers: Sequence[str] | str, depth: int | None = None) -> pd.DataFrame:
        """Order-book snapshots (yes/no price levels as JSON) for the given market tickers."""
        fetch, clean = _fetch(), _clean()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for t in _tickers(tickers):
            row = clean.clean_orderbook_snapshot(t, fetch.fetch_orderbook(t, depth=depth))
            rows.append({"snapshot_ts": ts, **row})
        return pd.DataFrame(rows)

    @dataset(key=["trade_id"])
    def trades(
        self,
        ticker: str,
        min_ts: int | None = None,
        max_ts: int | None = None,
        max_pages: int = 20,
    ) -> pd.DataFrame:
        """Executed trades for one market (min_ts/max_ts are unix seconds)."""
        return pd.DataFrame(_fetch().fetch_trades(ticker, min_ts=min_ts, max_ts=max_ts, max_pages=max_pages))

    async def stream(self, market_tickers: Sequence[str]) -> AsyncIterator[dict]:
        """Live orderbook_delta messages (requires Kalshi API credentials)."""
        live = import_external("Kalshi.live_datastream")
        async with aclosing(live.stream_orderbook(_tickers(market_tickers))) as feed:
            async for msg in feed:
                yield msg
