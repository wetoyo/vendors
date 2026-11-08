"""Offline tests for the open_meteo vendor: HTTP is monkeypatched, normalization is real."""

import pytest

from vendors.core.registry import REGISTRY

REGISTRY.setdefault("open_meteo", "vendors.open_meteo:OpenMeteoVendor")  # no-op once registered

from vendors import get_vendor  # noqa: E402

pytestmark = pytest.mark.vendor("open_meteo")

FORECAST = {
    "latitude": 40.71, "longitude": -73.99, "elevation": 27.0,
    "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
    "hourly": {"time": ["2026-11-08T00:00", "2026-11-08T01:00"],
               "temperature_2m": [8.1, 7.9], "precipitation": [0.0, 0.2], "wind_speed_10m": [10.0, 12.5]},
}
ARCHIVE = {
    "latitude": 29.75, "longitude": -95.4, "elevation": 12.0,
    "daily": {"time": ["2024-01-01", "2024-01-02"], "temperature_2m_max": [18.0, 20.5],
              "temperature_2m_min": [9.0, 11.0], "precipitation_sum": [0.0, 3.1]},
}
GEO = {"results": [{"id": 4699066, "name": "Houston", "latitude": 29.76328, "longitude": -95.36327,
                    "country_code": "US", "population": 2314157, "postcodes": ["77001", "77002"]}]}


@pytest.fixture
def calls(monkeypatch):
    import vendors.open_meteo as om
    seen = []

    class Resp:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body
        def raise_for_status(self): pass

    def fake_get(url, params=None, **kw):
        seen.append((url, params))
        body = FORECAST if url == om.FORECAST_URL else ARCHIVE if url == om.ARCHIVE_URL else GEO
        return Resp(body)

    monkeypatch.setattr(om.requests, "get", fake_get)
    return seen


def test_forecast_latlon_upserts(tmp_path, calls):
    v = get_vendor("open_meteo", data_dir=tmp_path)
    df = v.collect("forecast", latitude="40.7128", longitude="-74.006", days=2)
    assert list(df.columns[:4]) == ["latitude", "longitude", "location", "time"]
    assert df["wind_speed_10m"].tolist() == [10.0, 12.5]
    assert (df.latitude.iloc[0], df.grid_latitude.iloc[0]) == (40.7128, 40.71)
    assert calls[0][1]["forecast_days"] == 2 and calls[0][1]["timezone"] == "UTC"
    v.collect("forecast", latitude=40.7128, longitude=-74.006)
    assert len(v.load("forecast")) == 2


def test_forecast_preset_location(tmp_path, calls):
    from vendors.open_meteo import LOCATIONS
    df = get_vendor("open_meteo", data_dir=tmp_path).fetch("forecast", location="NYC", hourly=["temperature_2m"])
    assert df.location.iloc[0] == "nyc" and df.latitude.iloc[0] == LOCATIONS["nyc"][0]
    assert calls[0][1]["hourly"] == "temperature_2m"


def test_historical_city_geocodes(tmp_path, calls):
    v = get_vendor("open_meteo", data_dir=tmp_path)
    df = v.collect("historical", city="Houston", start="2024-01-01", end="2024-01-02")
    import vendors.open_meteo as om
    assert [c[0] for c in calls] == [om.GEOCODE_URL, om.ARCHIVE_URL]
    assert df.date.tolist() == ["2024-01-01", "2024-01-02"]
    assert (df.latitude.iloc[0], df.location.iloc[0]) == (29.76328, "Houston")
    assert df.precipitation_sum.tolist() == [0.0, 3.1]
    assert calls[1][1]["start_date"] == "2024-01-01"


def test_geocode(tmp_path, calls):
    v = get_vendor("open_meteo", data_dir=tmp_path)
    df = v.collect("geocode", name="Houston")
    assert (df["query"].iloc[0], df.id.iloc[0], df.country_code.iloc[0]) == ("Houston", 4699066, "US")
    assert len(v.load("geocode")) == 1


def test_point_errors(tmp_path, calls):
    v = get_vendor("open_meteo", data_dir=tmp_path)
    with pytest.raises(ValueError, match="latitude"):
        v.fetch("forecast")
    with pytest.raises(ValueError, match="unknown location"):
        v.fetch("forecast", location="atlantis")
    with pytest.raises(ValueError, match="start"):
        v.fetch("historical", location="nyc")
    assert calls == []
