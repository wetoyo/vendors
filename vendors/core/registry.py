"""Vendor registry.

Vendors are registered by import path and imported lazily, so a missing
optional dependency (e.g. ``yfinance``) only breaks that one vendor.

To plug in a new vendor: create ``vendors/<name>/`` with a ``Vendor``
subclass and add one line to ``REGISTRY``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from .base import Vendor

REGISTRY: dict[str, str] = {
    "aisstream": "vendors.aisstream:AisstreamVendor",
    "bluesky": "vendors.bluesky:BlueskyVendor",
    "cftc": "vendors.cftc:CftcVendor",
    "coingecko": "vendors.coingecko:CoinGeckoVendor",
    "copernicus": "vendors.copernicus:CopernicusVendor",
    "deribit": "vendors.deribit:DeribitVendor",
    "earth_search": "vendors.earth_search:EarthSearchVendor",
    "eia": "vendors.eia:EiaVendor",
    "fama_french": "vendors.fama_french:FamaFrenchVendor",
    "fred": "vendors.fred:FredVendor",
    "gdelt": "vendors.gdelt:GdeltVendor",
    "hackernews": "vendors.hackernews:HackerNewsVendor",
    "kalshi": "vendors.kalshi:KalshiVendor",
    "nasa_firms": "vendors.nasa_firms:NasaFirmsVendor",
    "nws": "vendors.nws:NwsVendor",
    "open_meteo": "vendors.open_meteo:OpenMeteoVendor",
    "opensky": "vendors.opensky:OpenSkyVendor",
    "planetary_computer": "vendors.planetary_computer:PlanetaryComputerVendor",
    "polymarket": "vendors.polymarket:PolymarketVendor",
    "reddit": "vendors.reddit:RedditVendor",
    "rss": "vendors.rss:RssVendor",
    "sec": "vendors.sec:SecVendor",
    "stocktwits": "vendors.stocktwits:StocktwitsVendor",
    "tank_watch": "vendors.tank_watch:TankWatchVendor",
    "yahoo": "vendors.yahoo:YahooVendor",
}


def available() -> list[str]:
    return sorted(REGISTRY)


def vendor_class(name: str) -> type[Vendor]:
    if name not in REGISTRY:
        raise KeyError(f"Unknown vendor {name!r}; available: {available()}")
    module, cls = REGISTRY[name].split(":")
    return getattr(importlib.import_module(module), cls)


def get_vendor(name: str, data_dir: str | Path | None = None, **kwargs) -> Vendor:
    """Instantiates a registered vendor. ``data_dir`` overrides its storage folder."""
    return vendor_class(name)(data_dir=data_dir, **kwargs)
