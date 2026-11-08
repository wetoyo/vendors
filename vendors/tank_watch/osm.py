"""Storage-tank outlines from OpenStreetMap (Overpass API, keyless)."""

from __future__ import annotations

import math
import time

import requests

MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
HEADERS = {"User-Agent": "vendors-client/0.1"}
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON_EQ = 111_320.0


def query_tanks(bbox: tuple[float, float, float, float], tries: int = 3) -> list[dict]:
    """Returns ``man_made=storage_tank`` ways in ``bbox`` (min_lon, min_lat, max_lon, max_lat) with geometry."""
    w, s, e, n = bbox
    q = f'[out:json][timeout:90];way["man_made"="storage_tank"]({s},{w},{n},{e});out geom tags;'
    last: Exception | None = None
    for attempt in range(tries):
        for url in MIRRORS:
            try:
                r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=120)
                if r.status_code == 200 and r.text.lstrip().startswith("{"):
                    return r.json().get("elements", [])
                last = RuntimeError(f"{url}: HTTP {r.status_code}")
            except requests.RequestException as ex:
                last = ex
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass unavailable: {last}")


def circle_fit(geometry: list[dict]) -> tuple[float, float, float]:
    """(center_lon, center_lat, diameter_m) of a tank outline, from its vertex centroid and mean radius."""
    pts = geometry[:-1] if len(geometry) > 3 and geometry[0] == geometry[-1] else geometry
    lat0 = sum(p["lat"] for p in pts) / len(pts)
    lon0 = sum(p["lon"] for p in pts) / len(pts)
    kx = M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))
    radii = [math.hypot((p["lon"] - lon0) * kx, (p["lat"] - lat0) * M_PER_DEG_LAT) for p in pts]
    return lon0, lat0, 2 * sum(radii) / len(radii)
