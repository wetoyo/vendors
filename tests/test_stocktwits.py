"""Offline tests for the StockTwits vendor: HTTP is monkeypatched."""

import pytest

from vendors.core import REGISTRY, get_vendor

# Registered centrally on merge; this keeps the tests runnable on the branch alone.
REGISTRY.setdefault("stocktwits", "vendors.stocktwits:StocktwitsVendor")

pytestmark = pytest.mark.vendor("stocktwits")


def _msg(i, sentiment=None, syms=("AAPL",)):
    return {
        "id": i, "body": f"msg {i}", "created_at": "2026-11-08T05:15:01Z",
        "user": {"username": f"u{i}", "followers": i * 10},
        "entities": {"sentiment": {"basic": sentiment} if sentiment else None},
        "likes": {"total": 2} if i % 2 else None,
        "symbols": [{"symbol": s} for s in syms],
    }


@pytest.fixture
def fake_get(monkeypatch):
    import vendors.stocktwits as st
    calls = []
    pages = {
        None: {"messages": [_msg(5, "Bullish", ("AAPL", "MSFT")), _msg(4, "Bearish")],
               "cursor": {"more": True, "max": 4}},
        4: {"messages": [_msg(3, "Bullish"), _msg(2)], "cursor": {"more": False, "max": 2}},
    }

    def get(path, params=None):
        calls.append((path, dict(params or {})))
        if path.startswith("trending"):
            return {"symbols": [{"symbol": "XLP", "trending_score": 5.5}, {"symbol": "NVDA", "trending_score": 4.0}]}
        return pages[(params or {}).get("max")]

    monkeypatch.setattr(st, "get", get)
    return calls


def test_messages_paginate_and_upsert(tmp_path, fake_get):
    v = get_vendor("stocktwits", data_dir=tmp_path)
    df = v.collect("messages", symbols="aapl", pages=3)
    assert df["id"].tolist() == [5, 4, 3, 2]  # stops when cursor.more is false
    assert fake_get[1] == ("streams/symbol/AAPL.json", {"limit": 30, "max": 4})
    assert df["sentiment"].tolist() == ["Bullish", "Bearish", "Bullish", None]
    assert df["likes"].tolist() == [2, 0, 2, 0]
    assert df["symbols"].iloc[0] == ["AAPL", "MSFT"]
    v.collect("messages", symbols="AAPL", pages=1)
    assert len(v.load("messages")) == 4  # keyed on id


def test_sentiment_and_trending_snapshots(tmp_path, fake_get):
    v = get_vendor("stocktwits", data_dir=tmp_path)
    s = v.collect("sentiment", symbols="AAPL", pages=2).iloc[0]
    assert (s.bullish, s.bearish, s.untagged) == (2, 1, 1)
    assert s.bullish_ratio == pytest.approx(2 / 3)
    t = v.collect("trending")
    v.collect("trending")
    assert t["rank"].tolist() == [1, 2] and t["symbol"].tolist() == ["XLP", "NVDA"]
    assert len(v.load("trending")) == 4  # append snapshots


def test_403_raises_helpful_error(monkeypatch):
    import vendors.stocktwits as st

    class Resp:
        status_code = 403

    monkeypatch.setattr(st.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(st.StocktwitsBlocked, match="Cloudflare"):
        st.get("trending/symbols.json")


def test_user_agent_override(monkeypatch):
    import vendors.stocktwits as st
    seen = {}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr(st.requests, "get", lambda url, **k: seen.update(k["headers"]) or Resp())
    monkeypatch.setenv("STOCKTWITS_USER_AGENT", "my-client/1.0")
    st.get("trending/symbols.json")
    assert seen["User-Agent"] == "my-client/1.0"
