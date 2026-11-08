"""Offline EIA tests: requests.get is monkeypatched; paging and normalization are real."""

import pytest


from vendors import get_vendor  # noqa: E402


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


def _row(series, period, value, **extra):
    return {"period": period, "series": series, "series-description": f"{series} desc",
            "area-name": "U.S.", "value": value, "units": "MBBL", **extra}


@pytest.fixture
def calls(monkeypatch):
    """Records each request; responds from the `pages` list set on the fixture."""
    import vendors.eia as eia
    monkeypatch.setenv("EIA_API_KEY", "test-key")
    log = type("Calls", (list,), {})()

    def fake_get(url, params=None, timeout=None):
        log.append((url, list(params or [])))
        return FakeResponse(log.responder(url, dict(params or [])))

    monkeypatch.setattr(eia.requests, "get", fake_get)
    return log


def _param_values(params, name):
    return [v for k, v in params if k == name]


@pytest.mark.vendor("eia")
def test_eia_series_paginates_and_normalizes(tmp_path, calls, monkeypatch):
    import vendors.eia as eia
    monkeypatch.setattr(eia, "PAGE_SIZE", 2)
    data = [_row("WCESTUS1", f"2026-10-{d:02d}", str(400000 + d)) for d in (3, 10, 17)]

    def responder(url, params):
        off = int(params["offset"])
        return {"response": {"total": "3", "data": data[off:off + 2]}}

    calls.responder = responder
    v = get_vendor("eia", data_dir=tmp_path)
    df = v.collect("series", series="WCESTUS1", start="2026-10-01", facets={"duoarea": "NUS"})

    assert len(calls) == 2
    url, params = calls[0]
    assert url == "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
    assert ("api_key", "test-key") in params and ("frequency", "weekly") in params
    assert _param_values(params, "facets[series][]") == ["WCESTUS1"]
    assert _param_values(params, "facets[duoarea][]") == ["NUS"]
    assert [p for p in calls[1][1] if p[0] == "offset"] == [("offset", "2")]

    assert list(df.columns[:4]) == ["series", "period", "value", "units"]
    assert {"area_name", "series_description", "route"} <= set(df.columns)
    assert df["value"].tolist() == [400003.0, 400010.0, 400017.0]
    v.collect("series", series="WCESTUS1")
    assert len(v.load("series")) == 3  # upserted on (series, period)


@pytest.mark.vendor("eia")
def test_eia_preset_maps_route_and_series(tmp_path, calls):
    calls.responder = lambda url, p: {"response": {"total": 1, "data": [_row("RWTC", "2026-11-06", "60.1")]}}
    df = get_vendor("eia", data_dir=tmp_path).fetch("series", preset="wti_spot")
    url, params = calls[0]
    assert url.endswith("/v2/petroleum/pri/spt/data/")
    assert ("frequency", "daily") in params and ("facets[series][]", "RWTC") in params
    assert (df["preset"].iloc[0], df["value"].iloc[0]) == ("wti_spot", 60.1)
    with pytest.raises(KeyError):
        get_vendor("eia", data_dir=tmp_path).fetch("series", preset="nope")


@pytest.mark.vendor("eia")
def test_eia_weekly_petroleum_groups_by_route(tmp_path, calls):
    def responder(url, params):
        ids = [v for k, v in calls[-1][1] if k == "facets[series][]"]
        return {"response": {"total": len(ids), "data": [_row(s, "2026-10-31", "1") for s in ids]}}

    calls.responder = responder
    df = get_vendor("eia", data_dir=tmp_path).fetch("weekly_petroleum")
    assert len(calls) == 3  # wstk, wiup, sndw
    assert sorted(df["preset"]) == sorted([
        "crude_stocks_us", "crude_stocks_cushing", "spr_stocks", "gasoline_stocks",
        "distillate_stocks", "refinery_utilization", "crude_production",
    ])
    assert df.set_index("preset").loc["crude_stocks_cushing", "series"] == "W_EPC0_SAX_YCUOK_MBBL"


@pytest.mark.vendor("eia")
def test_eia_routes_children_and_leaf(tmp_path, calls):
    calls.responder = lambda url, p: {"response": {"id": "petroleum", "routes": [
        {"id": "stoc", "name": "Stocks", "description": "Petroleum stocks"},
        {"id": "pri", "name": "Prices", "description": "Petroleum prices"},
    ]}}
    v = get_vendor("eia", data_dir=tmp_path)
    df = v.collect("routes", route="petroleum")
    assert calls[0][0] == "https://api.eia.gov/v2/petroleum/"
    assert df["path"].tolist() == ["petroleum/stoc", "petroleum/pri"]

    calls.responder = lambda url, p: {"response": {
        "id": "wstk", "name": "Weekly Stocks", "frequency": [{"id": "weekly"}],
        "facets": [{"id": "duoarea"}, {"id": "series"}], "data": {"value": {}},
        "startPeriod": "1982-08-20", "endPeriod": "2026-10-31"}}
    leaf = v.fetch("routes", route="petroleum/stoc/wstk").iloc[0]
    assert leaf["leaf"] and leaf["facets"] == ["duoarea", "series"] and leaf["start_period"] == "1982-08-20"


@pytest.mark.vendor("eia")
def test_eia_api_error_and_missing_key(tmp_path, calls, monkeypatch):
    import vendors.eia as eia
    calls.responder = lambda url, p: {"error": "invalid api_key", "code": 403}
    with pytest.raises(RuntimeError, match="invalid api_key"):
        get_vendor("eia", data_dir=tmp_path).fetch("routes")

    monkeypatch.delenv("EIA_API_KEY")
    n = len(calls)
    with pytest.raises(eia.MissingApiKey, match="EIA_API_KEY"):
        get_vendor("eia", data_dir=tmp_path).fetch("series", preset="crude_stocks_us")
    assert len(calls) == n  # no request made without a key
