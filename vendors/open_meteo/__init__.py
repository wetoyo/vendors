"""Open-Meteo weather forecasts, ERA5 history and geocoding (https://open-meteo.com).

No API key. Pick a point with ``latitude``/``longitude``, a preset ``location``
(see ``LOCATIONS``) or ``city`` (resolved through the geocoding API). Times are
UTC and units are Open-Meteo defaults (deg C, mm, km/h). ``latitude``/``longitude``
columns hold the requested point, so re-collects upsert cleanly;
``grid_latitude``/``grid_longitude`` are the model cell actually used.
"""

from __future__ import annotations

import os

import pandas as pd
import requests

from vendors.core import Vendor, dataset

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Demand centres and hubs that move power, gas and weather markets.
LOCATIONS: dict[str, tuple[float, float]] = {
    "nyc": (40.7789, -73.9692),          # Central Park (NYISO Zone J, Kalshi NYC highs)
    "chicago": (41.7868, -87.7522),      # Midway (PJM ComEd)
    "houston": (29.7604, -95.3698),      # ERCOT Houston, Gulf Coast refining
    "dallas": (32.7767, -96.7970),       # ERCOT North
    "austin": (30.2672, -97.7431),       # ERCOT South-Central
    "philadelphia": (39.9526, -75.1652),
    "boston": (42.3601, -71.0589),       # ISO-NE
    "atlanta": (33.7490, -84.3880),
    "denver": (39.7392, -104.9903),
    "los_angeles": (34.0522, -118.2437),  # CAISO SP15
    "miami": (25.7617, -80.1918),
    "henry_hub": (30.0960, -92.1060),    # Erath, LA gas hub
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "berlin": (52.5200, 13.4050),
    "tokyo": (35.6762, 139.6503),
}


def _user_agent() -> str:
    return os.environ.get("OPEN_METEO_USER_AGENT", "vendors-client/0.1")


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params=params, headers={"User-Agent": _user_agent()}, timeout=30)
    if r.status_code >= 400:
        try:
            reason = r.json().get("reason")
        except ValueError:
            reason = None
        if reason:
            raise ValueError(f"Open-Meteo error: {reason}")
        r.raise_for_status()
    return r.json()


def _csv(value) -> str:
    return value if isinstance(value, str) else ",".join(value)


def _frame(data: dict, block: str, lat: float, lon: float, label: str | None, time_col: str) -> pd.DataFrame:
    df = pd.DataFrame(data.get(block) or {})
    if df.empty:
        return df
    df = df.rename(columns={"time": time_col})
    df.insert(0, "latitude", lat)
    df.insert(1, "longitude", lon)
    df.insert(2, "location", label)
    df["grid_latitude"] = data.get("latitude")
    df["grid_longitude"] = data.get("longitude")
    df["elevation"] = data.get("elevation")
    return df


class OpenMeteoVendor(Vendor):
    name = "open_meteo"
    description = "Open-Meteo hourly forecasts, ERA5 daily history and geocoding (no key)"

    def _point(self, latitude, longitude, location, city) -> tuple[float, float, str | None]:
        if location is not None:
            loc = str(location).lower()
            if loc not in LOCATIONS:
                raise ValueError(f"unknown location {location!r}; presets: {sorted(LOCATIONS)}")
            return (*LOCATIONS[loc], loc)
        if city is not None:
            results = _get(GEOCODE_URL, {"name": city, "count": 1, "format": "json"}).get("results") or []
            if not results:
                raise ValueError(f"Open-Meteo geocoding found no place named {city!r}")
            return float(results[0]["latitude"]), float(results[0]["longitude"]), str(city)
        if latitude is None or longitude is None:
            raise ValueError(f"pass latitude= and longitude=, city=<name>, or location=<one of {sorted(LOCATIONS)}>")
        return float(latitude), float(longitude), None

    @dataset(key=["latitude", "longitude", "time"])
    def forecast(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        hourly: str = "temperature_2m,precipitation,wind_speed_10m",
        days: int = 7,
        location: str | None = None,
        city: str | None = None,
    ) -> pd.DataFrame:
        """Hourly forecast for a point, one row per hour (UTC)."""
        lat, lon, label = self._point(latitude, longitude, location, city)
        data = _get(FORECAST_URL, {"latitude": lat, "longitude": lon, "hourly": _csv(hourly),
                                   "forecast_days": int(days), "timezone": "UTC"})
        return _frame(data, "hourly", lat, lon, label, "time")

    @dataset(key=["latitude", "longitude", "date"])
    def historical(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        start: str | None = None,
        end: str | None = None,
        daily: str = "temperature_2m_max,temperature_2m_min,precipitation_sum",
        location: str | None = None,
        city: str | None = None,
    ) -> pd.DataFrame:
        """ERA5 reanalysis daily history for a point, one row per day."""
        if not start or not end:
            raise ValueError("historical needs start=YYYY-MM-DD and end=YYYY-MM-DD")
        lat, lon, label = self._point(latitude, longitude, location, city)
        data = _get(ARCHIVE_URL, {"latitude": lat, "longitude": lon, "start_date": str(start),
                                  "end_date": str(end), "daily": _csv(daily), "timezone": "UTC"})
        return _frame(data, "daily", lat, lon, label, "date")

    @dataset(key=["id"])
    def geocode(self, name: str, count: int = 10, language: str = "en") -> pd.DataFrame:
        """Places matching a name, with coordinates, country and population."""
        data = _get(GEOCODE_URL, {"name": name, "count": int(count), "language": language, "format": "json"})
        df = pd.DataFrame(data.get("results") or [])
        if not df.empty:
            df.insert(0, "query", name)
        return df
