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
    "deribit": "vendors.deribit:DeribitVendor",
    "kalshi": "vendors.kalshi:KalshiVendor",
    "sec": "vendors.sec:SecVendor",
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
