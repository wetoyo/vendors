"""Offline tests for the coingecko vendor (HTTP is monkeypatched)."""

import pytest


from vendors import get_vendor  # noqa: E402

pytestmark = pytest.mark.vendor("coingecko")

T0, T1 = 1700000000000, 1700003600000


class Resp:
    def __init__(self, payload, status=200, headers=None):
        self.payload, self.status_code, self.headers = payload, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.payload


def route(url, params):
    if url.endswith("/coins/markets"):
        page = params["page"]
        return [{"id": f"c{page}", "symbol": "x", "current_price": 1.0 * page, "roi": {"times": 2}}] * (
            params["per_page"] if page == 1 else 1)
    if url.endswith("/market_chart"):
        return {"prices": [[T0, 1.0], [T1, 2.0], [T1, 2.0]], "market_caps": [[T0, 10.0], [T1, 20.0]],
                "total_volumes": [[T0, 5.0], [T1, 6.0]]}
    if url.endswith("/ohlc"):
        return [[T0, 1, 2, 0.5, 1.5], [T1, 1.5, 3, 1, 2]]
    if url.endswith("/search/trending"):
        return {"coins": [{"item": {"id": "pepe", "name": "Pepe", "data": {"price": 1}}}],
                "nfts": [{"id": "punks", "name": "Punks"}], "categories": [{"id": 1, "name": "Meme"}]}
    if url.endswith("/global"):
        return {"data": {"active_cryptocurrencies": 100, "total_market_cap": {"usd": 3e12, "btc": 30e6},
                         "market_cap_percentage": {"btc": 55.0}}}
    raise AssertionError(url)


@pytest.fixture
def calls(monkeypatch):
    import vendors.coingecko as cg
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append((url, params, headers))
        return Resp(route(url, params))

    monkeypatch.setattr(cg.requests, "get", fake_get)
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    monkeypatch.delenv("COINGECKO_PRO", raising=False)
    return seen


def test_markets_pages_and_append(tmp_path, calls):
    v = get_vendor("coingecko", data_dir=tmp_path)
    df = v.collect("markets", per_page=2, pages=3)
    assert len(df) == 3 and len(calls) == 2  # stops on a short page
    assert {"snapshot_time", "vs_currency", "current_price"} <= set(df.columns)
    v.collect("markets", per_page=2, pages=1)
    assert len(v.load("markets")) == 5  # append snapshots


def test_market_chart_and_ohlc_upsert(tmp_path, calls):
    v = get_vendor("coingecko", data_dir=tmp_path)
    mc = v.collect("market_chart", ids="bitcoin, ethereum")
    assert len(mc) == 4 and list(mc.columns[:3]) == ["id", "vs_currency", "timestamp"]
    assert mc[mc.id == "bitcoin"].market_cap.tolist() == [10.0, 20.0]
    assert calls[0][0].endswith("/coins/bitcoin/market_chart")
    v.collect("market_chart", ids=["bitcoin"])
    assert len(v.load("market_chart")) == 4
    o = v.collect("ohlc", id="bitcoin", days=1)
    v.collect("ohlc")
    assert o.close.tolist() == [1.5, 2] and len(v.load("ohlc")) == 2


def test_trending_and_global(tmp_path, calls):
    v = get_vendor("coingecko", data_dir=tmp_path)
    t = v.collect("trending")
    assert t.type.tolist() == ["coin", "nft", "category"] and t.name.tolist() == ["Pepe", "Punks", "Meme"]
    g = v.collect("global_metrics")
    assert g.total_market_cap_usd.iloc[0] == 3e12 and g.market_cap_percentage_btc.iloc[0] == 55.0
    v.collect("global_metrics")
    assert len(v.load("global_metrics")) == 2


def test_api_key_headers_and_429_backoff(monkeypatch):
    import vendors.coingecko as cg
    seen, replies = [], [Resp({}, 429, {"Retry-After": "1"}), Resp({"ok": 1})]
    monkeypatch.setattr(cg.requests, "get", lambda url, params=None, headers=None, timeout=None:
                        seen.append((url, headers)) or replies.pop(0))
    sleeps = []
    monkeypatch.setattr(cg.time, "sleep", sleeps.append)
    monkeypatch.setenv("COINGECKO_API_KEY", "k")
    monkeypatch.delenv("COINGECKO_PRO", raising=False)
    assert cg.get("/ping") == {"ok": 1}
    assert sleeps == [1.0] and seen[0][1]["x-cg-demo-api-key"] == "k"
    assert seen[0][0].startswith(cg.PUBLIC_URL)

    monkeypatch.setenv("COINGECKO_PRO", "1")
    replies.append(Resp({}))
    cg.get("/ping")
    assert seen[-1][0].startswith(cg.PRO_URL) and seen[-1][1]["x-cg-pro-api-key"] == "k"
