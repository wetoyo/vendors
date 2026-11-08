"""Offline tests for the hackernews vendor (HTTP monkeypatched)."""

import pytest

from vendors.core.registry import REGISTRY

REGISTRY.setdefault("hackernews", "vendors.hackernews:HackerNewsVendor")


def _vendor(tmp_path, monkeypatch, responder):
    import vendors.hackernews as hn

    calls = []

    class Resp:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    def get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        return Resp(responder(url, params or {}))

    monkeypatch.setattr(hn.requests, "get", get)
    return hn.HackerNewsVendor(data_dir=tmp_path), calls


def _hit(i):
    return {"objectID": str(i), "title": f"T{i}", "url": "u", "author": "a", "points": i,
            "num_comments": 0, "created_at": "2026-11-01T00:00:00Z", "_tags": ["story"]}


@pytest.mark.vendor("hackernews")
def test_search_paginates_and_upserts(tmp_path, monkeypatch):
    pages = {0: [_hit(1), _hit(2)], 1: [_hit(3)]}
    v, calls = _vendor(tmp_path, monkeypatch, lambda url, p: {"hits": pages[p["page"]], "nbPages": 2})
    df = v.collect("search", query="rust", hits=3)
    assert df.object_id.tolist() == ["1", "2", "3"] and df["query"].iloc[0] == "rust"
    assert df.tags.iloc[0] == '["story"]'
    assert calls[0][0].endswith("/search_by_date") and calls[0][1]["numericFilters"].startswith("created_at_i>")
    v.collect("search", query="rust", hits=3)
    assert len(v.load("search")) == 3


@pytest.mark.vendor("hackernews")
def test_top_ranks_items(tmp_path, monkeypatch):
    def responder(url, p):
        if url.endswith("/topstories.json"):
            return [30, 10, 20]
        i = int(url.rsplit("/", 1)[1].split(".")[0])
        return {"id": i, "title": f"S{i}", "score": i, "time": 1700000000, "kids": [1, 2], "type": "story"}

    v, _ = _vendor(tmp_path, monkeypatch, responder)
    df = v.collect("top", list="top", limit=2)
    assert df.id.tolist() == [30, 10] and df["rank"].tolist() == [1, 2]
    assert (df["list"] == "topstories").all() and "kids" not in df.columns
    with pytest.raises(ValueError):
        v.fetch("top", list="bogus")


@pytest.mark.vendor("hackernews")
def test_comments_flatten_tree(tmp_path, monkeypatch):
    tree = {"id": 1, "children": [
        {"id": 2, "parent_id": 1, "story_id": 1, "text": "a", "children": [
            {"id": 3, "parent_id": 2, "story_id": 1, "text": "b", "children": []}]},
        {"id": 4, "parent_id": 1, "story_id": 1, "text": "c", "children": []},
    ]}
    v, _ = _vendor(tmp_path, monkeypatch, lambda url, p: tree)
    df = v.collect("comments", story_id=1)
    assert df.id.tolist() == [2, 3, 4]
    assert df.depth.tolist() == [1, 2, 1] and df.parent_id.tolist() == [1, 2, 1]
