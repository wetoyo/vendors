"""Imports code that lives in git submodules under ``external/``.

The upstream repos (derbit, prediction_market_scraper) are kept as-is, not
vendored or modified, so their packages are imported by putting the right
directory on ``sys.path``. Adapters call :func:`import_external` instead of
touching ``sys.path`` themselves.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

from .storage import REPO_ROOT

EXTERNAL = REPO_ROOT / "external"

# module name -> (submodule dir, directory to put on sys.path relative to it)
_SOURCES = {
    "derbit": ("derbit", ".."),
    "Kalshi": ("prediction_market_scraper", "Clients"),
    "Polymarket": ("prediction_market_scraper", "Clients"),
}


def import_external(module: str) -> ModuleType:
    """Imports ``module`` (e.g. ``"derbit"`` or ``"Kalshi.fetch_historical"``) from a submodule."""
    top = module.split(".")[0]
    if top not in _SOURCES:
        raise KeyError(f"No submodule source registered for {top!r}")
    submodule, rel = _SOURCES[top]
    checkout = EXTERNAL / submodule
    if not any(checkout.glob("*")):
        raise ImportError(
            f"Submodule external/{submodule} is empty. Run: git submodule update --init --recursive"
        )
    search_dir = str((checkout / rel).resolve())
    if search_dir not in sys.path:
        sys.path.insert(0, search_dir)
    return importlib.import_module(module)
