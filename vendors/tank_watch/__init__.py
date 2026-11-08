"""Oil-tank fill levels from Sentinel-2 floating-roof shadows.

Tank outlines come from OpenStreetMap; imagery is Sentinel-2 L2A from Earth
Search (keyless, public COGs). For each clear scene, every tank's pixels are fit
with the shadow model in :mod:`vendors.tank_watch.model`. At 10 m pixels this is a
statistical estimate: individual tanks are noisy, the capacity-weighted site
total is the useful number. Low sun (October to March at Cushing) gives long
shadows and the best estimates; near midsummer the shadow of an empty tank is
under one pixel, so those rows are flagged ``confident = False``.

Needs ``rasterio`` (``pip install .[tank_watch]``).
"""

from __future__ import annotations

import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd

from vendors.core import Vendor, dataset
from vendors.core.aoi import AOIS, resolve_bbox
from vendors.earth_search import stac

from . import model
from .osm import circle_fit, query_tanks

log = logging.getLogger(__name__)

BANDS = ("blue", "green", "red", "nir")  # all 10 m
BAD_SCL = {0, 1, 3, 8, 9, 10, 11}  # nodata, saturated, cloud shadow, clouds, cirrus, snow
BBL_PER_M3 = 6.28981
DEFAULT_HEIGHT_M = 14.6  # 48 ft shell, typical of Cushing's large crude tanks
AOI_PAD_DEG = 0.02
MIN_SHADOW_M = 4.0  # below this (sun above ~75 deg) the fit has too little shadow to work with


def _bbox(aoi, bbox):
    w, s, e, n = resolve_bbox(aoi, bbox)
    if bbox is None:
        w, s, e, n = w - AOI_PAD_DEG, s - AOI_PAD_DEG, e + AOI_PAD_DEG, n + AOI_PAD_DEG
    return w, s, e, n


def _rasterio():
    try:
        import rasterio
    except ImportError as e:  # pragma: no cover
        raise ImportError("tank_watch needs rasterio: pip install rasterio") from e
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
    os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    logging.getLogger("rasterio").setLevel(logging.WARNING)
    return rasterio


def read_scene(item: dict, tanks: pd.DataFrame) -> tuple[list[dict], dict]:
    """Reads the AOI window of one Sentinel-2 item and returns per-tank pixel blocks."""
    rasterio = _rasterio()
    from rasterio.enums import Resampling
    from rasterio.warp import transform
    from rasterio.windows import from_bounds

    hrefs = {b: stac.https_href(item["assets"][b]["href"]) for b in (*BANDS, "scl")}
    with rasterio.open(hrefs["red"]) as ref:
        xs, ys = transform("EPSG:4326", ref.crs, tanks["lon"].tolist(), tanks["lat"].tolist())
        xs, ys = np.asarray(xs), np.asarray(ys)
        pad = float(tanks["diameter_m"].max())
        win = from_bounds(xs.min() - pad, ys.min() - pad, xs.max() + pad, ys.max() + pad, ref.transform)
        win = win.round_offsets().round_lengths()
        wt = ref.window_transform(win)
        res = wt.a
        x0, y0 = wt.c, wt.f
        shape = (int(win.height), int(win.width))
    stack = []
    for b in BANDS:
        with rasterio.open(hrefs[b]) as src:
            stack.append(src.read(1, window=win, boundless=True, fill_value=0).astype("float32"))
    with rasterio.open(hrefs["scl"]) as src:  # 20 m, resample onto the 10 m window
        scl_win = from_bounds(x0, y0 - shape[0] * res, x0 + shape[1] * res, y0, src.transform)
        scl = src.read(1, window=scl_win, out_shape=shape, boundless=True, fill_value=0,
                       resampling=Resampling.nearest)
    cube = np.stack(stack, axis=-1)  # (H, W, B)
    # L2A reflectance: DN/10000, minus the 1000 offset for processing baseline >= 04.00.
    offset = 1000.0 if str(item["properties"].get("s2:processing_baseline", "05.00")) >= "04.00" else 0.0
    cube = (cube - offset) / 10000.0
    blocks = []
    for (_, t), cx, cy in zip(tanks.iterrows(), xs, ys):
        rows, cols, pts = model.interior_pixels(cx, cy, t["diameter_m"] / 2, x0, y0, res, shape)
        if len(rows) < 6:
            continue
        if np.isin(scl[rows, cols], list(BAD_SCL)).any() or (cube[rows, cols] <= -0.05).any():
            continue
        blocks.append({"osm_id": t["osm_id"], "values": cube[rows, cols].astype("float64"),
                       "pts": pts, "radius": t["diameter_m"] / 2})
    return blocks, {"res": res}


class TankWatchVendor(Vendor):
    name = "tank_watch"
    description = "Oil-tank fill levels from Sentinel-2 floating-roof shadows (OSM tanks, Earth Search imagery)"

    @dataset(key=["osm_id"])
    def tanks(self, aoi: str = "cushing", bbox=None, min_diameter_m: float = 40.0,
              height_m: float = DEFAULT_HEIGHT_M) -> pd.DataFrame:
        """Storage tanks from OpenStreetMap with diameter and estimated shell capacity."""
        rows = []
        for w in query_tanks(_bbox(aoi, bbox)):
            if not w.get("geometry"):
                continue
            lon, lat, d = circle_fit(w["geometry"])
            if d < min_diameter_m:
                continue
            tags = w.get("tags", {})
            rows.append({
                "osm_id": w["id"], "aoi": aoi if bbox is None else None, "lon": lon, "lat": lat,
                "diameter_m": round(d, 1), "height_m": height_m,
                "capacity_bbl": round(math.pi * (d / 2) ** 2 * height_m * BBL_PER_M3),
                "operator": tags.get("operator"), "content": tags.get("content") or tags.get("substance"),
                "name": tags.get("name"),
            })
        return pd.DataFrame(rows)

    @dataset(key=["osm_id", "scene_id"])
    def fill_levels(self, aoi: str = "cushing", start=None, end=None, max_cloud: float = 30.0,
                    min_diameter_m: float = 40.0, height_m: float = DEFAULT_HEIGHT_M,
                    limit: int = 5000, workers: int = 4) -> pd.DataFrame:
        """Per-tank fill estimate for every clear Sentinel-2 scene over the AOI."""
        tanks = self.load("tanks")
        if not tanks.empty and "aoi" in tanks:
            tanks = tanks[(tanks["aoi"] == aoi) & (tanks["diameter_m"] >= min_diameter_m)]
        if tanks.empty:
            tanks = self.collect("tanks", aoi=aoi, min_diameter_m=min_diameter_m, height_m=height_m)
        end = end or date.today().isoformat()
        start = start or (date.fromisoformat(str(end)[:10]) - timedelta(days=365)).isoformat()
        w, s, e, n = resolve_bbox(aoi)
        items = stac.search({
            "collections": ["sentinel-2-l2a"], "bbox": [w, s, e, n],
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud}}, "limit": 100,
        }, max_items=limit)
        # Cushing straddles two MGRS tiles; keep one item per date, preferring the tile with more scenes.
        tile_counts = pd.Series([i["properties"].get("grid:code") for i in items]).value_counts()
        items.sort(key=lambda i: -tile_counts.get(i["properties"].get("grid:code"), 0))
        by_date: dict[str, dict] = {}
        for it in items:
            by_date.setdefault(it["properties"]["datetime"][:10], it)
        log.info("tank_watch: %d scenes, %d tanks", len(by_date), len(tanks))

        def one(item):
            p = item["properties"]
            try:
                blocks, _ = read_scene(item, tanks)
            except Exception as ex:  # one bad scene shouldn't stop the run
                log.warning("skip %s: %s", item["id"], ex)
                return []
            fits = model.estimate_scene(blocks, p["view:sun_elevation"], p["view:sun_azimuth"], height_m)
            max_shadow = height_m / math.tan(math.radians(p["view:sun_elevation"]))
            for f in fits:
                f.update({
                    "scene_id": item["id"], "date": p["datetime"][:10], "datetime": p["datetime"],
                    "tile": p.get("grid:code"), "sun_elevation": round(p["view:sun_elevation"], 2),
                    "sun_azimuth": round(p["view:sun_azimuth"], 2), "max_shadow_m": round(max_shadow, 1),
                    "scene_cloud_pct": p.get("eo:cloud_cover"),
                    "confident": bool(max_shadow >= MIN_SHADOW_M and f["n_px"] >= 10),
                })
            return fits

        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = [r for fits in pool.map(one, list(by_date.values())) for r in fits]
        return pd.DataFrame(rows)

    @dataset(key=["aoi", "date"])
    def site_fill(self, aoi: str = "cushing", min_tanks: int = 30, confident_only: bool = True) -> pd.DataFrame:
        """Capacity-weighted site fill per scene date, from stored tanks and fill_levels."""
        tanks = self.load("tanks")
        fills = self.load("fill_levels")
        if tanks.empty or fills.empty:
            return pd.DataFrame()
        tanks = tanks[tanks["aoi"] == aoi]
        df = fills.merge(tanks[["osm_id", "capacity_bbl"]], on="osm_id")
        if confident_only:
            df = df[df["confident"].astype(bool)]
        total_cap = float(tanks["capacity_bbl"].sum())
        rows = []
        for d, g in df.groupby("date"):
            if len(g) < min_tanks:
                continue
            wts = g["capacity_bbl"]
            fill = float((g["fill_pct"] * wts).sum() / wts.sum())
            rows.append({
                "aoi": aoi, "date": d, "n_tanks": len(g), "tanks_total": len(tanks),
                "coverage_pct": round(100 * wts.sum() / total_cap, 1),
                "fill_pct": round(fill, 2),
                "fill_median_pct": round(float(g["fill_pct"].median()), 1),
                "est_stock_bbl": round(fill / 100 * total_cap),
                "shell_capacity_bbl": round(total_cap),
                "sun_elevation": float(g["sun_elevation"].iloc[0]),
                "scene_id": g["scene_id"].iloc[0],
            })
        return pd.DataFrame(rows)
