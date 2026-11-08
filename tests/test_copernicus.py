"""Offline tests for the Copernicus vendor: HTTP is monkeypatched; normalization is real."""

import json

import pytest

from vendors.core import REGISTRY, get_vendor

# Registered here too so the test runs before/without the registry.py line.
REGISTRY.setdefault("copernicus", "vendors.copernicus:CopernicusVendor")

pytestmark = pytest.mark.vendor("copernicus")


class FakeResp:
    def __init__(self, payload=None, content=b"", status=200, headers=None):
        self._payload, self.content, self.status_code, self.headers = payload, content, status, headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size=1):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _item(i, cloud=None):
    props = {"datetime": f"2026-11-0{i}T00:00:00Z", "platform": "sentinel-1d",
             "sar:instrument_mode": "IW", "sar:polarizations": ["VV", "VH"]}
    if cloud is not None:
        props["eo:cloud_cover"] = cloud
    return {
        "id": f"S1_{i}", "collection": "sentinel-1-grd", "bbox": [0, 0, 1, 1],
        "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": props,
        "assets": {
            "thumbnail": {"href": "https://example.test/thumb.png", "type": "image/png"},
            "vv": {"href": "s3://eodata/vv.tiff", "alternate": {"https": {"href": "https://example.test/vv.tiff"}}},
        },
    }


def test_scenes_search_paginates_and_normalizes(tmp_path, monkeypatch):
    import vendors.copernicus as cop

    bodies = []
    pages = [
        {"features": [_item(1)], "links": [{"rel": "next", "href": f"{cop.STAC_URL}/search", "body": {"token": "n"}}]},
        {"features": [_item(2)], "links": []},
    ]
    monkeypatch.setattr(cop.requests, "post", lambda url, json=None, **k: (bodies.append(json), FakeResp(pages.pop(0)))[1])

    v = get_vendor("copernicus", data_dir=tmp_path)
    df = v.collect("scenes", aoi="cushing", preset="oil_tanks_sar", limit=5)
    assert df["id"].tolist() == ["S1_1", "S1_2"]
    assert (df["aoi"] == "cushing").all()
    first = bodies[0]
    assert first["collections"] == ["sentinel-1-grd"]
    assert first["bbox"] == [-96.80, 35.93, -96.70, 36.00]
    assert first["filter"]["args"] == [{"property": "sar:instrument_mode"}, "IW"]
    assert bodies[1] == {"token": "n"}  # follows the next link's body
    assets = json.loads(df["assets"].iloc[0])
    assert assets == {"thumbnail": "https://example.test/thumb.png", "vv": "https://example.test/vv.tiff"}
    assert json.loads(df["bbox"].iloc[0]) == [0, 0, 1, 1]
    assert len(v.load("scenes")) == 2


def test_scenes_optical_preset_sets_cloud_filter(tmp_path, monkeypatch):
    import vendors.copernicus as cop

    seen = {}
    monkeypatch.setattr(cop.requests, "post", lambda url, json=None, **k: (
        seen.update(json), FakeResp({"features": [_item(1, cloud=5.0)], "links": []}))[1])
    df = get_vendor("copernicus", data_dir=tmp_path).fetch(
        "scenes", bbox="1,2,3,4", preset="oil_tanks_optical", start="2026-10-01", end="2026-10-31")
    assert seen["collections"] == ["sentinel-2-l2a"]
    assert seen["filter"] == {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 20.0]}
    assert seen["datetime"] == "2026-10-01T00:00:00Z/2026-10-31T00:00:00Z"
    assert df["cloud_cover"].tolist() == [5.0]
    assert df["aoi"].isna().all()


def test_scenes_needs_aoi_or_bbox(tmp_path):
    v = get_vendor("copernicus", data_dir=tmp_path)
    with pytest.raises(ValueError):
        v.fetch("scenes")
    with pytest.raises(ValueError):
        v.fetch("scenes", aoi="nowhere")


def test_aois_lists_presets(tmp_path):
    df = get_vendor("copernicus", data_dir=tmp_path).collect("aois")
    assert "cushing" in df["name"].tolist()
    assert set(df.columns) >= {"name", "desc", "min_lon", "max_lat"}


def test_download_requires_credentials(tmp_path, monkeypatch):
    import vendors.copernicus as cop

    monkeypatch.delenv("CDSE_USERNAME", raising=False)
    monkeypatch.delenv("CDSE_PASSWORD", raising=False)
    with pytest.raises(cop.MissingCredentials):
        get_vendor("copernicus", data_dir=tmp_path).fetch("download", item_id="S1_1")


def test_download_saves_asset(tmp_path, monkeypatch):
    import vendors.copernicus as cop

    monkeypatch.setenv("CDSE_USERNAME", "u")
    monkeypatch.setenv("CDSE_PASSWORD", "p")
    monkeypatch.setattr(cop.requests, "post", lambda *a, **k: FakeResp({"access_token": "tok"}))
    calls = []

    def fake_get(url, headers=None, **k):
        calls.append((url, headers))
        if "/items/" in url:
            return FakeResp(_item(1))
        if "redirect" not in url:
            return FakeResp(status=302, headers={"Location": "https://example.test/redirect.png"})
        return FakeResp(content=b"PNGDATA")

    monkeypatch.setattr(cop.requests, "get", fake_get)
    v = get_vendor("copernicus", data_dir=tmp_path)
    df = v.collect("download", item_id="S1_1")
    assert df["bytes"].iloc[0] == 7
    assert (tmp_path / "files" / "sentinel-1-grd" / "S1_1" / "thumbnail.png").read_bytes() == b"PNGDATA"
    assert calls[-1][1]["Authorization"] == "Bearer tok"  # token kept across redirect
