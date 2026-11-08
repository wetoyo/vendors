"""Offline tests for the FRED vendor: requests.get is monkeypatched."""

import math

import pytest

pytestmark = pytest.mark.vendor("fred")


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def _fake_api(calls):
    def fake_get(url, params=None, timeout=None):
        calls.append((url.rsplit("/fred/", 1)[1], dict(params)))
        path = calls[-1][0]
        if path == "series/observations":
            sid = params["series_id"]
            return Resp({"observations": [
                {"realtime_start": "2026-11-08", "realtime_end": "2026-11-08", "date": "2026-11-05", "value": "4.1"},
                {"realtime_start": "2026-11-08", "realtime_end": "2026-11-08", "date": "2026-11-06",
                 "value": "." if sid == "DGS2" else "4.2"},
            ]})
        if path == "series":
            return Resp({"seriess": [{"id": params["series_id"], "title": "T", "frequency_short": "D"}]})
        if path == "series/search":
            return Resp({"seriess": [{"id": f"S{params['offset'] + i}"} for i in range(params["limit"])]})
        if path == "releases/dates":
            return Resp({"release_dates": [{"release_id": 10, "release_name": "CPI", "date": "2026-11-12"}]})
        raise AssertionError(path)
    return fake_get


@pytest.fixture
def fred(tmp_path, monkeypatch):
    import vendors.fred as fred_mod
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(fred_mod.requests, "get", _fake_api(calls))
    return fred_mod.FredVendor(data_dir=tmp_path), calls


def test_observations_parse_and_upsert(fred):
    v, calls = fred
    df = v.collect("observations", series_ids="dgs10, DGS2", start="2026-11-01")
    assert list(df.columns[:3]) == ["series_id", "date", "value"]
    assert df.series_id.tolist() == ["DGS10", "DGS10", "DGS2", "DGS2"]
    assert df.value.iloc[0] == 4.1 and math.isnan(df.value.iloc[3])
    p = calls[0][1]
    assert (p["api_key"], p["file_type"], p["observation_start"]) == ("test-key", "json", "2026-11-01")
    assert "observation_end" not in p
    v.collect("observations", series_ids=["DGS10", "DGS2"])
    assert len(v.load("observations")) == 4


def test_presets_fill_series_ids(fred):
    import vendors.fred as fred_mod
    v, calls = fred
    df = v.fetch("series_info", preset="rates")
    assert df.id.tolist() == fred_mod.PRESETS["rates"]
    assert len(v.fetch("observations", preset="fx")) == 2 * len(fred_mod.PRESETS["fx"])
    with pytest.raises(KeyError):
        v.fetch("observations", preset="nope")
    with pytest.raises(ValueError):
        v.fetch("series_info")


def test_search_pages_and_release_dates(fred):
    v, calls = fred
    assert len(v.collect("search", query="treasury", limit=1500)) == 1500
    assert [c[1]["offset"] for c in calls] == [0, 1000]
    rd = v.collect("release_dates", days_ahead=7)
    assert rd.release_name.tolist() == ["CPI"]


def test_missing_api_key(tmp_path, monkeypatch):
    import vendors.fred as fred_mod
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred_mod.requests, "get", lambda *a, **k: pytest.fail("no request without a key"))
    with pytest.raises(fred_mod.MissingApiKey, match="FRED_API_KEY"):
        fred_mod.FredVendor(data_dir=tmp_path).fetch("observations")
