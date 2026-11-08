"""StockTwits public API (api.stocktwits.com/api/2): message streams, sentiment, trending.

No key needed. Override the User-Agent with ``STOCKTWITS_USER_AGENT``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timezone

import pandas as pd
import requests

from vendors.core import Vendor, dataset

BASE_URL = "https://api.stocktwits.com/api/2"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class StocktwitsBlocked(RuntimeError):
    """StockTwits (Cloudflare) refused the request."""


def _symbols(value: Sequence[str] | str) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [s.strip().upper() for s in value if s and s.strip()]


def get(path: str, params: dict | None = None) -> dict:
    """GETs ``BASE_URL/path`` and returns the JSON body."""
    headers = {
        "User-Agent": os.environ.get("STOCKTWITS_USER_AGENT") or DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    r = requests.get(f"{BASE_URL}/{path}", params=params, headers=headers, timeout=30)
    if r.status_code == 403:
        raise StocktwitsBlocked(
            f"StockTwits returned 403 for {path} (likely Cloudflare bot protection). "
            "Try another network, slow down, or set STOCKTWITS_USER_AGENT to a current browser User-Agent."
        )
    r.raise_for_status()
    return r.json()


def _sentiment(msg: dict) -> str | None:
    s = (msg.get("entities") or {}).get("sentiment") or {}
    return s.get("basic")


class StocktwitsVendor(Vendor):
    name = "stocktwits"
    description = "StockTwits symbol message streams, sentiment and trending symbols"

    def _messages(self, symbol: str, limit: int, max_id: int | None, pages: int) -> list[dict]:
        rows: list[dict] = []
        cursor = max_id
        for _ in range(max(1, int(pages))):
            params = {"limit": limit}
            if cursor:
                params["max"] = cursor
            body = get(f"streams/symbol/{symbol}.json", params)
            for m in body.get("messages") or []:
                user = m.get("user") or {}
                rows.append({
                    "id": m.get("id"),
                    "symbol": symbol,
                    "body": m.get("body"),
                    "created_at": m.get("created_at"),
                    "user_username": user.get("username"),
                    "user_followers": user.get("followers"),
                    "sentiment": _sentiment(m),
                    "likes": (m.get("likes") or {}).get("total", 0),
                    "symbols": [s.get("symbol") for s in m.get("symbols") or []],
                })
            cur = body.get("cursor") or {}
            if not cur.get("more") or not cur.get("max"):
                break
            cursor = cur["max"]
        return rows

    @dataset(key=["id"])
    def messages(
        self,
        symbols: Sequence[str] | str = "AAPL,TSLA,SPY",
        limit: int = 30,
        max_id: int | None = None,
        pages: int = 1,
    ) -> pd.DataFrame:
        """Recent messages per symbol, paging back from ``max_id`` for ``pages`` pages."""
        rows = []
        for sym in _symbols(symbols):
            rows += self._messages(sym, limit, max_id, pages)
        return pd.DataFrame(rows)

    @dataset()
    def sentiment(
        self, symbols: Sequence[str] | str = "AAPL,TSLA,SPY", limit: int = 30, pages: int = 1
    ) -> pd.DataFrame:
        """Bullish/bearish counts and ratios per symbol over its latest messages."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for sym in _symbols(symbols):
            msgs = self._messages(sym, limit, None, pages)
            bull = sum(m["sentiment"] == "Bullish" for m in msgs)
            bear = sum(m["sentiment"] == "Bearish" for m in msgs)
            tagged = bull + bear
            rows.append({
                "snapshot_time": now,
                "symbol": sym,
                "messages": len(msgs),
                "bullish": bull,
                "bearish": bear,
                "untagged": len(msgs) - tagged,
                "bullish_ratio": bull / tagged if tagged else None,
                "bearish_ratio": bear / tagged if tagged else None,
                "newest_id": max((m["id"] for m in msgs), default=None),
            })
        return pd.DataFrame(rows)

    @dataset()
    def trending(self) -> pd.DataFrame:
        """Currently trending symbols, ranked."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for i, s in enumerate(get("trending/symbols.json").get("symbols") or [], start=1):
            rows.append({
                "snapshot_time": now,
                "rank": i,
                "symbol": s.get("symbol"),
                "title": s.get("title"),
                "exchange": s.get("exchange"),
                "instrument_class": s.get("instrument_class"),
                "sector": s.get("sector"),
                "industry": s.get("industry"),
                "trending_score": s.get("trending_score"),
                "watchlist_count": s.get("watchlist_count"),
                "summary": (s.get("trends") or {}).get("summary"),
            })
        return pd.DataFrame(rows)
