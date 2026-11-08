"""Bluesky posts via AT Protocol XRPC (plain ``requests``), plus a Jetstream firehose.

No credentials needed: reads go to the public AppView, which serves only the
first page (<=100 posts per term) of anonymous searches. Set ``BSKY_HANDLE`` and
``BSKY_APP_PASSWORD`` (an app password) to call through bsky.social with a
session token and paginate further.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator, Sequence

import pandas as pd
import requests

from vendors.core import Vendor, dataset

log = logging.getLogger(__name__)

PUBLIC_APPVIEWS =("https://public.api.bsky.app", "https://api.bsky.app")
PDS = "https://bsky.social"
JETSTREAM = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"
PAGE = 100  # searchPosts/getAuthorFeed max page size

PRESETS = {
    "markets": '$SPY OR stocks OR "stock market"',
    "oil": 'oil OR crude OR OPEC OR "oil prices"',
    "fed": 'fed OR FOMC OR powell OR "rate cut" OR "interest rates"',
    "crypto": "bitcoin OR $BTC OR ethereum OR crypto",
}


class BlueskyError(RuntimeError):
    """An XRPC call failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _post_row(post: dict) -> dict:
    author = post.get("author") or {}
    record = post.get("record") or {}
    return {
        "uri": post.get("uri"),
        "cid": post.get("cid"),
        "author_handle": author.get("handle"),
        "author_did": author.get("did"),
        "text": record.get("text"),
        "created_at": record.get("createdAt"),
        "indexed_at": post.get("indexedAt"),
        "like_count": post.get("likeCount"),
        "repost_count": post.get("repostCount"),
        "reply_count": post.get("replyCount"),
        "quote_count": post.get("quoteCount"),
        "langs": record.get("langs"),
    }


def _or_terms(query: str) -> list[str]:
    """Splits ``a OR "b c"`` into ``["a", '"b c"']``, leaving quoted phrases intact."""
    terms = [t.strip() for t in re.split(r'\s+OR\s+(?=(?:[^"]*"[^"]*")*[^"]*$)', query)]
    return [t for t in terms if t]


def _matches(text: str, keywords: Sequence[str]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in keywords)


class BlueskyVendor(Vendor):
    name = "bluesky"
    description = "Bluesky posts: search, author feeds and the Jetstream firehose"

    def __init__(self, data_dir=None, handle: str | None = None, app_password: str | None = None):
        super().__init__(data_dir)
        self.handle = handle or os.environ.get("BSKY_HANDLE")
        self.app_password = app_password or os.environ.get("BSKY_APP_PASSWORD")
        self._token: str | None = None
        self.session = requests.Session()

    # ------------------------------------------------------------------ http
    def _login(self) -> str:
        r = self.session.post(
            f"{PDS}/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.app_password},
            timeout=30,
        )
        if r.status_code != 200:
            raise BlueskyError(f"createSession failed ({r.status_code}): {r.text[:200]}")
        self._token = r.json()["accessJwt"]
        return self._token

    def _get(self, method: str, params: dict) -> dict:
        """GETs an XRPC query, authed via the PDS if credentials are set, else the public AppView."""
        params = {k: v for k, v in params.items() if v is not None}
        if self.handle and self.app_password:
            for attempt in range(2):
                token = self._token or self._login()
                r = self.session.get(f"{PDS}/xrpc/{method}", params=params,
                                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
                if r.status_code == 400 and "ExpiredToken" in r.text and attempt == 0:
                    self._token = None
                    continue
                break
        else:
            # public.api.bsky.app 403s searchPosts for anonymous callers; api.bsky.app serves it.
            for host in PUBLIC_APPVIEWS:
                r = self.session.get(f"{host}/xrpc/{method}", params=params, timeout=30)
                if r.status_code not in (401, 403):
                    break
        if r.status_code != 200:
            hint = " (set BSKY_HANDLE / BSKY_APP_PASSWORD)" if r.status_code in (401, 403) else ""
            raise BlueskyError(f"{method} failed ({r.status_code}){hint}: {r.text[:200]}", r.status_code)
        return r.json()

    def _paginate(self, method: str, params: dict, items: str, limit: int) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while len(out) < limit:
            try:
                res = self._get(method, {**params, "limit": min(PAGE, limit - len(out)), "cursor": cursor})
            except BlueskyError as e:
                # The anonymous AppView 403s searchPosts once a cursor is sent; keep the first page.
                if cursor is None or e.status not in (401, 403):
                    raise
                log.warning("%s: stopped at %d posts; set BSKY_HANDLE/BSKY_APP_PASSWORD to page further", method, len(out))
                break
            page = res.get(items) or []
            out.extend(page)
            cursor = res.get("cursor")
            if not page or not cursor:
                break
        return out[:limit]

    # --------------------------------------------------------------- datasets
    @dataset(key=["uri"])
    def search(
        self,
        query: str | None = None,
        limit: int = 100,
        sort: str = "latest",
        since: str | None = None,
        until: str | None = None,
        lang: str | None = None,
        preset: str | None = None,
    ) -> pd.DataFrame:
        """Posts matching a query or preset; each OR term is its own search of up to limit posts."""
        if preset:
            if preset not in PRESETS:
                raise KeyError(f"Unknown preset {preset!r}; available: {sorted(PRESETS)}")
            query = PRESETS[preset]
        if not query:
            raise ValueError("Pass query= or preset=")
        # searchPosts ANDs every term and ignores OR, so each OR branch is its own search.
        rows: dict[str, dict] = {}
        for term in _or_terms(query):
            posts = self._paginate(
                "app.bsky.feed.searchPosts",
                {"q": term, "sort": sort, "since": since, "until": until, "lang": lang},
                "posts", int(limit),
            )
            for p in posts:
                rows.setdefault(p.get("uri"), {**_post_row(p), "query": query, "matched": term})
        df = pd.DataFrame(list(rows.values()))
        if not df.empty and sort == "latest":
            df = df.sort_values("created_at", ascending=False, kind="stable")
        return df

    @dataset(key=["uri"])
    def author_feed(self, actor: str = "bsky.app", limit: int = 100) -> pd.DataFrame:
        """Recent posts and reposts from one account (handle or DID)."""
        items = self._paginate("app.bsky.feed.getAuthorFeed", {"actor": actor}, "feed", int(limit))
        rows = []
        for item in items:
            row = _post_row(item.get("post") or {})
            reason = item.get("reason") or {}
            row["reposted_by"] = (reason.get("by") or {}).get("handle") if "repost" in reason.get("$type", "").lower() else None
            row["actor"] = actor
            rows.append(row)
        return pd.DataFrame(rows)

    # ----------------------------------------------------------------- stream
    async def stream(self, keywords: Sequence[str] | str | None = None, url: str = JETSTREAM) -> AsyncIterator[dict]:
        """New posts from Jetstream, optionally only those containing any of ``keywords``."""
        import websockets

        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        async with websockets.connect(url) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                commit = msg.get("commit") or {}
                if msg.get("kind") != "commit" or commit.get("operation") != "create":
                    continue
                record = commit.get("record") or {}
                text = record.get("text") or ""
                if keywords and not _matches(text, keywords):
                    continue
                yield {
                    "uri": f"at://{msg.get('did')}/{commit.get('collection')}/{commit.get('rkey')}",
                    "cid": commit.get("cid"),
                    "author_did": msg.get("did"),
                    "text": text,
                    "created_at": record.get("createdAt"),
                    "langs": record.get("langs"),
                    "time_us": msg.get("time_us"),
                }
