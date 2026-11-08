"""Microsoft Planetary Computer: STAC search over Sentinel-1/2, Landsat and more.

Keyless. Blob asset hrefs are SAS-signed with tokens from the PC SAS API
(plain requests, no ``planetary-computer`` package). Set
``PC_SDK_SUBSCRIPTION_KEY`` for higher rate limits. Downloaded assets are saved
under ``data/planetary_computer/files/<collection>/<item_id>/``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pandas as pd
import requests

from vendors.core import Vendor, dataset
from vendors.core.aoi import AOIS, resolve_bbox

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1"
USER_AGENT = "vendors-client/0.1"

# preset -> search settings. No VIIRS night-lights collection exists on PC, so
# there is no "nightlights" preset.
PRESETS: dict[str, dict] = {
    "oil_tanks_optical": {"collection": "sentinel-2-l2a", "max_cloud": 20},
    "oil_tanks_sar": {"collection": "sentinel-1-rtc", "max_cloud": None},
    "landsat": {"collection": "landsat-c2-l2", "max_cloud": 20},
}

# Assets kept (and signed) per collection; other collections keep visual/data assets.
KEY_ASSETS: dict[str, list[str]] = {
    "sentinel-2-l2a": ["visual", "rendered_preview", "B02", "B03", "B04", "B08", "B11", "B12", "SCL"],
    "sentinel-1-rtc": ["vv", "vh", "rendered_preview"],
    "landsat-c2-l2": ["red", "green", "blue", "nir08", "swir16", "swir22", "qa_pixel", "rendered_preview"],
}

# Collections without eo:cloud_cover, where a cloud filter would drop every item.
NO_CLOUD = {"sentinel-1-rtc", "sentinel-1-grd"}


def _headers() -> dict:
    h = {"User-Agent": USER_AGENT}
    key = os.environ.get("PC_SDK_SUBSCRIPTION_KEY")
    if key:
        h["Ocp-Apim-Subscription-Key"] = key
    return h


def _get(url: str, **params) -> requests.Response:
    r = requests.get(url, params=params or None, headers=_headers(), timeout=60)
    r.raise_for_status()
    return r


def _post(url: str, body: dict) -> dict:
    r = requests.post(url, json=body, headers=_headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def _aoi_list(aoi: str | None) -> list[str | None]:
    if aoi is None:
        return [None]
    if aoi == "all":
        return list(AOIS)
    return [a.strip() for a in aoi.split(",") if a.strip()]


def _pick_assets(collection: str, assets: dict) -> dict:
    wanted = KEY_ASSETS.get(collection)
    if wanted:
        return {k: assets[k] for k in wanted if k in assets}
    return {
        k: a for k, a in assets.items()
        if k in ("visual", "rendered_preview") or "data" in (a.get("roles") or [])
    }


class PlanetaryComputerVendor(Vendor):
    name = "planetary_computer"
    description = "Microsoft Planetary Computer STAC: Sentinel-1/2 and Landsat scenes over preset AOIs"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tokens: dict[tuple[str, str], str] = {}

    # ---------------------------------------------------------------- signing
    def sign(self, href: str) -> str:
        """Appends a SAS token to an Azure blob href; other hrefs pass through."""
        u = urlparse(href)
        if not u.netloc.endswith(".blob.core.windows.net") or "sig=" in u.query:
            return href
        account = u.netloc.split(".")[0]
        container = u.path.lstrip("/").split("/")[0]
        if (account, container) not in self._tokens:
            self._tokens[(account, container)] = _get(f"{SAS_URL}/token/{account}/{container}").json()["token"]
        token = self._tokens[(account, container)]
        return f"{href}{'&' if u.query else '?'}{token}"

    # --------------------------------------------------------------- datasets
    @dataset(key=["id"])
    def scenes(
        self,
        aoi: str | None = None,
        bbox: str | tuple | None = None,
        collection: str = "sentinel-2-l2a",
        start: str | None = None,
        end: str | None = None,
        max_cloud: float | None = 20,
        limit: int = 50,
        sign: bool = True,
        preset: str | None = None,
    ) -> pd.DataFrame:
        """STAC scenes over an AOI preset (or bbox) in a date window, with signed asset hrefs."""
        if preset:
            if preset not in PRESETS:
                raise ValueError(f"unknown preset {preset!r}; presets: {sorted(PRESETS)}")
            collection = PRESETS[preset]["collection"]
            max_cloud = PRESETS[preset]["max_cloud"]
        end_dt = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start) if start else end_dt - timedelta(days=30)
        window = f"{start_dt.date().isoformat()}T00:00:00Z/{end_dt.date().isoformat()}T23:59:59Z"

        rows = []
        for name in _aoi_list(aoi) if bbox is None else [aoi]:
            box = resolve_bbox(name, bbox)
            body: dict = {
                "collections": [collection],
                "bbox": list(box),
                "datetime": window,
                "limit": min(int(limit), 100),
                "sortby": [{"field": "datetime", "direction": "desc"}],
            }
            if max_cloud is not None and collection not in NO_CLOUD:
                body["query"] = {"eo:cloud_cover": {"lt": max_cloud}}
            got = 0
            url = f"{STAC_URL}/search"
            while url and got < limit:
                page = _post(url, body)
                for f in page.get("features", []):
                    if got >= limit:
                        break
                    rows.append(self._row(f, collection, name, sign))
                    got += 1
                nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
                url = nxt.get("href") if nxt else None
                if nxt and nxt.get("body"):
                    body = nxt["body"]
        return pd.DataFrame(rows)

    def _row(self, f: dict, collection: str, aoi: str | None, sign: bool) -> dict:
        props = f.get("properties", {})
        assets = {
            k: (self.sign(a["href"]) if sign else a["href"])
            for k, a in _pick_assets(f.get("collection") or collection, f.get("assets", {})).items()
        }
        return {
            "id": f["id"],
            "collection": f.get("collection") or collection,
            "aoi": aoi,
            "datetime": props.get("datetime"),
            "eo:cloud_cover": props.get("eo:cloud_cover"),
            "platform": props.get("platform"),
            "bbox": json.dumps(f.get("bbox")),
            "geometry": json.dumps(f.get("geometry")),
            "assets": json.dumps(assets),
        }

    @dataset(key=["id"])
    def collections(self) -> pd.DataFrame:
        """Collections available in the Planetary Computer STAC catalog."""
        data = _get(f"{STAC_URL}/collections").json()
        rows = []
        for c in data.get("collections", []):
            ext = c.get("extent", {})
            interval = (ext.get("temporal", {}).get("interval") or [[None, None]])[0]
            rows.append({
                "id": c["id"],
                "title": c.get("title"),
                "description": (c.get("description") or "").split("\n")[0][:500],
                "license": c.get("license"),
                "keywords": json.dumps(c.get("keywords") or []),
                "start": interval[0],
                "end": interval[1],
                "bbox": json.dumps((ext.get("spatial", {}).get("bbox") or [None])[0]),
            })
        return pd.DataFrame(rows)

    @dataset(key=["path"])
    def download(self, item_id: str, collection: str = "sentinel-2-l2a", asset: str = "rendered_preview") -> pd.DataFrame:
        """Downloads one signed item asset to files/<collection>/<item_id>/."""
        item = _get(f"{STAC_URL}/collections/{collection}/items/{item_id}").json()
        assets = item.get("assets", {})
        if asset not in assets:
            raise KeyError(f"{item_id} has no asset {asset!r}; available: {sorted(assets)}")
        href = assets[asset]["href"]
        content = _get(self.sign(href)).content
        ext = os.path.splitext(urlparse(href).path)[1] or (".png" if asset == "rendered_preview" else ".bin")
        if asset == "rendered_preview" and ext not in (".png", ".jpg", ".jpeg"):
            ext = ".png"
        path = self.storage.write_file(f"{collection}/{item_id}/{asset}{ext}", content)
        return pd.DataFrame([{
            "path": str(path), "item_id": item_id, "collection": collection, "asset": asset,
            "href": href, "bytes": len(content),
            "datetime": item.get("properties", {}).get("datetime"),
        }])
