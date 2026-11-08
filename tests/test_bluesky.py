"""Offline tests for the bluesky vendor: XRPC and Jetstream are faked."""

import asyncio
import json
import sys
import types

import pytest

from vendors import get_vendor

pytestmark = pytest.mark.vendor("bluesky")


def _post(n, text="stocks up"):
    return {
        "uri": f"at://did:plc:a/app.bsky.feed.post/{n}", "cid": f"c{n}",
        "author": {"did": "did:plc:a", "handle": "a.bsky.social"},
        "record": {"text": text, "createdAt": "2026-11-08T00:00:00Z", "langs": ["en"]},
        "likeCount": n, "repostCount": 0, "replyCount": 1, "quoteCount": 0,
    }


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def anon(monkeypatch, tmp_path):
    monkeypatch.delenv("BSKY_HANDLE", raising=False)
    monkeypatch.delenv("BSKY_APP_PASSWORD", raising=False)
    return get_vendor("bluesky", data_dir=tmp_path)


def test_search_paginates_and_falls_back_on_403(anon, monkeypatch):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params)))
        if url.startswith("https://public.api"):
            return FakeResp(403, {"error": "Forbidden"})
        if params.get("cursor") is None:
            return FakeResp(200, {"posts": [_post(1), _post(2)], "cursor": "next"})
        return FakeResp(200, {"posts": [_post(3)]})

    monkeypatch.setattr(anon.session, "get", get)
    df = anon.collect("search", preset="markets", limit=5, lang="en")
    assert df["uri"].str.endswith(("1", "2", "3")).all() and len(df) == 3
    assert df["query"].iloc[0] == '$SPY OR stocks OR "stock market"'
    assert df["like_count"].tolist() == [1, 2, 3]
    queried = [p["q"] for u, p in calls if u.startswith("https://api.bsky.app") and p.get("cursor") is None]
    assert queried == ["$SPY", "stocks", '"stock market"']  # OR split client-side
    assert calls[1][1]["lang"] == "en" and "since" not in calls[1][1]
    anon.collect("search", query="stocks", limit=5)
    assert len(anon.load("search")) == 3  # upserted on uri


def test_anonymous_search_keeps_first_page_when_cursor_forbidden(anon, monkeypatch):
    def get(url, params=None, headers=None, timeout=None):
        if params.get("cursor"):
            return FakeResp(403, {"error": "Forbidden"})
        return FakeResp(200, {"posts": [_post(1)], "cursor": "next"})

    monkeypatch.setattr(anon.session, "get", get)
    assert len(anon.fetch("search", query="oil", limit=500)) == 1


def test_author_feed_and_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("BSKY_HANDLE", "me.bsky.social")
    monkeypatch.setenv("BSKY_APP_PASSWORD", "pw")
    v = get_vendor("bluesky", data_dir=tmp_path)
    monkeypatch.setattr(v.session, "post", lambda url, json=None, timeout=None: FakeResp(200, {"accessJwt": "tok"}))
    seen = {}

    def get(url, params=None, headers=None, timeout=None):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return FakeResp(200, {"feed": [{"post": _post(1)}, {
            "post": _post(2), "reason": {"$type": "app.bsky.feed.defs#reasonRepost", "by": {"handle": "me"}}}]})

    monkeypatch.setattr(v.session, "get", get)
    df = v.fetch("author_feed", actor="a.bsky.social", limit=10)
    assert seen == {"url": "https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed", "auth": "Bearer tok"}
    assert df["reposted_by"].tolist() == [None, "me"]
    assert df["author_handle"].iloc[0] == "a.bsky.social"


def test_stream_filters_keywords(anon, monkeypatch):
    msgs = [
        {"did": "did:plc:a", "time_us": 1, "kind": "commit",
         "commit": {"operation": "create", "collection": "app.bsky.feed.post", "rkey": "r1", "cid": "c1",
                    "record": {"text": "Oil prices jump", "createdAt": "t"}}},
        {"did": "did:plc:b", "time_us": 2, "kind": "commit",
         "commit": {"operation": "create", "collection": "app.bsky.feed.post", "rkey": "r2",
                    "record": {"text": "cat pics"}}},
        {"did": "did:plc:c", "time_us": 3, "kind": "commit", "commit": {"operation": "delete"}},
    ]

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            async def gen():
                for m in msgs:
                    yield json.dumps(m)
            return gen()

    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda url: FakeWS()))

    async def run():
        return [m async for m in anon.stream(keywords="oil")]

    out = asyncio.run(run())
    assert [m["uri"] for m in out] == ["at://did:plc:a/app.bsky.feed.post/r1"]
    assert out[0]["text"] == "Oil prices jump"
