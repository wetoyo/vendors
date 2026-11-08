"""Lets the suite run on a partial (sparse) checkout.

Mark a test with ``@pytest.mark.vendor("kalshi")``; it is skipped when that
vendor's package, its submodule, or its optional dependencies aren't present,
instead of failing. See "Working on one vendor" in the README.
"""

import importlib

import pytest

from vendors.core import vendor_class
from vendors.core.submodules import import_external

# What each vendor needs importable beyond its own package under vendors/.
_PROBES = {
    "deribit": lambda: import_external("derbit"),
    "kalshi": lambda: import_external("Kalshi.fetch_historical"),
    "polymarket": lambda: import_external("Polymarket.fetch_historical"),
    "yahoo": lambda: importlib.import_module("yfinance"),
    "sec": lambda: None,
    "aisstream": lambda: importlib.import_module("websockets"),
}


def vendor_missing(name: str) -> str | None:
    try:
        vendor_class(name)
        _PROBES.get(name, lambda: None)()
    except ImportError as e:
        return f"{name} not checked out / installed: {e}"
    return None


def pytest_configure(config):
    config.addinivalue_line("markers", "vendor(name): skip unless this vendor is checked out")


def pytest_runtest_setup(item):
    for mark in item.iter_markers("vendor"):
        reason = vendor_missing(mark.args[0])
        if reason:
            pytest.skip(reason)
