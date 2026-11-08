"""Reddit posts, search and comments via public JSON or app-only OAuth.

Set ``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET`` to use OAuth (oauth.reddit.com);
without them requests go to www.reddit.com/*.json, which Reddit often blocks.
``REDDIT_USER_AGENT`` overrides the generic User-Agent.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone

import pandas as pd

from vendors.core import Vendor, dataset

from .client import RedditClient

GROUPS: dict[str, list[str]] = {
    "stocks": ["wallstreetbets", "stocks", "investing", "options", "StockMarket"],
    "crypto": ["CryptoCurrency", "Bitcoin", "ethereum"],
    "energy": ["oil", "energy", "oilandgasworkers"],
    "macro": ["economics", "Economics"],
    "prediction": ["Kalshi", "Polymarket"],
}

POST_COLUMNS = [
    "id", "subreddit", "title", "selftext", "author", "created_utc", "score",
    "upvote_ratio", "num_comments", "url", "permalink", "flair", "is_self",
]

_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
_BARE = re.compile(r"\b([A-Z]{2,5})\b")
# Uppercase words that are rarely tickers in finance subs.
_STOP = set("""
A I AM AN AND ARE AS AT BE BY CEO CFO DD DO EOD EPS ETF FD FOMO FOR FYI GDP GO HODL IF IMO IN IPO
IS IT ITM IRS LOL ME MY NO NOT NYSE OF OK ON OR OTM PM PE RH SEC SO THE TA TL DR TO UP US USA USD
WE WSB YOLO ATH CPI FED API AI EV IV OP PSA LMAO WTF YOY QOQ EU UK OTC ASAP TLDR EST PST UTC
""".split())


def _subs(subreddit: Sequence[str] | str | None, group: str | None) -> list[str]:
    if group:
        if group not in GROUPS:
            raise KeyError(f"Unknown group {group!r}; available: {sorted(GROUPS)}")
        return GROUPS[group]
    if not subreddit:
        return []
    if isinstance(subreddit, str):
        subreddit = subreddit.split(",")
    return [s.strip().removeprefix("r/") for s in subreddit if s.strip()]


def _ts(value) -> pd.Timestamp | None:
    return pd.to_datetime(value, unit="s", utc=True) if value is not None else None


def _post_row(d: dict) -> dict:
    return {
        "id": d.get("id"),
        "subreddit": d.get("subreddit"),
        "title": d.get("title"),
        "selftext": d.get("selftext"),
        "author": d.get("author"),
        "created_utc": _ts(d.get("created_utc")),
        "score": d.get("score"),
        "upvote_ratio": d.get("upvote_ratio"),
        "num_comments": d.get("num_comments"),
        "url": d.get("url"),
        "permalink": d.get("permalink"),
        "flair": d.get("link_flair_text"),
        "is_self": d.get("is_self"),
    }


def count_tickers(texts: Sequence[str]) -> pd.DataFrame:
    """Counts $TICKER cashtags and bare uppercase tickers across texts."""
    total, cashtags, posts = Counter(), Counter(), Counter()
    for text in texts:
        tags = [t.upper() for t in _CASHTAG.findall(text or "")]
        bare = [t for t in _BARE.findall(_CASHTAG.sub(" ", text or "")) if t not in _STOP]
        cashtags.update(tags)
        total.update(tags + bare)
        posts.update(set(tags + bare))
    rows = [
        {"ticker": t, "mentions": n, "cashtag_mentions": cashtags[t], "posts": posts[t]}
        for t, n in total.items()
    ]
    df = pd.DataFrame(rows, columns=["ticker", "mentions", "cashtag_mentions", "posts"])
    return df.sort_values(["mentions", "ticker"], ascending=[False, True]).reset_index(drop=True)


class RedditVendor(Vendor):
    name = "reddit"
    description = "Reddit posts, search, comments and ticker mentions (JSON or app-only OAuth)"

    def __init__(self, data_dir=None, min_interval: float | None = None):
        super().__init__(data_dir)
        self.client = RedditClient() if min_interval is None else RedditClient(min_interval)

    def _posts(self, path: str, limit: int, **params) -> pd.DataFrame:
        rows = [_post_row(d) for d in self.client.listing(path, int(limit), **params)]
        return pd.DataFrame(rows, columns=POST_COLUMNS)

    @dataset(key=["id"])
    def posts(
        self,
        subreddit: Sequence[str] | str = "wallstreetbets",
        sort: str = "new",
        limit: int = 100,
        time_filter: str = "day",
        group: str | None = None,
    ) -> pd.DataFrame:
        """Posts from subreddits (comma list) or a preset group."""
        subs = _subs(subreddit, group)
        t = time_filter if sort in ("top", "controversial") else None
        return self._posts(f"/r/{'+'.join(subs)}/{sort}", limit, t=t)

    @dataset(key=["id"])
    def search(
        self,
        query: str,
        subreddit: Sequence[str] | str | None = None,
        sort: str = "new",
        limit: int = 100,
        time_filter: str = "week",
        group: str | None = None,
    ) -> pd.DataFrame:
        """Posts matching a search query, site-wide or within subreddits."""
        subs = _subs(subreddit, group)
        path = f"/r/{'+'.join(subs)}/search" if subs else "/search"
        return self._posts(path, limit, q=query, sort=sort, t=time_filter,
                           restrict_sr=1 if subs else None)

    @dataset(key=["id"])
    def comments(self, post_id: str, limit: int = 500) -> pd.DataFrame:
        """A post's comment tree, flattened with parent_id and depth."""
        post_id = str(post_id).removeprefix("t3_")
        body = self.client.get(f"/comments/{post_id}", limit=int(limit))
        rows: list[dict] = []

        def walk(children: list, depth: int) -> None:
            for child in children:
                if child.get("kind") != "t1":
                    continue  # skip "more" stubs
                d = child.get("data", {})
                rows.append({
                    "id": d.get("id"),
                    "post_id": post_id,
                    "parent_id": d.get("parent_id"),
                    "depth": d.get("depth", depth),
                    "author": d.get("author"),
                    "body": d.get("body"),
                    "score": d.get("score"),
                    "created_utc": _ts(d.get("created_utc")),
                    "permalink": d.get("permalink"),
                })
                replies = d.get("replies")
                if isinstance(replies, dict):
                    walk(replies.get("data", {}).get("children", []), depth + 1)

        if isinstance(body, list) and len(body) > 1:
            walk(body[1].get("data", {}).get("children", []), 0)
        return pd.DataFrame(rows[: int(limit)])

    @dataset()
    def ticker_mentions(self, group: str = "stocks", limit: int = 200) -> pd.DataFrame:
        """Snapshot of ticker mention counts across a group's recent posts."""
        posts = self.posts(group=group, sort="new", limit=limit)
        texts = (posts["title"].fillna("") + "\n" + posts["selftext"].fillna("")).tolist()
        df = count_tickers(texts)
        df.insert(0, "snapshot_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        df.insert(1, "group", group)
        df["posts_scanned"] = len(posts)
        return df
