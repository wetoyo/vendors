"""Offline tests for the NASA FIRMS vendor: HTTP is monkeypatched, CSV parsing is real."""

import pytest

from vendors.core import REGISTRY

# Until the main registry lists it, register locally so the vendor marker resolves.
REGISTRY.setdefault("nasa_firms", "vendors.nasa_firms:NasaFirmsVendor")

from vendors import get_vendor  # noqa: E402
from vendors.nasa_firms import MissingMapKey  # noqa: E402

VIIRS_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
35.95,-96.75,330.1,0.39,0.36,2026-11-07,0812,N,VIIRS,n,2.0NRT,290.2,5.4,N
35.97,-96.72,345.6,0.40,0.37,2026-11-07,1930,N,VIIRS,h,2.0NRT,295.0,12.1,D
"""
COUNTRY_CSV = """country_id,latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
USA,29.70,-95.10,320.0,0.39,0.36,2026-11-07,0005,N,VIIRS,n,2.0NRT,288.0,3.3,N
"""
AVAIL_CSV = """data_id,min_date,max_date
MODIS_NRT,2026-08-01,2026-11-08
VIIRS_SNPP_NRT,2026-08-01,2026-11-08
"""


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


@pytest.fixture
def http(monkeypatch):
    """Records requested URLs and serves canned CSV by endpoint."""
    import vendors.nasa_firms as firms

    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        if "/area/" in url:
            return _Resp(VIIRS_CSV if "-96.85" in url else "")
        if "/country/" in url:
            return _Resp(COUNTRY_CSV)
        return _Resp(AVAIL_CSV)

    monkeypatch.setenv("FIRMS_MAP_KEY", "testkey")
    monkeypatch.setattr(firms.requests, "get", fake_get)
    return calls


@pytest.mark.vendor("nasa_firms")
def test_hotspots_preset_aoi_padded(tmp_path, http):
    v = get_vendor("nasa_firms", data_dir=tmp_path)
    df = v.collect("hotspots", aoi="cushing", days=2)
    assert http[0].endswith("/area/csv/testkey/VIIRS_SNPP_NRT/-96.85,35.88,-96.65,36.05/2")
    assert df["aoi"].tolist() == ["cushing", "cushing"]
    assert df["acq_time"].tolist() == ["0812", "1930"]
    assert str(df["acq_datetime"].iloc[0]) == "2026-11-07 08:12:00+00:00"
    v.collect("hotspots", aoi="cushing", days=2)
    assert len(v.load("hotspots")) == 2  # upserted on the detection key


@pytest.mark.vendor("nasa_firms")
def test_hotspots_custom_bbox_and_date(tmp_path, http):
    v = get_vendor("nasa_firms", data_dir=tmp_path)
    df = v.fetch("hotspots", bbox="-96.85,35.88,-96.65,36.05", date="2026-11-07")
    assert http[0].endswith("/-96.85,35.88,-96.65,36.05/1/2026-11-07")
    assert df["aoi"].isna().all()
    with pytest.raises(ValueError):
        v.fetch("hotspots", aoi="cushing", days=11)


@pytest.mark.vendor("nasa_firms")
def test_site_scan_covers_all_presets(tmp_path, http):
    from vendors.core.aoi import AOIS

    v = get_vendor("nasa_firms", data_dir=tmp_path)
    df = v.collect("site_scan")
    assert len(http) == len(AOIS)
    assert len(df) == 2 and set(df["aoi"]) == {"cushing"}


@pytest.mark.vendor("nasa_firms")
def test_country_and_data_availability(tmp_path, http):
    v = get_vendor("nasa_firms", data_dir=tmp_path)
    df = v.collect("country", country="USA")
    assert http[0].endswith("/country/csv/testkey/VIIRS_SNPP_NRT/USA/1")
    assert (df["country_id"].iloc[0], df["acq_time"].iloc[0]) == ("USA", "0005")
    avail = v.collect("data_availability")
    assert http[1].endswith("/data_availability/csv/testkey/ALL")
    assert avail["data_id"].tolist() == ["MODIS_NRT", "VIIRS_SNPP_NRT"]


@pytest.mark.vendor("nasa_firms")
def test_firms_text_error_raises(tmp_path, monkeypatch):
    import vendors.nasa_firms as firms

    monkeypatch.setenv("FIRMS_MAP_KEY", "bad")
    monkeypatch.setattr(firms.requests, "get", lambda url, timeout=None: _Resp("Invalid MAP_KEY."))
    with pytest.raises(RuntimeError, match="Invalid MAP_KEY"):
        get_vendor("nasa_firms", data_dir=tmp_path).fetch("hotspots", aoi="cushing")


@pytest.mark.vendor("nasa_firms")
def test_missing_map_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    with pytest.raises(MissingMapKey, match="FIRMS_MAP_KEY"):
        get_vendor("nasa_firms", data_dir=tmp_path).fetch("hotspots", aoi="cushing")
