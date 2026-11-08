"""US Energy Information Administration (EIA) API v2.

Set ``EIA_API_KEY`` (free key from https://www.eia.gov/opendata/register.php).
Weekly crude stocks (``preset="crude_stocks_us"``) are the ground truth for
satellite oil-tank estimates.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

import pandas as pd
import requests

from vendors.core import Vendor, dataset

BASE_URL = "https://api.eia.gov/v2"
PAGE_SIZE = 5000  # API maximum rows per request

# preset -> (route, series id, frequency)
PRESETS: dict[str, tuple[str, str, str]] = {
    "crude_stocks_us": ("petroleum/stoc/wstk", "WCESTUS1", "weekly"),
    "crude_stocks_cushing": ("petroleum/stoc/wstk", "W_EPC0_SAX_YCUOK_MBBL", "weekly"),
    "spr_stocks": ("petroleum/stoc/wstk", "WCSSTUS1", "weekly"),
    "gasoline_stocks": ("petroleum/stoc/wstk", "WGTSTUS1", "weekly"),
    "distillate_stocks": ("petroleum/stoc/wstk", "WDISTUS1", "weekly"),
    "refinery_utilization": ("petroleum/pnp/wiup", "WPULEUS3", "weekly"),
    "crude_production": ("petroleum/sum/sndw", "WCRFPUS2", "weekly"),
    "wti_spot": ("petroleum/pri/spt", "RWTC", "daily"),
    "henry_hub_spot": ("natural-gas/pri/fut", "RNGWHHD", "daily"),
}


class MissingApiKey(RuntimeError):
    """Raised when ``EIA_API_KEY`` is not set."""


def api_key() -> str:
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "EIA_API_KEY is not set; get a free key at https://www.eia.gov/opendata/register.php"
        )
    return key


def get(path: str, params: list[tuple[str, str]] | None = None) -> dict:
    """GET ``{BASE_URL}/{path}/`` with the API key; returns the ``response`` object."""
    key = api_key()
    path = path.strip("/")
    url = f"{BASE_URL}/{path}/" if path else f"{BASE_URL}/"
    r = requests.get(url, params=[("api_key", key), *(params or [])], timeout=60)
    try:
        body = r.json()
    except ValueError:
        r.raise_for_status()
        raise
    if r.status_code >= 400 or "error" in body:
        raise RuntimeError(f"EIA {r.status_code} for {path or '/'}: {body.get('error') or body}")
    return body.get("response", {})


def _list(value: Sequence[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def fetch_data(
    route: str,
    series: Sequence[str] | str | None = None,
    frequency: str | None = "weekly",
    start: str | None = None,
    end: str | None = None,
    facets: Mapping[str, Sequence[str] | str] | None = None,
) -> list[dict]:
    """All rows of ``/v2/{route}/data``, paging with offset/length."""
    params: list[tuple[str, str]] = [
        ("data[0]", "value"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
    ]
    if frequency:
        params.append(("frequency", frequency))
    if start:
        params.append(("start", str(start)))
    if end:
        params.append(("end", str(end)))
    all_facets = {k: _list(v) for k, v in (facets or {}).items()}
    if series:
        all_facets["series"] = _list(series)
    for name, values in all_facets.items():
        params += [(f"facets[{name}][]", v) for v in values]

    rows: list[dict] = []
    offset = 0
    while True:
        page_params = params + [("offset", str(offset)), ("length", str(PAGE_SIZE))]
        resp = get(f"{route.strip('/')}/data", page_params)
        page = resp.get("data") or []
        rows += page
        offset += len(page)
        total = int(resp.get("total") or 0)
        if not page or len(page) < PAGE_SIZE or offset >= total:
            return rows


def _normalize(rows: list[dict], route: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["series", "period", "value", "route"])
    df["route"] = route.strip("/")
    if "value" in df:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
    front = [c for c in ("series", "period", "value", "units") if c in df]
    return df[front + [c for c in df.columns if c not in front]]


class EiaVendor(Vendor):
    name = "eia"
    description = "US Energy Information Administration API v2 (petroleum, natural gas, ...)"

    @dataset(key=["series", "period"])
    def series(
        self,
        route: str = "petroleum/stoc/wstk",
        series: Sequence[str] | str | None = None,
        frequency: str | None = "weekly",
        start: str | None = None,
        end: str | None = None,
        facets: Mapping[str, Sequence[str] | str] | None = None,
        preset: str | None = None,
    ) -> pd.DataFrame:
        """Observations from any v2 data route, one row per (series, period); or pick a preset."""
        if preset:
            if preset not in PRESETS:
                raise KeyError(f"Unknown EIA preset {preset!r}; available: {sorted(PRESETS)}")
            route, series, frequency = PRESETS[preset]
        df = _normalize(fetch_data(route, series, frequency, start, end, facets), route)
        if preset:
            df.insert(0, "preset", preset)
        return df

    @dataset(key=["series", "period"])
    def weekly_petroleum(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """All weekly petroleum presets (stocks, utilization, production) in one table."""
        by_route: dict[str, dict[str, str]] = {}
        for preset, (route, sid, freq) in PRESETS.items():
            if freq == "weekly":
                by_route.setdefault(route, {})[sid] = preset
        frames = []
        for route, ids in by_route.items():
            df = _normalize(fetch_data(route, list(ids), "weekly", start, end), route)
            if not df.empty:
                df.insert(0, "preset", df["series"].map(ids))
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @dataset(key=["route", "id"])
    def routes(self, route: str = "") -> pd.DataFrame:
        """Child routes under a route (or a leaf route's facets/frequencies), for discovery."""
        resp = get(route)
        parent = route.strip("/")
        children = resp.get("routes") or []
        if children:
            return pd.DataFrame([
                {"route": parent, "id": c.get("id"), "path": f"{parent}/{c.get('id')}".strip("/"),
                 "name": c.get("name"), "description": c.get("description"), "leaf": False}
                for c in children
            ])
        return pd.DataFrame([{
            "route": parent, "id": resp.get("id") or parent.rsplit("/", 1)[-1], "path": parent,
            "name": resp.get("name"), "description": resp.get("description"), "leaf": True,
            "frequency": [f.get("id") for f in resp.get("frequency") or []],
            "facets": [f.get("id") for f in resp.get("facets") or []],
            "data_columns": list((resp.get("data") or {}).keys()),
            "start_period": resp.get("startPeriod"), "end_period": resp.get("endPeriod"),
            "default_frequency": resp.get("defaultFrequency"),
        }])
