"""Polymarket (prediction markets) via the ``external/prediction_market_scraper`` submodule.

Everything here is public: Gamma API for markets/events, CLOB for order books,
price history and the live order-book websocket. Order-book/price datasets are
keyed by CLOB *token* id; get them from ``markets``' ``clob_token_ids`` column
(one token per outcome, e.g. [Yes, No]).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from datetime import datetime, timezone

import pandas as pd
import requests

from vendors.core import Vendor, dataset
from vendors.core.submodules import import_external

# Gamma returns most numbers as strings; these are coerced to floats.
_NUMERIC = [
    "liquidity", "volume", "volume24hr", "volume1wk", "volume1mo", "volume1yr",
    "bestBid", "bestAsk", "lastTradePrice", "spread",
    "oneDayPriceChange", "oneWeekPriceChange", "oneMonthPriceChange",
]
# Bulky nested/presentational fields left out of the markets table.
_DROP = ["events", "clobRewards", "image", "icon", "raw_json"]


def _fetch():
    return import_external("Polymarket.fetch_historical")


def _clean():
    return import_external("Polymarket.clean_historical")


def _ids(value: Sequence[str | int] | str | int) -> list[str]:
    if isinstance(value, (str, int)):
        return [s.strip() for s in str(value).split(",") if s.strip()]
    return [str(v) for v in value]


class PolymarketVendor(Vendor):
    name = "polymarket"
    description = "Polymarket Gamma + CLOB APIs (prediction markets)"

    @dataset(key=["id"])
    def markets(
        self,
        query: str | None = None,
        active: bool = True,
        closed: bool = False,
        max_pages: int = 10,
    ) -> pd.DataFrame:
        """Markets with Polymarket.clean_market's fields; ``query`` searches by text instead of listing."""
        fetch, clean = _fetch(), _clean()
        if query:
            status = "active" if active and not closed else "closed" if closed else "all"
            raw = [m for e in fetch.search_events(query, events_status=status) for m in e.get("markets", [])]
        else:
            raw = fetch.fetch_markets(active=active, closed=closed, max_pages=max_pages)
        rows = []
        for m in raw:
            row = {**m, **clean.clean_market(m)}
            for k in _DROP:
                row.pop(k, None)
            rows.append(row)
        df = pd.DataFrame(rows)
        for col in set(_NUMERIC) & set(df.columns):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    @dataset(key=["id"])
    def events(self, query: str, events_status: str = "active", limit_per_type: int = 20) -> pd.DataFrame:
        """Events (groups of related markets) matching a free-text search."""
        rows = []
        for e in _fetch().search_events(query, events_status=events_status, limit_per_type=limit_per_type):
            markets = e.pop("markets", []) or []
            e.pop("tags", None)
            rows.append({**e, "market_ids": [m.get("id") for m in markets], "n_markets": len(markets)})
        return pd.DataFrame(rows)

    @dataset()  # append-only snapshots
    def orderbooks(self, token_ids: Sequence[str] | str) -> pd.DataFrame:
        """Order-book snapshots (bids/asks as JSON) for CLOB token ids."""
        fetch, clean = _fetch(), _clean()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for tok in _ids(token_ids):
            book = fetch.fetch_order_book(tok)
            row = clean.clean_order_book(tok, book)
            row.pop("raw_json", None)
            rows.append({
                "snapshot_ts": ts,
                "market": book.get("market"),
                "book_timestamp": book.get("timestamp"),
                "tick_size": book.get("tick_size"),
                "last_trade_price": book.get("last_trade_price"),
                **row,
            })
        return pd.DataFrame(rows)

    @dataset(key=["token_id", "t"])
    def price_history(
        self,
        token_ids: Sequence[str] | str,
        interval: str | None = "1w",
        fidelity: int = 60,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        """Price time series per token from CLOB /prices-history.

        Either ``interval`` (1h, 6h, 1d, 1w, 1m, max) or ``start_ts``/``end_ts``
        (unix seconds). ``fidelity`` is the bar size in minutes.
        """
        base = _fetch().CLOB_BASE_URL
        frames = []
        for tok in _ids(token_ids):
            params = {"market": tok, "fidelity": fidelity}
            if start_ts is not None or end_ts is not None:
                params.update({k: v for k, v in (("startTs", start_ts), ("endTs", end_ts)) if v is not None})
            elif interval:
                params["interval"] = interval
            r = requests.get(f"{base}/prices-history", params=params, timeout=20)
            r.raise_for_status()
            hist = pd.DataFrame(r.json().get("history", []), columns=["t", "p"])
            frames.append(hist.assign(token_id=tok))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["t", "p", "token_id"])
        df = df.rename(columns={"p": "price"})
        df["ts_utc"] = pd.to_datetime(df["t"], unit="s", utc=True).astype(str)
        return df[["token_id", "t", "ts_utc", "price"]]

    async def stream(self, asset_ids: Sequence[str]) -> AsyncIterator[dict]:
        """Live book / price_change messages for CLOB token ids (public, no credentials)."""
        live = import_external("Polymarket.live_datastream")
        async with aclosing(live.stream_order_books(_ids(asset_ids))) as feed:
            async for msg in feed:
                yield msg
