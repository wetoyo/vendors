"""Offline tests for the OpenSky vendor: HTTP is monkeypatched, parsing is real."""

import pytest

from vendors import get_vendor
from vendors.core.registry import REGISTRY

# Until the registry line is merged, register at runtime so get_vendor works.
REGISTRY.setdefault("opensky", "vendors.opensky:OpenSkyVendor")

STATE = ["39cc8a", "CRL775  ", "France", 1794115245, 1794115245, 9.2116, 45.7214, 11582.4,
         False, 227.79, 293.84, 0, None, 11666.22, "5506", False, 0]
FLIGHT = {"icao24": "3c6444", "firstSeen": 1762300000, "estDepartureAirport": "EGLL",
          "lastSeen": 1762305000, "estArrivalAirport": "EDDF", "callsign": "DLH901  "}


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.body


@pytest.mark.vendor("opensky")
def test_opensky_states_bbox_and_append(tmp_path, monkeypatch):
    import vendors.opensky as os_
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        return Resp({"time": 1794115245, "states": [STATE]})

    monkeypatch.setattr(os_.requests, "get", fake_get)
    v = get_vendor("opensky", data_dir=tmp_path)
    df = v.collect("states", bbox="5,45,10,48")
    assert calls[0][1] == {"lamin": 45.0, "lomin": 5.0, "lamax": 48.0, "lomax": 10.0}
    assert calls[0][2] == {}  # anonymous
    row = df.iloc[0]
    assert (row.snapshot_time, row.icao24, row.callsign, row.geo_altitude, row.squawk) == (
        1794115245, "39cc8a", "CRL775", 11666.22, "5506")
    v.collect("states", icao24="39CC8A")
    assert calls[1][1] == {"icao24": ["39cc8a"]}
    assert len(v.load("states")) == 2  # append-only snapshots


@pytest.mark.vendor("opensky")
def test_opensky_states_empty(tmp_path, monkeypatch):
    import vendors.opensky as os_
    monkeypatch.setattr(os_.requests, "get", lambda *a, **k: Resp({"time": 1, "states": None}))
    df = get_vendor("opensky", data_dir=tmp_path).fetch("states")
    assert df.empty and "icao24" in df.columns


@pytest.mark.vendor("opensky")
def test_opensky_arrivals_auth_dates_upsert(tmp_path, monkeypatch):
    import vendors.opensky as os_
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "secret")
    posts, gets = [], []
    monkeypatch.setattr(os_.requests, "post", lambda url, data=None, timeout=None: (
        posts.append(data), Resp({"access_token": "tok", "expires_in": 1800}))[1])

    def fake_get(url, params=None, headers=None, timeout=None):
        gets.append((url, params, headers))
        return Resp([FLIGHT])

    monkeypatch.setattr(os_.requests, "get", fake_get)
    v = get_vendor("opensky", data_dir=tmp_path)
    df = v.collect("arrivals", airport="eddf", begin="2025-11-05", end=1762387200)
    v.collect("arrivals", airport="EDDF", begin=1762300800, end="1762387200")
    url, params, headers = gets[0]
    assert url.endswith("/flights/arrival") and headers == {"Authorization": "Bearer tok"}
    assert params == {"airport": "EDDF", "begin": 1762300800, "end": 1762387200}
    assert len(posts) == 1  # token cached
    assert list(df.columns[:2]) == ["icao24", "first_seen"]
    assert len(v.load("arrivals")) == 1  # upserted on (icao24, first_seen)


@pytest.mark.vendor("opensky")
def test_opensky_flights_404_is_empty(tmp_path, monkeypatch):
    import vendors.opensky as os_
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.setattr(os_.requests, "get", lambda *a, **k: Resp(None, status=404))
    v = get_vendor("opensky", data_dir=tmp_path)
    assert v.collect("departures", airport="EDDF", begin=0, end=3600).empty
    assert v.fetch("flights", begin=0, end=3600).empty
