"""Deribit (crypto options/futures) via the ``external/derbit`` submodule.

Public endpoints only -- no credentials needed.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence

import pandas as pd
import requests

from vendors.core import Vendor, dataset
from vendors.core.submodules import import_external


def _derbit():
    return import_external("derbit")


class DeribitVendor(Vendor):
    name = "deribit"
    description = "Deribit public REST/WebSocket API (crypto options & futures)"

    @dataset(key=["instrument_name"])
    def instruments(self, currency: str = "BTC", kind: str = "option", expired: bool = False) -> pd.DataFrame:
        """Instrument definitions (strike, expiry, tick size, ...) for a currency/kind."""
        return pd.DataFrame(_derbit().get_instruments(currency=currency, kind=kind, expired=expired))

    @dataset(key=["instrument_name", "creation_timestamp"])
    def book_summary(self, currency: str = "BTC", kind: str = "option") -> pd.DataFrame:
        """Top-of-book/mark/IV/open-interest snapshot for every instrument in one request."""
        df = pd.DataFrame(_derbit().get_book_summary_by_currency(currency=currency, kind=kind))
        if not df.empty:
            df.insert(0, "currency", currency)
        return df

    @dataset(key=["instrument_name", "timestamp"])
    def order_books(
        self,
        instruments: Sequence[str] | str | None = None,
        currency: str = "BTC",
        kind: str = "option",
        limit: int | None = 25,
    ) -> pd.DataFrame:
        """Full order-book snapshots incl. greeks, normalized by derbit.clean_order_book.

        Pass ``instruments`` explicitly, or a currency/kind (first ``limit``
        active instruments; one HTTP request each, so keep it modest).
        """
        d = _derbit()
        if instruments is None:
            names = [i["instrument_name"] for i in d.get_instruments(currency=currency, kind=kind)]
            instruments = names[:limit] if limit else names
        elif isinstance(instruments, str):
            instruments = [s.strip() for s in instruments.split(",") if s.strip()]
        rows = [d.clean_order_book(d.get_order_book(name)) for name in instruments]
        return pd.DataFrame(rows)

    @dataset(key=["trade_id"])
    def trades(
        self, currency: str = "BTC", kind: str = "option", instrument_name: str | None = None, count: int = 100
    ) -> pd.DataFrame:
        """Most recent trades for an instrument, or for a whole currency/kind."""
        d = _derbit()
        if instrument_name:
            res = d.get_last_trades_by_instrument(instrument_name, count=count)
        else:
            # derbit.get_last_trades_by_currency doesn't pass `kind`, which Deribit
            # now requires (error 11060 "not_supported_for_coinbase_routed_spot"
            # without it), so call the endpoint directly until that's fixed upstream.
            r = requests.get(
                f"{d.fetch_historical.BASE_URL}/public/get_last_trades_by_currency",
                params={"currency": currency, "kind": kind, "count": count},
                timeout=20,
            )
            r.raise_for_status()
            res = r.json().get("result", {})
        return pd.DataFrame(res.get("trades", []))

    @dataset(key=["currency", "timestamp"])
    def historical_volatility(self, currency: str = "BTC") -> pd.DataFrame:
        """Deribit's hourly realized-volatility series."""
        raw = _derbit().get_historical_volatility(currency)
        df = pd.DataFrame(raw, columns=["timestamp", "volatility"])
        df.insert(0, "currency", currency)
        return df

    @dataset(key=["index_name", "timestamp"])
    def index_price(self, index_name: str = "btc_usd") -> pd.DataFrame:
        """Current index price (timestamped at fetch time, ms since epoch)."""
        res = _derbit().get_index_price(index_name)
        return pd.DataFrame([{"index_name": index_name, "timestamp": int(time.time() * 1000), **res}])

    async def stream(
        self, instruments: Sequence[str], update_types: Sequence[str] | None = None
    ) -> AsyncIterator[dict]:
        """Live ticker/book/trade messages, e.g. update_types=["ticker.100ms", "book.100ms"]."""
        async for msg in _derbit().stream_instrument_updates(list(instruments), list(update_types or [])):
            yield msg
