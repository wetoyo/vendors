"""Offline tests for the reddit vendor: requests.get/post are monkeypatched."""

import pytest

from vendors.core import REGISTRY

# Lets these tests run before the registry line is merged in.
REGISTRY.setdefault("reddit", "vendors.reddit:RedditVendor")

from vendors import get_vendor  # noqa: E402


class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def listing(children, after=None):
    return {"kind": "Listing", "data": {"after": after, "children": [{"kind": "t3", "data": c} for c in children]}}


def post(i, sub="wallstreetbets", title="", text=""):
    return {"id": f"p{i}", "subreddit": sub, "title": title, "selftext": text, "author": "u",
            "created_utc": 1762560000 + i, "score": i, "upvote_ratio": 0.9, "num_comments": 1,
            "url": "https://x", "permalink": f"/r/{sub}/comments/p{i}/", "link_flair_text": "DD",
            "is_self": True}


@pytest.fixture
def anon(monkeypatch):
    for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.vendor("reddit")
def test_reddit_posts_anonymous_paginates(tmp_path, monkeypatch, anon):
    import vendors.reddit.client as c
    calls = []
    pages = {None: listing([post(1), post(2)], after="t3_p2"), "t3_p2": listing([post(3)], after=None)}

    def get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params), headers))
        return Resp(pages[params.get("after")])

    monkeypatch.setattr(c.requests, "get", get)
    monkeypatch.setattr(c.requests, "post", lambda *a, **k: pytest.fail("no token call when anonymous"))
    v = get_vendor("reddit", data_dir=tmp_path, min_interval=0)
    df = v.collect("posts", subreddit="wallstreetbets, stocks", limit=10)
    assert df["id"].tolist() == ["p1", "p2", "p3"]
    assert str(df["created_utc"].dtype).startswith("datetime64") and df["flair"].iloc[0] == "DD"
    assert calls[0][0] == "https://www.reddit.com/r/wallstreetbets+stocks/new.json"
    assert calls[1][1]["after"] == "t3_p2"
    assert calls[0][2] == {"User-Agent": "vendors-client/0.1"}
    v.collect("posts", subreddit="wallstreetbets", limit=10)
    assert len(v.load("posts")) == 3  # upserted on id


@pytest.mark.vendor("reddit")
def test_reddit_oauth_token_and_group(tmp_path, monkeypatch):
    import vendors.reddit.client as c
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "sec")
    monkeypatch.setenv("REDDIT_USER_AGENT", "test-agent/1.0")
    posts, gets = [], []

    def tok(url, auth=None, data=None, headers=None, timeout=None):
        posts.append((url, auth, data, headers))
        return Resp({"access_token": "TOK", "expires_in": 3600})

    def get(url, params=None, headers=None, timeout=None):
        gets.append((url, params, headers))
        return Resp(listing([post(1, sub="Kalshi")]))

    monkeypatch.setattr(c.requests, "post", tok)
    monkeypatch.setattr(c.requests, "get", get)
    v = get_vendor("reddit", data_dir=tmp_path, min_interval=0)
    v.fetch("posts", group="prediction", sort="top", limit=1)
    v.fetch("search", query="fed", subreddit="Kalshi", limit=1)
    assert len(posts) == 1  # token cached
    assert posts[0][:3] == (c.TOKEN_URL, ("cid", "sec"), {"grant_type": "client_credentials"})
    assert gets[0][0] == "https://oauth.reddit.com/r/Kalshi+Polymarket/top"
    assert gets[0][1]["t"] == "day"
    assert gets[0][2] == {"User-Agent": "test-agent/1.0", "Authorization": "bearer TOK"}
    assert gets[1][0] == "https://oauth.reddit.com/r/Kalshi/search"
    assert gets[1][1]["q"] == "fed" and gets[1][1]["restrict_sr"] == 1


@pytest.mark.vendor("reddit")
def test_reddit_comments_flatten(tmp_path, monkeypatch, anon):
    import vendors.reddit.client as c

    def cm(i, parent, depth, replies=""):
        return {"kind": "t1", "data": {"id": i, "parent_id": parent, "depth": depth, "author": "a",
                                       "body": i, "score": 1, "created_utc": 1762560000, "replies": replies}}

    reply = cm("c2", "t1_c1", 1)
    tree = [listing([post(1)]), {"kind": "Listing", "data": {"children": [
        cm("c1", "t3_p1", 0, {"kind": "Listing", "data": {"children": [reply]}}),
        cm("c3", "t3_p1", 0),
        {"kind": "more", "data": {"children": ["c9"]}},
    ]}}]
    monkeypatch.setattr(c.requests, "get", lambda url, **k: Resp(tree))
    v = get_vendor("reddit", data_dir=tmp_path, min_interval=0)
    df = v.collect("comments", post_id="t3_p1")
    assert df["id"].tolist() == ["c1", "c2", "c3"]
    assert df["depth"].tolist() == [0, 1, 0] and df["parent_id"].iloc[1] == "t1_c1"
    assert (df["post_id"] == "p1").all()


@pytest.mark.vendor("reddit")
def test_reddit_ticker_mentions_appends(tmp_path, monkeypatch, anon):
    import vendors.reddit.client as c
    body = listing([post(1, title="$tsla to the moon", text="TSLA and NVDA calls, YOLO"),
                    post(2, title="NVDA earnings DD")])
    monkeypatch.setattr(c.requests, "get", lambda url, **k: Resp(body))
    v = get_vendor("reddit", data_dir=tmp_path, min_interval=0)
    df = v.collect("ticker_mentions", group="stocks")
    counts = df.set_index("ticker")
    assert counts.loc["TSLA", "mentions"] == 2 and counts.loc["TSLA", "cashtag_mentions"] == 1
    assert counts.loc["NVDA", "posts"] == 2
    assert "YOLO" not in counts.index and "DD" not in counts.index
    assert (df["group"] == "stocks").all() and df["posts_scanned"].iloc[0] == 2
    v.collect("ticker_mentions", group="stocks")
    assert len(v.load("ticker_mentions")) == 2 * len(df)  # append-only snapshots
