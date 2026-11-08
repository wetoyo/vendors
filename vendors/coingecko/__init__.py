"""CoinGecko API v3 (crypto prices, market caps, trending, global metrics).

Works keyless on the public API. Set ``COINGECKO_API_KEY`` to send a demo key,
or also ``COINGECKO_PRO=1`` to use pro-api.coingecko.com with a pro key.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from datetime import datetime, timezone

import pandas as pd
import requests

from vendors.core import Vendor, dataset

PUBLIC_URL = "https://api.coingecko.com/api/v3"
PRO_URL = "https://pro-api.coingecko.com/api/v3"
RETRIES = 4


def _ids(value: Sequence[str] | str) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ms(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, unit="ms", utc=True)


def _pro() -> bool:
    return os.environ.get("COINGECKO_PRO", "").strip().lower() in ("1", "true", "yes")


def get(path: str, **params) -> dict | list:
    """GET a CoinGecko endpoint, backing off on 429."""
    key = os.environ.get("COINGECKO_API_KEY")
    base = PRO_URL if _pro() else PUBLIC_URL
    headers = {"accept": "application/json"}
    if key:
        headers["x-cg-pro-api-key" if _pro() else "x-cg-demo-api-key"] = key
    params = {k: v for k, v in params.items() if v is not None}
    for attempt in range(RETRIES + 1):
        resp = requests.get(f"{base}{path}", params=params, headers=headers, timeout=30)
        if resp.status_code != 429 or attempt == RETRIES:
            resp.raise_for_status()
            return resp.json()
        retry_after = resp.headers.get("Retry-After", "")
        time.sleep(min(float(retry_after), 60.0) if retry_after.isdigit() else 2.0 * 2**attempt)
    raise RuntimeError("unreachable")


class CoinGeckoVendor(Vendor):
    name = "coingecko"
    description = "CoinGecko API v3: crypto markets, price history, trending, global metrics"

    @dataset()
    def markets(
        self,
        vs_currency: str = "usd",
        per_page: int = 250,
        pages: int = 1,
        ids: Sequence[str] | str | None = None,
    ) -> pd.DataFrame:
        """Coin market snapshot (price, market cap, volume) ranked by market cap."""
        snapshot = _now()
        id_list = ",".join(_ids(ids)) if ids else None
        rows: list[dict] = []
        for page in range(1, int(pages) + 1):
            batch = get(
                "/coins/markets", vs_currency=vs_currency, ids=id_list,
                order="market_cap_desc", per_page=int(per_page), page=page,
            )
            rows.extend(batch)
            if len(batch) < int(per_page):
                break
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df.insert(0, "snapshot_time", snapshot)
        df.insert(2, "vs_currency", vs_currency)
        return df

    @dataset(key=["id", "vs_currency", "timestamp"])
    def market_chart(
        self, ids: Sequence[str] | str = "bitcoin,ethereum", vs_currency: str = "usd", days: int | str = 30,
    ) -> pd.DataFrame:
        """Price, market cap and volume history per coin."""
        frames = []
        for coin in _ids(ids):
            data = get(f"/coins/{coin}/market_chart", vs_currency=vs_currency, days=days)
            parts = [
                pd.DataFrame(data.get(field) or [], columns=["timestamp", col])
                .drop_duplicates("timestamp", keep="last").set_index("timestamp")
                for field, col in (("prices", "price"), ("market_caps", "market_cap"), ("total_volumes", "total_volume"))
            ]
            df = pd.concat(parts, axis=1).reset_index()
            if df.empty:
                continue
            df.insert(0, "id", coin)
            df.insert(1, "vs_currency", vs_currency)
            df["timestamp"] = _ms(df["timestamp"])
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @dataset(key=["id", "vs_currency", "timestamp"])
    def ohlc(self, id: str = "bitcoin", vs_currency: str = "usd", days: int | str = 30) -> pd.DataFrame:
        """OHLC candles for one coin (granularity set by ``days``)."""
        data = get(f"/coins/{id}/ohlc", vs_currency=vs_currency, days=days)
        df = pd.DataFrame(data or [], columns=["timestamp", "open", "high", "low", "close"])
        if df.empty:
            return df
        df.insert(0, "id", id)
        df.insert(1, "vs_currency", vs_currency)
        df["timestamp"] = _ms(df["timestamp"])
        return df

    @dataset()
    def trending(self) -> pd.DataFrame:
        """Trending coins, NFTs and categories (one row each, ``type`` says which)."""
        data = get("/search/trending")
        snapshot = _now()
        rows = []
        for kind, field in (("coin", "coins"), ("nft", "nfts"), ("category", "categories")):
            for rank, entry in enumerate(data.get(field) or [], start=1):
                item = entry.get("item", entry) if isinstance(entry, dict) else {}
                rows.append({"snapshot_time": snapshot, "type": kind, "rank": rank, **item})
        return pd.DataFrame(rows)

    @dataset()
    def global_metrics(self) -> pd.DataFrame:
        """Global crypto market totals and dominance."""
        data = get("/global").get("data", {})
        row = {"snapshot_time": _now(), **data}
        for field in ("total_market_cap", "total_volume", "market_cap_percentage"):
            for cur in ("usd", "btc", "eth"):
                if cur in (data.get(field) or {}):
                    row[f"{field}_{cur}"] = data[field][cur]
        return pd.DataFrame([row])
