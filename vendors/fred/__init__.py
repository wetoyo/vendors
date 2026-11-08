"""St. Louis Fed FRED API (economic time series).

Set ``FRED_API_KEY`` (free at https://fred.stlouisfed.org/docs/api/api_key.html).
``observations`` and ``series_info`` take ``series_ids`` or a ``preset`` name (see PRESETS).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from vendors.core import Vendor, dataset

BASE_URL = "https://api.stlouisfed.org/fred"
PAGE = 1000  # max page size for /series/search

PRESETS: dict[str, list[str]] = {
    "rates": ["DFF", "DGS2", "DGS10", "DGS30", "T10Y2Y", "SOFR"],
    "inflation": ["CPIAUCSL", "CPILFESL", "PCEPI", "T5YIE"],
    "labor": ["UNRATE", "PAYEMS", "ICSA"],
    "growth": ["GDPC1", "INDPRO"],
    "energy": ["DCOILWTICO", "DCOILBRENTEU", "DHHNGSP", "GASREGW"],
    "credit": ["BAMLH0A0HYM2", "BAMLC0A0CM"],
    "fx": ["DTWEXBGS", "DEXUSEU", "DEXJPUS"],
}


class MissingApiKey(RuntimeError):
    """FRED_API_KEY is not set."""


def api_key() -> str:
    """Returns ``$FRED_API_KEY`` or raises MissingApiKey."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "FRED_API_KEY is not set; get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    return key


def get(path: str, **params) -> dict:
    """GETs ``/fred/<path>`` as JSON with the API key applied."""
    params = {k: v for k, v in params.items() if v is not None}
    params.update(api_key=api_key(), file_type="json")
    r = requests.get(f"{BASE_URL}/{path.lstrip('/')}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _series_ids(series_ids: Sequence[str] | str | None, preset: str | None) -> list[str]:
    if preset:
        if preset not in PRESETS:
            raise KeyError(f"Unknown FRED preset {preset!r}; available: {sorted(PRESETS)}")
        return list(PRESETS[preset])
    if isinstance(series_ids, str):
        series_ids = series_ids.split(",")
    ids = [str(s).strip().upper() for s in series_ids or [] if str(s).strip()]
    if not ids:
        raise ValueError("Pass series_ids or preset")
    return ids


def _value(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan  # FRED uses "." for missing


class FredVendor(Vendor):
    name = "fred"
    description = "St. Louis Fed FRED economic time series"

    @dataset(key=["series_id", "date"])
    def observations(
        self,
        series_ids: Sequence[str] | str | None = "DGS10,DGS2",
        start: str | None = None,
        end: str | None = None,
        preset: str | None = None,
    ) -> pd.DataFrame:
        """Series observations, one row per (series_id, date); missing values are NaN."""
        frames = []
        for sid in _series_ids(series_ids, preset):
            obs = get(
                "series/observations", series_id=sid, observation_start=start, observation_end=end
            ).get("observations", [])
            df = pd.DataFrame(obs, columns=["date", "value", "realtime_start", "realtime_end"])
            df.insert(0, "series_id", sid)
            df["value"] = df["value"].map(_value).astype(float)
            frames.append(df)
        return pd.concat(frames, ignore_index=True)

    @dataset(key=["id"])
    def series_info(
        self, series_ids: Sequence[str] | str | None = None, preset: str | None = None
    ) -> pd.DataFrame:
        """Series metadata (title, units, frequency, last updated)."""
        rows = []
        for sid in _series_ids(series_ids, preset):
            rows.extend(get("series", series_id=sid).get("seriess", []))
        return pd.DataFrame(rows)

    @dataset(key=["id"])
    def search(self, query: str, limit: int = 100) -> pd.DataFrame:
        """Series matching a full-text query, most popular first."""
        rows: list[dict] = []
        while len(rows) < limit:
            page = get(
                "series/search",
                search_text=query,
                limit=min(PAGE, limit - len(rows)),
                offset=len(rows),
                order_by="popularity",
                sort_order="desc",
            ).get("seriess", [])
            rows.extend(page)
            if len(page) < PAGE:
                break
        return pd.DataFrame(rows[:limit])

    @dataset(key=["release_id", "date"])
    def release_dates(self, days_ahead: int = 14) -> pd.DataFrame:
        """Scheduled release dates from today through ``days_ahead`` days out."""
        today = date.today()
        data = get(
            "releases/dates",
            realtime_start=today.isoformat(),
            realtime_end=(today + timedelta(days=days_ahead)).isoformat(),
            include_release_dates_with_no_data="true",
            sort_order="asc",
            limit=1000,
        )
        return pd.DataFrame(data.get("release_dates", []), columns=["release_id", "release_name", "date"])
