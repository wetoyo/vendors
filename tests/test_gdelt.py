"""Offline tests for the GDELT vendor: HTTP is monkeypatched; parsing is real."""

import pytest


gdelt = pytest.importorskip("vendors.gdelt")
from vendors.gdelt import client  # noqa: E402

# The marker needs a registry entry; skip it until the vendor is registered.
pytestmark = pytest.mark.vendor("gdelt")


def _fake(responses, calls):
    def doc(query, mode, **params):
        calls.append({"query": query, "mode": mode, **params})
        return responses[mode]
    return doc


ARTLIST = {"articles": [
    {"url": "https://a.example/1", "url_mobile": "", "title": "Oil jumps", "seendate": "20261107T061500Z",
     "socialimage": "https://a.example/1.jpg", "domain": "a.example", "language": "English", "sourcecountry": "United States"},
    {"url": "https://b.example/2", "title": "OPEC meets", "seendate": "20261107T070000Z",
     "domain": "b.example", "language": "English", "sourcecountry": "United Kingdom"},
]}
TIMELINE = {"query_details": {}, "timeline": [{"series": "Article Count", "data": [
    {"date": "20261106T000000Z", "value": 12, "norm": 1000},
    {"date": "20261107T000000Z", "value": 15, "norm": 1100},
]}]}
TONECHART = {"tonechart": [
    {"bin": -3, "count": 4, "toparts": [{"url": "https://a.example/1", "title": "Oil jumps"}]},
    {"bin": 1, "count": 9, "toparts": []},
]}
RESPONSES = {"ArtList": ARTLIST, "TimelineVolRaw": TIMELINE, "ToneChart": TONECHART}


def test_gdelt_articles_preset_and_upsert(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "doc", _fake(RESPONSES, calls))
    v = gdelt.GdeltVendor(data_dir=tmp_path)
    df = v.collect("articles", preset="oil", sourcecountry="US", max_records=500)
    assert calls[0]["query"] == gdelt.PRESETS["oil"] + " sourcecountry:US"
    assert calls[0]["maxrecords"] == 250 and calls[0]["timespan"] == "1d"
    assert list(df.columns) == ["query", "url", "title", "seendate", "domain", "language", "sourcecountry", "socialimage"]
    assert df["query"].iloc[0] == gdelt.PRESETS["oil"]
    assert str(df["seendate"].iloc[0]) == "2026-11-07 06:15:00+00:00"
    v.collect("articles", preset="oil")
    assert len(v.load("articles")) == 2


def test_gdelt_articles_date_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "doc", _fake(RESPONSES, calls))
    gdelt.GdeltVendor(data_dir=tmp_path).fetch("articles", query="tanker", start="20261101000000")
    assert calls[0]["startdatetime"] == "20261101000000" and "timespan" not in calls[0]


def test_gdelt_timeline_and_tone(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "doc", _fake(RESPONSES, []))
    v = gdelt.GdeltVendor(data_dir=tmp_path)
    df = v.collect("timeline", query="bitcoin")
    assert df[["series", "value", "norm"]].values.tolist() == [["Article Count", 12, 1000], ["Article Count", 15, 1100]]
    v.collect("timeline", query="bitcoin")
    assert len(v.load("timeline")) == 2  # upserted on (query, mode, date, series)
    v.collect("tone", query="bitcoin")
    v.collect("tone", query="bitcoin")
    tone = v.load("tone")
    assert len(tone) == 4  # append-only snapshots
    assert tone["bin"].tolist()[:2] == [-3, 1]


def test_gdelt_empty_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "doc", lambda *a, **k: {})
    v = gdelt.GdeltVendor(data_dir=tmp_path)
    assert v.fetch("articles", query="zzz").empty
    assert v.fetch("timeline", query="zzz", mode="TimelineTone").empty
    with pytest.raises(ValueError):
        v.fetch("timeline", query="x", mode="Bogus")
    with pytest.raises(KeyError):
        v.fetch("articles", preset="nope")


def test_gdelt_client_retries_rate_limit(monkeypatch):
    class R:
        def __init__(self, status, text):
            self.status_code, self.text = status, text
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)
    seq = [R(429, "Please limit requests to one every 5 seconds"), R(200, '{"articles": []}')]
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    assert client.doc("oil", "ArtList") == {"articles": []}
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: R(200, "The specified phrase is too short."))
    with pytest.raises(client.GdeltError):
        client.doc("a", "ArtList")
