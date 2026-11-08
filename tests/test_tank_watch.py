import numpy as np
import pandas as pd
import pytest

from vendors.core import get_vendor
from vendors.tank_watch import model
from vendors.tank_watch.osm import circle_fit

pytestmark = pytest.mark.vendor("tank_watch")


def _synthetic_tank(fill, elev=30.0, az=160.0, radius=31.0, height=14.6, k=0.3, noise=0.0, seed=0):
    rows, cols, pts = model.interior_pixels(500.0, 500.0, radius, 400.0, 600.0, 10.0, (20, 20))
    shaded = model.shaded_fractions(pts, radius, np.array([(1 - fill) * height]), elev, az)[0]
    roof = np.array([0.20, 0.22, 0.25, 0.30])
    vals = roof[None, :] * (1 - (1 - k) * shaded)[:, None]
    vals += np.random.default_rng(seed).normal(0, noise, vals.shape)
    return {"osm_id": 1, "values": vals, "pts": pts, "radius": radius}


def test_interior_pixels_stay_inside_the_tank():
    rows, cols, pts = model.interior_pixels(500.0, 500.0, 31.0, 400.0, 600.0, 10.0, (20, 20))
    assert 15 <= len(rows) <= 30
    assert np.hypot(pts[..., 0], pts[..., 1]).max() < 31.0


def test_empty_tank_shadow_points_away_from_sun():
    _, _, pts = model.interior_pixels(500.0, 500.0, 31.0, 400.0, 600.0, 10.0, (20, 20))
    sh = model.shaded_fractions(pts, 31.0, np.array([14.6]), 30.0, 180.0)[0]  # sun due south
    y = pts[:, :, 1].mean(axis=1)
    assert sh[y < 0].mean() > sh[y > 0].mean()  # south side of the roof is shaded


@pytest.mark.parametrize("fill", [0.1, 0.5, 0.8])  # the top ~10% is below 10 m resolution at 30 deg sun
def test_estimate_recovers_fill(fill):
    out = model.estimate_scene([_synthetic_tank(fill, noise=0.003)] * 3, 30.0, 160.0, 14.6, apparent_m=0.0)
    assert abs(out[0]["fill_pct"] - 100 * fill) <= 7.5
    assert out[0]["fill_lo_pct"] <= out[0]["fill_pct"] <= out[0]["fill_hi_pct"]


def test_circle_fit_diameter():
    lat0, lon0, r = 36.0, -96.76, 30.0
    ring = [{"lat": lat0 + r * np.sin(a) / 110540, "lon": lon0 + r * np.cos(a) / (111320 * np.cos(np.radians(lat0)))}
            for a in np.linspace(0, 2 * np.pi, 33)]
    lon, lat, d = circle_fit(ring)
    assert abs(d - 60) < 0.5 and abs(lat - lat0) < 1e-5 and abs(lon - lon0) < 1e-5


def test_tanks_from_overpass(tmp_path, monkeypatch):
    ring = [{"lat": 36 + 30 * np.sin(a) / 110540, "lon": -96.76 + 30 * np.cos(a) / 89500}
            for a in np.linspace(0, 2 * np.pi, 17)]
    small =[{"lat": 36.01 + (p["lat"] - 36) / 4, "lon": -96.76 + (p["lon"] + 96.76) / 4} for p in ring]
    monkeypatch.setattr("vendors.tank_watch.query_tanks", lambda bbox: [
        {"id": 1, "geometry": ring, "tags": {"man_made": "storage_tank", "operator": "Acme"}},
        {"id": 2, "geometry": small, "tags": {}},
    ])
    df = get_vendor("tank_watch", data_dir=tmp_path).collect("tanks", aoi="cushing")
    assert df["osm_id"].tolist() == [1]
    assert 250_000 < df["capacity_bbl"].iloc[0] < 280_000  # 60 m x 14.6 m shell
    assert df["operator"].iloc[0] == "Acme"


def test_site_fill_is_capacity_weighted(tmp_path):
    v = get_vendor("tank_watch", data_dir=tmp_path)
    v.storage.write("tanks", pd.DataFrame({"osm_id": [1, 2], "aoi": "cushing", "capacity_bbl": [300_000, 100_000]}),
                    key=["osm_id"])
    v.storage.write("fill_levels", pd.DataFrame({
        "osm_id": [1, 2], "scene_id": "S", "date": "2026-01-03", "fill_pct": [50.0, 10.0],
        "confident": [True, True], "sun_elevation": 29.3}), key=["osm_id", "scene_id"])
    df = v.collect("site_fill", aoi="cushing", min_tanks=1)
    assert df["fill_pct"].iloc[0] == 40.0
    assert df["est_stock_bbl"].iloc[0] == 160_000


def test_apparent_shadow_offset_is_not_read_as_depth():
    # A tank whose only "shadow" is the constant edge blur should read full.
    _, _, pts = model.interior_pixels(500.0, 500.0, 31.0, 400.0, 600.0, 10.0, (20, 20))
    sh = model.shaded_fractions(pts, 31.0, np.array([0.0]), 60.0, 160.0, apparent_m=model.APPARENT_SHADOW_M)[0]
    vals = np.array([0.2, 0.22, 0.25, 0.3])[None, :] * (1 - 0.7 * sh)[:, None]
    t = {"osm_id": 1, "values": vals, "pts": pts, "radius": 31.0}
    assert model.estimate_scene([t], 60.0, 160.0, 14.6)[0]["fill_pct"] == 100.0
