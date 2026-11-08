"""Offline tests for the earth_search vendor: STAC calls are monkeypatched."""

import json

import pytest

from vendors.core import registry


def _feat(item_id, collection="sentinel-2-l2a", cloud=5.0, dt="2026-11-04T17:00:00Z", **assets):
    props = {"datetime": dt, "platform": "sentinel-2b", "grid:code": "MGRS-14SPE"}
    if cloud is not None:
        props["eo:cloud_cover"] = cloud
    return {
        "id": item_id, "collection": collection, "bbox": [-97.9, 35.1, -96.6, 36.1],
        "geometry": {"type": "Polygon", "coordinates": [[[-97.9, 35.1], [-96.6, 35.1], [-96.6, 36.1], [-97.9, 35.1]]]},
        "properties": props,
        "assets": {k: {"href": v} for k, v in assets.items()},
    }


@pytest.fixture
def vendor(tmp_path):
    return registry.get_vendor("earth_search", data_dir=tmp_path)


@pytest.fixture
def calls(monkeypatch):
    from vendors.earth_search import stac
    seen = []

    def search(body, max_items=None):
        seen.append(body)
        return [_feat("S2B_14SPE_20261104_0_L2A", visual="https://x/TCI.tif", thumbnail="https://x/preview.jpg",
                      red="https://x/B04.tif", nir="https://x/B08.tif", scl="https://x/SCL.tif")]

    monkeypatch.setattr(stac, "search", search)
    return seen


@pytest.mark.vendor("earth_search")
def test_scenes_normalization(vendor, calls):
    df = vendor.collect("scenes", aoi="cushing", start="2026-10-01", end="2026-11-01")
    row = df.iloc[0]
    assert (row.id, row.collection, row.eo_cloud_cover, row.s2_mgrs_tile, row.aoi) == (
        "S2B_14SPE_20261104_0_L2A", "sentinel-2-l2a", 5.0, "14SPE", "cushing")
    assert json.loads(row.assets) == {"visual": "https://x/TCI.tif", "thumbnail": "https://x/preview.jpg",
                                      "red": "https://x/B04.tif", "nir": "https://x/B08.tif"}
    assert json.loads(row.geometry)["type"] == "Polygon"
    body = calls[0]
    assert body["bbox"] == [-96.80, 35.93, -96.70, 36.00]
    assert body["datetime"] == "2026-10-01T00:00:00Z/2026-11-01T23:59:59Z"
    assert body["query"] == {"eo:cloud_cover": {"lt": 20}}
    vendor.collect("scenes", aoi="cushing")
    assert len(vendor.load("scenes")) == 1  # upserted on id


@pytest.mark.vendor("earth_search")
def test_scenes_presets_and_bbox(vendor, calls):
    vendor.fetch("scenes", bbox="1,2,3,4", preset="oil_tanks_sar")
    assert calls[-1]["collections"] == ["sentinel-1-grd"] and "query" not in calls[-1]
    vendor.fetch("scenes", aoi="rotterdam", preset="landsat")
    assert calls[-1]["collections"] == ["landsat-c2-l2"]
    with pytest.raises(ValueError):
        vendor.fetch("scenes", preset="nope", aoi="cushing")


@pytest.mark.vendor("earth_search")
def test_latest_covers_every_aoi(vendor, calls):
    from vendors.core.aoi import AOIS
    df = vendor.collect("latest")
    assert sorted(df["aoi"]) == sorted(AOIS)
    assert all(b["limit"] == 1 for b in calls)
    assert len(vendor.load("latest")) == len(AOIS)


@pytest.mark.vendor("earth_search")
def test_download_writes_file(vendor, monkeypatch, tmp_path):
    from vendors.earth_search import stac
    monkeypatch.setattr(stac, "get_item", lambda c, i: _feat(i, thumbnail="https://x/a/preview.jpg"))
    monkeypatch.setattr(stac, "download", lambda href: b"jpegbytes")
    df = vendor.collect("download", item_id="S2B_X")
    p = tmp_path / "files" / "sentinel-2-l2a" / "S2B_X" / "thumbnail_preview.jpg"
    assert df["path"].iloc[0] == str(p) and p.read_bytes() == b"jpegbytes"
    with pytest.raises(KeyError):
        vendor.fetch("download", item_id="S2B_X", asset="vv")


def test_https_href():
    from vendors.earth_search.stac import https_href
    assert https_href("s3://usgs-landsat/a/b.tif") == "https://usgs-landsat.s3.amazonaws.com/a/b.tif"
    assert https_href("https://x/y") == "https://x/y"
