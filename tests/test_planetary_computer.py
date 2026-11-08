"""Offline tests for the Planetary Computer vendor: HTTP is monkeypatched."""

import json

import pytest

from vendors.core.aoi import AOIS, resolve_bbox


pytestmark = pytest.mark.vendor("planetary_computer")

BLOB = "https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/14/S/QE/T14SQE_TCI_10m.tif"


def _item(i, collection="sentinel-2-l2a"):
    return {
        "id": f"S2B_{i}",
        "collection": collection,
        "bbox": [-97, 35, -96, 36],
        "geometry": {"type": "Polygon", "coordinates": [[[-97, 35], [-96, 35], [-96, 36], [-97, 35]]]},
        "properties": {"datetime": f"2026-11-0{i}T17:15:09Z", "eo:cloud_cover": 1.5, "platform": "sentinel-2b"},
        "assets": {
            "visual": {"href": BLOB, "roles": ["visual"]},
            "B04": {"href": BLOB.replace("TCI", "B04"), "roles": ["data"]},
            "AOT": {"href": BLOB.replace("TCI", "AOT"), "roles": ["data"]},
            "rendered_preview": {"href": "https://planetarycomputer.microsoft.com/api/data/v1/item/preview.png?x=1"},
        },
    }


@pytest.fixture
def pc(monkeypatch, tmp_path):
    import vendors.planetary_computer as m

    calls = {"post": [], "get": []}

    class Resp:
        def __init__(self, data=None, content=b""):
            self._data, self.content = data, content

        def json(self):
            return self._data

    def fake_get(url, **params):
        calls["get"].append(url)
        if "/sas/v1/token/" in url:
            return Resp({"token": "st=1&sig=abc"})
        if url.endswith("/collections"):
            return Resp({"collections": [{
                "id": "sentinel-2-l2a", "title": "Sentinel-2 Level-2A", "description": "Global imagery.\nMore.",
                "license": "proprietary", "keywords": ["ESA"],
                "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]]},
                           "temporal": {"interval": [["2015-06-27T10:25:31Z", None]]}},
            }]})
        if "/items/" in url:
            return Resp(_item(1))
        return Resp(content=b"PNGDATA")

    def fake_post(url, body):
        calls["post"].append((url, body))
        if len(calls["post"]) == 1:
            return {"features": [_item(1), _item(2)],
                    "links": [{"rel": "next", "href": url, "body": {**body, "token": "next:1"}}]}
        return {"features": [_item(3)], "links": []}

    monkeypatch.setattr(m, "_get", fake_get)
    monkeypatch.setattr(m, "_post", fake_post)
    return m.PlanetaryComputerVendor(data_dir=tmp_path), calls


def test_resolve_bbox():
    assert resolve_bbox("cushing") == AOIS["cushing"]["bbox"]
    assert resolve_bbox(bbox="1,2,3,4") == (1.0, 2.0, 3.0, 4.0)
    with pytest.raises(ValueError):
        resolve_bbox("nowhere")
    with pytest.raises(ValueError):
        resolve_bbox(bbox="1,2,3")


def test_scenes_search_paging_and_signing(pc):
    v, calls = pc
    df = v.collect("scenes", aoi="cushing", start="2026-10-01", end="2026-11-01", limit=3)
    assert df["id"].tolist() == ["S2B_1", "S2B_2", "S2B_3"]  # followed the next link
    assert list(df.columns[:5]) == ["id", "collection", "aoi", "datetime", "eo_cloud_cover"]
    assert df["aoi"].unique().tolist() == ["cushing"]
    body = calls["post"][0][1]
    assert body["bbox"] == list(AOIS["cushing"]["bbox"])
    assert body["datetime"] == "2026-10-01T00:00:00Z/2026-11-01T23:59:59Z"
    assert body["query"] == {"eo:cloud_cover": {"lt": 20}}
    assert calls["post"][1][1]["token"] == "next:1"

    assets = json.loads(df["assets"].iloc[0])
    assert set(assets) == {"visual", "B04", "rendered_preview"}  # AOT isn't a key band
    assert assets["visual"] == BLOB + "?st=1&sig=abc"
    assert "sig=" not in assets["rendered_preview"]  # data-API URL needs no SAS
    assert json.loads(df["geometry"].iloc[0])["type"] == "Polygon"
    assert sum("/token/sentinel2l2a01/sentinel2-l2" in u for u in calls["get"]) == 1  # token cached

    v.collect("scenes", aoi="cushing", limit=3)
    assert len(v.load("scenes")) == 3  # upsert on id


def test_scenes_presets_and_unsigned(pc):
    v, calls = pc
    df = v.fetch("scenes", bbox=(-1, -1, 1, 1), preset="oil_tanks_sar", limit=2, sign=False)
    body = calls["post"][0][1]
    assert body["collections"] == ["sentinel-1-rtc"] and "query" not in body  # SAR has no cloud cover
    assert len(df) == 2 and df["aoi"].isna().all()
    with pytest.raises(ValueError):
        v.fetch("scenes", aoi="cushing", preset="nightlights")


def test_scenes_multiple_aois(pc):
    v, calls = pc
    v.fetch("scenes", aoi="cushing,fujairah", limit=1)
    assert [b["bbox"] for _, b in calls["post"]] == [list(AOIS["cushing"]["bbox"]), list(AOIS["fujairah"]["bbox"])]


def test_collections(pc):
    v, _ = pc
    df = v.collect("collections")
    row = df.iloc[0]
    assert (row.id, row.description, row.start) == ("sentinel-2-l2a", "Global imagery.", "2015-06-27T10:25:31Z")
    assert json.loads(row.bbox) == [-180, -90, 180, 90]


def test_download_saves_signed_asset(pc, tmp_path):
    v, calls = pc
    df = v.collect("download", item_id="S2B_1", collection="sentinel-2-l2a", asset="visual")
    p = tmp_path / "files" / "sentinel-2-l2a" / "S2B_1" / "visual.tif"
    assert df["path"].iloc[0] == str(p) and p.read_bytes() == b"PNGDATA"
    assert calls["get"][-1] == BLOB + "?st=1&sig=abc"
    with pytest.raises(KeyError):
        v.fetch("download", item_id="S2B_1", asset="nope")
