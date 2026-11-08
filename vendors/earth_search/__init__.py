"""Element84 Earth Search: a keyless STAC API over public imagery on AWS.

Sentinel-2 L2A assets are public COGs over https. Landsat C2 L2 and
Sentinel-1 GRD assets sit in requester-pays S3 buckets, so ``download`` of
those needs AWS credentials (thumbnails included).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import urlparse

import pandas as pd

from vendors.core import Vendor, dataset
from vendors.core.aoi import AOIS, resolve_bbox

from . import stac

PRESETS: dict[str, dict] = {
    "oil_tanks_optical": {"collection": "sentinel-2-l2a", "max_cloud": 20},
    "oil_tanks_sar": {"collection": "sentinel-1-grd", "max_cloud": None},
    "landsat": {"collection": "landsat-c2-l2", "max_cloud": 20},
}
# Collections without eo:cloud_cover; a cloud filter would drop every item.
_NO_CLOUD = {"sentinel-1-grd", "cop-dem-glo-30", "cop-dem-glo-90", "naip"}
_ASSETS = ("visual", "thumbnail", "red", "green", "blue", "nir", "vv", "vh")


def _window(start: str | None, end: str | None, days: int = 30) -> str:
    """STAC datetime range; bare dates cover whole days, default is the last ``days`` days."""
    e = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = start or (date.fromisoformat(e[:10]) - timedelta(days=days)).isoformat()
    s = s if "T" in s else f"{s}T00:00:00Z"
    e = e if "T" in e else f"{e}T23:59:59Z"
    return f"{s}/{e}"


def _body(bbox, collection, start, end, max_cloud, limit, days=30) -> dict:
    body = {
        "collections": [collection],
        "bbox": list(bbox),
        "datetime": _window(start, end, days),
        "limit": min(int(limit), 250),
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    if max_cloud is not None and collection not in _NO_CLOUD:
        body["query"] = {"eo:cloud_cover": {"lt": max_cloud}}
    return body


def _row(feat: dict, aoi: str | None) -> dict:
    props = feat.get("properties", {})
    assets = feat.get("assets", {})
    hrefs = {}
    for name in _ASSETS:
        a = assets.get(name) or (assets.get("nir08") if name == "nir" else None)
        if a and a.get("href"):
            hrefs[name] = a["href"]
    grid = props.get("grid:code") or ""
    mgrs = props.get("s2:mgrs_tile") or (grid[5:] if grid.startswith("MGRS-") else None)
    return {
        "id": feat.get("id"),
        "collection": feat.get("collection"),
        "datetime": props.get("datetime"),
        "eo:cloud_cover": props.get("eo:cloud_cover"),
        "s2:mgrs_tile": mgrs,
        "platform": props.get("platform"),
        "aoi": aoi,
        "bbox": json.dumps(feat.get("bbox")),
        "geometry": json.dumps(feat.get("geometry")),
        "assets": json.dumps(hrefs),
    }


def _frame(features: list[dict], aoi: str | None) -> pd.DataFrame:
    return pd.DataFrame([_row(f, aoi) for f in features])


def _preset(preset: str | None, collection: str, max_cloud):
    if preset is None:
        return collection, max_cloud
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; presets: {sorted(PRESETS)}")
    p = PRESETS[preset]
    return p["collection"], p["max_cloud"]


class EarthSearchVendor(Vendor):
    name = "earth_search"
    description = "Element84 Earth Search STAC: Sentinel-2, Sentinel-1 and Landsat scenes on AWS"

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
        preset: str | None = None,
    ) -> pd.DataFrame:
        """STAC scenes over an AOI or bbox, newest first (default window: last 30 days)."""
        collection, max_cloud = _preset(preset, collection, max_cloud)
        box = resolve_bbox(aoi, bbox)
        feats = stac.search(_body(box, collection, start, end, max_cloud, limit), max_items=int(limit))
        return _frame(feats, aoi)

    @dataset(key=["aoi", "id"])
    def latest(
        self,
        aoi: str | None = None,
        preset: str = "oil_tanks_optical",
        days: int = 30,
    ) -> pd.DataFrame:
        """Newest low-cloud scene per preset AOI (all AOIs unless one is given)."""
        collection, max_cloud = _preset(preset, "", None)
        names = [aoi] if aoi else list(AOIS)
        frames = []
        for name in names:
            body = _body(resolve_bbox(name), collection, None, None, max_cloud, 1, days)
            frames.append(_frame(stac.search(body, max_items=1), name))
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @dataset(key=["path"])
    def download(
        self,
        item_id: str,
        collection: str = "sentinel-2-l2a",
        asset: str = "thumbnail",
    ) -> pd.DataFrame:
        """Downloads one asset of a scene to files/<collection>/<item_id>/."""
        item = stac.get_item(collection, item_id)
        assets = item.get("assets", {})
        if asset not in assets:
            raise KeyError(f"{item_id} has no asset {asset!r}; available: {sorted(assets)}")
        href = assets[asset]["href"]
        content = stac.download(href)
        fname = PurePosixPath(urlparse(href).path).name or asset
        path = self.storage.write_file(f"{collection}/{item_id}/{asset}_{fname}", content)
        return pd.DataFrame([{
            "path": str(path), "item_id": item_id, "collection": collection,
            "asset": asset, "href": href, "bytes": len(content),
        }])
