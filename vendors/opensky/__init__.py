"""OpenSky Network REST API (https://opensky-network.org/api).

Anonymous access works for live ``states``. Historical flight endpoints need
an account: set ``OPENSKY_CLIENT_ID`` and ``OPENSKY_CLIENT_SECRET`` (OAuth2
client credentials) and every request is sent with a bearer token.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone

import pandas as pd
import requests

from vendors.core import Vendor, dataset

API = "https://opensky-network.org/api"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
TIMEOUT = 30

# Positional fields of a /states/all state vector, in API order.
STATE_FIELDS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk", "spi",
    "position_source", "category",
]


def to_epoch(value: int | float | str | date | datetime) -> int:
    """Epoch seconds from an int, a numeric string, an ISO date/datetime string, or a date (UTC)."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return to_epoch(datetime.fromisoformat(s))


def parse_bbox(bbox: str | list | tuple) -> dict[str, float]:
    """``"min_lon,min_lat,max_lon,max_lat"`` -> lamin/lomin/lamax/lomax query params."""
    parts = bbox.split(",") if isinstance(bbox, str) else list(bbox)
    if len(parts) != 4:
        raise ValueError(f"bbox must be min_lon,min_lat,max_lon,max_lat; got {bbox!r}")
    lomin, lamin, lomax, lamax = (float(p) for p in parts)
    return {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}


class OpenSkyVendor(Vendor):
    name = "opensky"
    description = "OpenSky Network live aircraft states and historical flights"

    def __init__(self, data_dir=None):
        super().__init__(data_dir)
        self._token: str | None = None
        self._token_expiry = 0.0

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict[str, str]:
        cid, secret = os.environ.get("OPENSKY_CLIENT_ID"), os.environ.get("OPENSKY_CLIENT_SECRET")
        if not (cid and secret):
            return {}
        if not self._token or time.time() >= self._token_expiry:
            r = requests.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._token_expiry = time.time() + float(body.get("expires_in", 1800)) - 60
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict):
        r = requests.get(f"{API}{path}", params=params, headers=self._headers(), timeout=TIMEOUT)
        if r.status_code == 404:  # flights endpoints answer 404 when nothing matched
            return []
        r.raise_for_status()
        return r.json()

    def _flights(self, path: str, **params) -> pd.DataFrame:
        params["begin"], params["end"] = to_epoch(params["begin"]), to_epoch(params["end"])
        return pd.DataFrame(self._get(path, params) or [])

    # -------------------------------------------------------------- datasets
    @dataset()
    def states(self, bbox: str | None = None, icao24: str | None = None) -> pd.DataFrame:
        """Live state vectors of all aircraft, optionally within a bbox or for given icao24s."""
        params: dict = parse_bbox(bbox) if bbox else {}
        if icao24:
            ids = icao24.split(",") if isinstance(icao24, str) else list(icao24)
            params["icao24"] = [i.strip().lower() for i in ids if i.strip()]
        body = self._get("/states/all", params) or {}
        states = body.get("states") or []
        width = max([17, *(len(s) for s in states)])  # 18th field (category) only with extended=1
        df = pd.DataFrame([dict(zip(STATE_FIELDS, s)) for s in states], columns=STATE_FIELDS[:width])
        df["callsign"] = df["callsign"].str.strip()
        df.insert(0, "snapshot_time", body.get("time"))
        return df

    @dataset(key=["icao24", "first_seen"])
    def flights(self, begin, end) -> pd.DataFrame:
        """All flights in a time window (max 2 hours; needs auth)."""
        return self._flights("/flights/all", begin=begin, end=end)

    @dataset(key=["icao24", "first_seen"])
    def arrivals(self, airport: str, begin, end) -> pd.DataFrame:
        """Flights arriving at an ICAO airport in a time window (needs auth)."""
        return self._flights("/flights/arrival", airport=airport.upper(), begin=begin, end=end)

    @dataset(key=["icao24", "first_seen"])
    def departures(self, airport: str, begin, end) -> pd.DataFrame:
        """Flights departing an ICAO airport in a time window (needs auth)."""
        return self._flights("/flights/departure", airport=airport.upper(), begin=begin, end=end)
