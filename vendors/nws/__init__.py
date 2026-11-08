"""NOAA National Weather Service API (api.weather.gov). No key needed.

NWS asks for a User-Agent identifying the client; set ``NWS_USER_AGENT``
(e.g. "myapp (you@example.com)") or the generic default is used.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests

from vendors.core import Vendor, dataset

BASE_URL = "https://api.weather.gov"
DEFAULT_USER_AGENT = "vendors-client/0.1"

ALERT_FIELDS = [
    "id", "event", "severity", "certainty", "urgency", "status", "message_type", "category",
    "headline", "area_desc", "sent", "effective", "onset", "expires", "ends",
    "sender_name", "description", "instruction", "response",
]


def user_agent() -> str:
    return os.environ.get("NWS_USER_AGENT") or DEFAULT_USER_AGENT


def get(path_or_url: str, params: dict | None = None) -> dict:
    url = path_or_url if path_or_url.startswith("http") else BASE_URL + path_or_url
    r = requests.get(
        url, params=params, timeout=30,
        headers={"User-Agent": user_agent(), "Accept": "application/geo+json"},
    )
    r.raise_for_status()
    return r.json()


def _unit(code: str | None) -> str | None:
    return code.split(":", 1)[-1] if code else None


def _unwrap(props: dict[str, Any]) -> dict[str, Any]:
    """Turns ``{"value": v, "unitCode": u}`` objects into ``x`` and ``x_unit`` columns."""
    out: dict[str, Any] = {}
    for k, v in props.items():
        if isinstance(v, dict) and "value" in v:
            out[k] = v.get("value")
            out[f"{k}_unit"] = _unit(v.get("unitCode"))
        else:
            out[k] = v
    return out


class NwsVendor(Vendor):
    name = "nws"
    description = "NOAA National Weather Service forecasts, alerts and station observations"

    @dataset(key=["grid_id", "grid_x", "grid_y", "hourly", "start_time"])
    def forecast(self, latitude: float, longitude: float, hourly: bool = False) -> pd.DataFrame:
        """Forecast periods for a lat/lon (12-hour, or hourly)."""
        pt = get(f"/points/{float(latitude):.4f},{float(longitude):.4f}")["properties"]
        url = pt["forecastHourly" if hourly else "forecast"]
        fc = get(url)["properties"]
        rows = []
        for p in fc.get("periods", []):
            row = {
                "grid_id": pt["gridId"], "grid_x": pt["gridX"], "grid_y": pt["gridY"],
                "hourly": bool(hourly), "latitude": float(latitude), "longitude": float(longitude),
                "updated": fc.get("updateTime") or fc.get("updated"),
            }
            row.update(_unwrap(p))
            rows.append(row)
        return pd.DataFrame(rows)

    @dataset(key=["id"])
    def alerts(
        self, area: str | None = None, event: str | None = None, severity: str | None = None
    ) -> pd.DataFrame:
        """Active weather alerts, optionally by state/area, event type or severity."""
        params = {k: v for k, v in {"area": area, "event": event, "severity": severity}.items() if v}
        data = get("/alerts/active", params=params or None)
        rows = []
        for f in data.get("features", []):
            p = f.get("properties", {})
            row = {
                "id": p.get("id"), "event": p.get("event"), "severity": p.get("severity"),
                "certainty": p.get("certainty"), "urgency": p.get("urgency"),
                "status": p.get("status"), "message_type": p.get("messageType"),
                "category": p.get("category"), "headline": p.get("headline"),
                "area_desc": p.get("areaDesc"), "sent": p.get("sent"),
                "effective": p.get("effective"), "onset": p.get("onset"),
                "expires": p.get("expires"), "ends": p.get("ends"),
                "sender_name": p.get("senderName"), "description": p.get("description"),
                "instruction": p.get("instruction"), "response": p.get("response"),
                "ugc": (p.get("geocode") or {}).get("UGC"),
            }
            rows.append(row)
        return pd.DataFrame(rows, columns=ALERT_FIELDS + ["ugc"])

    @dataset(key=["station", "timestamp"])
    def observations(self, station: str = "KNYC", limit: int = 100) -> pd.DataFrame:
        """Recent observations from a station, one row per report."""
        station = station.upper()
        data = get(f"/stations/{station}/observations", params={"limit": int(limit)})
        rows = []
        for f in data.get("features", []):
            p = _unwrap(f.get("properties", {}))
            for k in ("@id", "@type", "icon", "station", "stationId", "presentWeather", "cloudLayers"):
                p.pop(k, None)
            rows.append({"station": station, **p})
        return pd.DataFrame(rows)

    @dataset(key=["station"])
    def stations(self, state: str | None = None, limit: int = 500) -> pd.DataFrame:
        """Observation stations, optionally in one state."""
        params: dict[str, Any] = {"limit": int(limit)}
        if state:
            params["state"] = state.upper()
        data = get("/stations", params=params)
        rows = []
        for f in data.get("features", []):
            p = _unwrap(f.get("properties", {}))
            lon, lat = ((f.get("geometry") or {}).get("coordinates") or [None, None])[:2]
            rows.append({
                "station": p.get("stationIdentifier"), "name": p.get("name"),
                "latitude": lat, "longitude": lon,
                "elevation": p.get("elevation"), "elevation_unit": p.get("elevation_unit"),
                "time_zone": p.get("timeZone"), "forecast_zone": p.get("forecast"),
                "county": p.get("county"), "fire_weather_zone": p.get("fireWeatherZone"),
            })
        return pd.DataFrame(rows)
