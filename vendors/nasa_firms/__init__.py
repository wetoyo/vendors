"""NASA FIRMS active-fire hotspots (VIIRS/MODIS), for refinery and flare activity.

Set ``FIRMS_MAP_KEY`` (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/).
"""

from __future__ import annotations

import io
import os

import pandas as pd
import requests

from vendors.core import Vendor, dataset
from vendors.core.aoi import AOIS, resolve_bbox

BASE = "https://firms.modaps.eosdis.nasa.gov/api"
KEY = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]
PAD = 0.05  # degrees added around preset AOIs, which are tight site extents


class MissingMapKey(RuntimeError):
    pass


def _map_key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        raise MissingMapKey(
            "Set FIRMS_MAP_KEY (free key: https://firms.modaps.eosdis.nasa.gov/api/map_key/)"
        )
    return key


def _get_csv(path: str) -> pd.DataFrame:
    """GETs a FIRMS CSV endpoint. FIRMS reports errors as plain text with HTTP 200."""
    resp = requests.get(f"{BASE}/{path}", timeout=60)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        return pd.DataFrame()
    first = text.splitlines()[0]
    if "," not in first:
        raise RuntimeError(f"FIRMS error: {text[:200]}")
    return pd.read_csv(io.StringIO(text), dtype={"acq_time": str})


def _days(days: int) -> int:
    days = int(days)
    if not 1 <= days <= 10:
        raise ValueError("days must be 1-10")
    return days


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df["acq_time"] = df["acq_time"].astype(str).str.zfill(4)
    df["acq_datetime"] = pd.to_datetime(
        df["acq_date"].astype(str) + " " + df["acq_time"], format="%Y-%m-%d %H%M", utc=True
    )
    return df.drop_duplicates(subset=[c for c in KEY if c in df.columns])


class NasaFirmsVendor(Vendor):
    name = "nasa_firms"
    description = "NASA FIRMS VIIRS/MODIS active-fire hotspots"

    def _area(self, bbox: tuple, source: str, days: int, date: str | None) -> pd.DataFrame:
        west, south, east, north = (round(float(v), 4) for v in bbox)
        path = f"area/csv/{_map_key()}/{source}/{west},{south},{east},{north}/{_days(days)}"
        return _get_csv(f"{path}/{date}" if date else path)

    @dataset(key=KEY)
    def hotspots(
        self,
        aoi: str | None = None,
        bbox: str | tuple | None = None,
        source: str = "VIIRS_SNPP_NRT",
        days: int = 1,
        date: str | None = None,
    ) -> pd.DataFrame:
        """Fire detections in a preset AOI (padded 0.05 deg) or a custom bbox."""
        box = resolve_bbox(aoi, bbox)
        if bbox is None:
            box = (box[0] - PAD, box[1] - PAD, box[2] + PAD, box[3] + PAD)
        df = _normalize(self._area(box, source, days, date))
        if not df.empty:
            df.insert(0, "aoi", aoi if bbox is None else None)
            df["source"] = source
        return df

    @dataset(key=KEY)
    def site_scan(self, source: str = "VIIRS_SNPP_NRT", days: int = 1) -> pd.DataFrame:
        """Fire detections across every preset AOI."""
        frames = [self.hotspots(aoi=name, source=source, days=days) for name in AOIS]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).drop_duplicates(subset=KEY)

    @dataset(key=KEY)
    def country(
        self, country: str = "USA", source: str = "VIIRS_SNPP_NRT", days: int = 1, date: str | None = None
    ) -> pd.DataFrame:
        """Fire detections in one country (ISO3 code)."""
        path = f"country/csv/{_map_key()}/{source}/{country}/{_days(days)}"
        df = _normalize(_get_csv(f"{path}/{date}" if date else path))
        if not df.empty:
            df["source"] = source
        return df

    @dataset(key=["data_id"])
    def data_availability(self, sensor: str = "ALL") -> pd.DataFrame:
        """Date range available for each FIRMS source."""
        return _get_csv(f"data_availability/csv/{_map_key()}/{sensor}")
