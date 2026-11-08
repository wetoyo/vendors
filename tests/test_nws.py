"""Offline tests for the NWS vendor: HTTP is monkeypatched; normalization is real."""

import pytest

from vendors.core import get_vendor


POINT = {"properties": {
    "gridId": "OKX", "gridX": 34, "gridY": 45,
    "forecast": "https://api.weather.gov/gridpoints/OKX/34,45/forecast",
    "forecastHourly": "https://api.weather.gov/gridpoints/OKX/34,45/forecast/hourly",
}}
FORECAST = {"properties": {"updateTime": "2026-11-08T03:00:00+00:00", "periods": [
    {"number": 1, "name": "Tonight", "startTime": "2026-11-07T18:00:00-05:00",
     "endTime": "2026-11-08T06:00:00-05:00", "isDaytime": False, "temperature": 49,
     "temperatureUnit": "F", "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": 10},
     "windSpeed": "17 to 21 mph", "shortForecast": "Mostly Clear"},
    {"number": 2, "name": "Sunday", "startTime": "2026-11-08T06:00:00-05:00",
     "endTime": "2026-11-08T18:00:00-05:00", "isDaytime": True, "temperature": 58,
     "temperatureUnit": "F", "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None}},
]}}
ALERTS = {"features": [{"properties": {
    "id": "urn:oid:1", "event": "Beach Hazards Statement", "severity": "Moderate",
    "areaDesc": "Coast", "messageType": "Alert", "senderName": "NWS San Francisco CA",
    "geocode": {"UGC": ["CAZ505"]}, "sent": "2026-11-07T20:09:00-08:00",
}}]}
OBS = {"features": [{"properties": {
    "@id": "x", "stationId": "KNYC", "station": "https://api.weather.gov/stations/KNYC",
    "timestamp": "2026-11-08T04:51:00+00:00", "textDescription": "Cloudy",
    "temperature": {"unitCode": "wmoUnit:degC", "value": 9.4, "qualityControl": "V"},
    "windSpeed": {"unitCode": "wmoUnit:km_h-1", "value": None},
    "cloudLayers": [{"base": {"value": 2740}}],
}}]}
STATIONS = {"features": [{"geometry": {"type": "Point", "coordinates": [-71.4, 41.7]}, "properties": {
    "stationIdentifier": "A4893", "name": "RI DEM", "timeZone": "America/New_York",
    "elevation": {"unitCode": "wmoUnit:m", "value": -6.1},
}}]}


@pytest.fixture
def calls(monkeypatch):
    import vendors.nws as nws
    seen = []

    def fake_get(path, params=None):
        seen.append((path, params))
        if path.startswith("/points/"):
            return POINT
        if "/gridpoints/" in path:
            return FORECAST
        if path == "/alerts/active":
            return ALERTS
        if path.endswith("/observations"):
            return OBS
        if path == "/stations":
            return STATIONS
        raise AssertionError(path)

    monkeypatch.setattr(nws, "get", fake_get)
    return seen


@pytest.mark.vendor("nws")
def test_nws_forecast(tmp_path, calls):
    v = get_vendor("nws", data_dir=tmp_path)
    df = v.collect("forecast", latitude=40.7829, longitude=-73.9654, hourly=True)
    assert calls[1][0].endswith("/forecast/hourly")
    assert df[["grid_id", "grid_x", "grid_y"]].iloc[0].tolist() == ["OKX", 34, 45]
    assert df["start_time"].tolist()[0] == "2026-11-07T18:00:00-05:00"
    assert df["probability_of_precipitation"].tolist()[0] == 10
    assert df["probability_of_precipitation_unit"].iloc[0] == "percent"
    v.collect("forecast", latitude=40.7829, longitude=-73.9654)
    assert len(v.load("forecast")) == 4  # hourly and 12-hour periods kept apart
    v.collect("forecast", latitude=40.7829, longitude=-73.9654)
    assert len(v.load("forecast")) == 4  # re-collect upserts


@pytest.mark.vendor("nws")
def test_nws_alerts(tmp_path, calls):
    v = get_vendor("nws", data_dir=tmp_path)
    df = v.collect("alerts", area="CA", severity="Moderate")
    assert calls[0] == ("/alerts/active", {"area": "CA", "severity": "Moderate"})
    row = df.iloc[0]
    assert (row.id, row.event, row.area_desc, row.message_type) == (
        "urn:oid:1", "Beach Hazards Statement", "Coast", "Alert")
    v.collect("alerts")
    assert len(v.load("alerts")) == 1


@pytest.mark.vendor("nws")
def test_nws_observations_unwrap_values(tmp_path, calls):
    v = get_vendor("nws", data_dir=tmp_path)
    df = v.collect("observations", station="knyc", limit=5)
    assert calls[0] == ("/stations/KNYC/observations", {"limit": 5})
    row = df.iloc[0]
    assert (row.station, row.temperature, row.temperature_unit) == ("KNYC", 9.4, "degC")
    assert "cloud_layers" not in df.columns and "station_id" not in df.columns
    v.collect("observations", station="KNYC")
    assert len(v.load("observations")) == 1


@pytest.mark.vendor("nws")
def test_nws_stations(tmp_path, calls):
    v = get_vendor("nws", data_dir=tmp_path)
    df = v.collect("stations", state="ri")
    assert calls[0][1]["state"] == "RI"
    row = df.iloc[0]
    assert (row.station, row.latitude, row.longitude, row.elevation) == ("A4893", 41.7, -71.4, -6.1)


@pytest.mark.vendor("nws")
def test_nws_user_agent(monkeypatch):
    import vendors.nws as nws
    monkeypatch.delenv("NWS_USER_AGENT", raising=False)
    assert nws.user_agent() == "vendors-client/0.1"
    monkeypatch.setenv("NWS_USER_AGENT", "myapp (ops@example.com)")
    assert nws.user_agent() == "myapp (ops@example.com)"
