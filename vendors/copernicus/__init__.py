"""Copernicus Data Space Ecosystem (Sentinel-1/2) via its STAC API.

Scene search is keyless. ``download`` needs a free CDSE account: set
``CDSE_USERNAME`` and ``CDSE_PASSWORD``. Files land in ``data/copernicus/files/``.
"""

from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import pandas as pd
import requests

from vendors.core import Vendor, dataset
from vendors.core.aoi import AOIS, resolve_bbox

STAC_URL = "https://stac.dataspace.copernicus.eu/v1"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
HEADERS = {"User-Agent": "vendors-client/0.1"}
TIMEOUT = 60

# Search presets for the oil-tank use case. SAR (Sentinel-1 GRD, IW mode) sees
# through cloud and shows floating-roof shadows; Sentinel-2 L2A is optical.
PRESETS: dict[str, dict] = {
    "oil_tanks_sar": {"collection": "sentinel-1-grd", "instrument_mode": "IW"},
    "oil_tanks_optical": {"collection": "sentinel-2-l2a", "max_cloud": 20},
}


class MissingCredentials(RuntimeError):
    pass


def _iso(value: str | None, default: datetime) -> str:
    if not value:
        return default.strftime("%Y-%m-%dT%H:%M:%SZ")
    if "T" in value:
        return value
    return f"{value}T00:00:00Z"


def _https_href(asset: dict) -> str | None:
    """The asset's HTTPS link (CDSE lists S3 paths first for data bands)."""
    href = asset.get("href")
    if href and href.startswith("http"):
        return href
    alt = (asset.get("alternate") or {}).get("https") or {}
    return alt.get("href") or href


def _filter(instrument_mode: str | None, max_cloud: float | None) -> dict | None:
    args = []
    if instrument_mode:
        args.append({"op": "=", "args": [{"property": "sar:instrument_mode"}, instrument_mode]})
    if max_cloud is not None:
        args.append({"op": "<=", "args": [{"property": "eo:cloud_cover"}, float(max_cloud)]})
    if not args:
        return None
    return args[0] if len(args) == 1 else {"op": "and", "args": args}


def _token() -> str:
    user, pw = os.environ.get("CDSE_USERNAME"), os.environ.get("CDSE_PASSWORD")
    if not user or not pw:
        raise MissingCredentials("Set CDSE_USERNAME and CDSE_PASSWORD (free account at dataspace.copernicus.eu)")
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "password", "username": user, "password": pw, "client_id": "cdse-public"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _get_with_auth(url: str, token: str, max_redirects: int = 5) -> requests.Response:
    """GET that keeps the bearer token across redirects (requests drops it on host change)."""
    headers = {**HEADERS, "Authorization": f"Bearer {token}"}
    for _ in range(max_redirects + 1):
        r = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=False, stream=True)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            url = r.headers["Location"]
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"too many redirects fetching {url}")


class CopernicusVendor(Vendor):
    name = "copernicus"
    description = "Copernicus Data Space Sentinel-1 SAR / Sentinel-2 optical scenes (STAC)"

    @dataset(key=["id"])
    def scenes(
        self,
        aoi: str | None = None,
        bbox: str | tuple | None = None,
        collection: str | None = None,
        start: str | None = None,
        end: str | None = None,
        max_cloud: float | None = None,
        limit: int = 50,
        preset: str | None = None,
        instrument_mode: str | None = None,
    ) -> pd.DataFrame:
        """Sentinel scenes over an AOI or bbox (STAC item search, default last 30 days)."""
        if preset is not None:
            if preset not in PRESETS:
                raise ValueError(f"unknown preset {preset!r}; presets: {sorted(PRESETS)}")
            p = PRESETS[preset]
            collection = collection or p.get("collection")
            instrument_mode = instrument_mode or p.get("instrument_mode")
            max_cloud = p.get("max_cloud") if max_cloud is None else max_cloud
        collection = collection or "sentinel-1-grd"
        box = resolve_bbox(aoi, bbox)
        now = datetime.now(timezone.utc)
        end_s = _iso(end, now)
        start_s = _iso(start, now - timedelta(days=30))

        body: dict = {
            "collections": [collection],
            "bbox": list(box),
            "datetime": f"{start_s}/{end_s}",
            "limit": min(int(limit), 1000),
            "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        }
        flt = _filter(instrument_mode, max_cloud)
        if flt:
            body["filter-lang"] = "cql2-json"
            body["filter"] = flt

        features: list[dict] = []
        url = f"{STAC_URL}/search"
        while body is not None and len(features) < limit:
            r = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            page = r.json()
            features.extend(page.get("features", []))
            nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
            if not nxt or not page.get("features"):
                break
            url, body = nxt.get("href", url), nxt.get("body")

        rows = []
        for f in features[:limit]:
            props = f.get("properties", {})
            rows.append({
                "id": f.get("id"),
                "collection": f.get("collection") or collection,
                "datetime": props.get("datetime"),
                "platform": props.get("platform"),
                "cloud_cover": props.get("eo:cloud_cover"),
                "instrument_mode": props.get("sar:instrument_mode"),
                "polarizations": props.get("sar:polarizations"),
                "orbit_state": props.get("sat:orbit_state"),
                "bbox": json.dumps(f.get("bbox")),
                "geometry": json.dumps(f.get("geometry")),
                "assets": json.dumps({k: _https_href(a) for k, a in (f.get("assets") or {}).items()}),
                "aoi": aoi,
            })
        return pd.DataFrame(rows)

    @dataset(key=["name"])
    def aois(self) -> pd.DataFrame:
        """Preset areas of interest (oil storage hubs) usable as aoi=."""
        return pd.DataFrame([
            {"name": n, "desc": a["desc"], "min_lon": a["bbox"][0], "min_lat": a["bbox"][1],
             "max_lon": a["bbox"][2], "max_lat": a["bbox"][3]}
            for n, a in AOIS.items()
        ])

    @dataset(key=["path"])
    def download(self, item_id: str, collection: str = "sentinel-1-grd", asset: str = "thumbnail") -> pd.DataFrame:
        """Downloads one asset of a scene to files/<collection>/<item_id>/ (needs CDSE creds)."""
        token = _token()
        r = requests.get(f"{STAC_URL}/collections/{collection}/items/{item_id}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        assets = r.json().get("assets", {})
        if asset not in assets:
            raise KeyError(f"{item_id} has no asset {asset!r}; available: {sorted(assets)}")
        a = assets[asset]
        href = _https_href(a)
        if not href or not href.startswith("http"):
            raise ValueError(f"asset {asset!r} has no HTTPS link: {href}")

        local = a.get("file:local_path")
        suffix = PurePosixPath(local).suffix if local else ""
        suffix = suffix or mimetypes.guess_extension((a.get("type") or "").split(";")[0].strip()) or ""
        path = self.storage.path(collection, item_id, f"{asset}{suffix}")
        size = 0
        with _get_with_auth(href, token) as resp, open(path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):  # products can be GBs; stream to disk
                fh.write(chunk)
                size += len(chunk)
        return pd.DataFrame([{
            "path": str(path), "item_id": item_id, "collection": collection, "asset": asset,
            "href": href, "bytes": size,
        }])
