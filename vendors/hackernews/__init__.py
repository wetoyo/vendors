"""Hacker News via the Algolia search API and the official Firebase API (both keyless)."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from vendors.core import Vendor, dataset

ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
LISTS = ("topstories", "newstories", "beststories", "askstories", "showstories", "jobstories")
TIMEOUT = 30
PAGE_SIZE = 100


def _get(url: str, params: dict | None = None):
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


class HackerNewsVendor(Vendor):
    name = "hackernews"
    description = "Hacker News stories, front-page lists and comment trees"

    @dataset(key=["object_id"])
    def search(
        self, query: str = "", tags: str = "story", sort: str = "date", days: int = 7, hits: int = 200
    ) -> pd.DataFrame:
        """Algolia search over the last `days` days, sorted by date or relevance."""
        endpoint = "search_by_date" if sort == "date" else "search"
        since = int(time.time()) - int(days) * 86400
        params = {"query": query, "tags": tags, "numericFilters": f"created_at_i>{since}",
                  "hitsPerPage": min(PAGE_SIZE, int(hits))}
        rows, page = [], 0
        while len(rows) < int(hits):
            data = _get(f"{ALGOLIA}/{endpoint}", {**params, "page": page})
            for h in data.get("hits", []):
                rows.append({
                    "object_id": h.get("objectID"),
                    "title": h.get("title") or h.get("story_title"),
                    "url": h.get("url") or h.get("story_url"),
                    "author": h.get("author"),
                    "points": h.get("points"),
                    "num_comments": h.get("num_comments"),
                    "created_at": h.get("created_at"),
                    "story_text": h.get("story_text") or h.get("comment_text"),
                    "tags": json.dumps(h.get("_tags") or []),
                    "query": query,
                })
            page += 1
            if not data.get("hits") or page >= data.get("nbPages", 0):
                break
        return pd.DataFrame(rows[: int(hits)])

    @dataset(key=["id"])
    def top(self, list: str = "topstories", limit: int = 100) -> pd.DataFrame:
        """Current Firebase story list (top/new/best/ask/show/job) with full items."""
        name = list if list.endswith("stories") else f"{list}stories"
        if name not in LISTS:
            raise ValueError(f"Unknown list {list!r}; use one of {LISTS}")
        ids = _get(f"{FIREBASE}/{name}.json")[: int(limit)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = pool.map(lambda i: _get(f"{FIREBASE}/item/{i}.json"), ids)
            rows = []
            for rank, item in enumerate(items, 1):
                if not item:
                    continue
                item.pop("kids", None)
                item["rank"], item["list"] = rank, name
                if item.get("time"):
                    item["created_at"] = pd.Timestamp(item["time"], unit="s", tz="UTC").isoformat()
                rows.append(item)
        return pd.DataFrame(rows)

    @dataset(key=["id"])
    def comments(self, story_id: int) -> pd.DataFrame:
        """A story's comment tree (Algolia /items), flattened with parent_id and depth."""
        root = _get(f"{ALGOLIA}/items/{story_id}")
        rows, stack = [], [(c, 1) for c in reversed(root.get("children") or [])]
        while stack:
            node, depth = stack.pop()
            rows.append({
                "id": node.get("id"),
                "story_id": node.get("story_id") or root.get("id"),
                "parent_id": node.get("parent_id"),
                "depth": depth,
                "author": node.get("author"),
                "text": node.get("text"),
                "created_at": node.get("created_at"),
            })
            stack.extend((c, depth + 1) for c in reversed(node.get("children") or []))
        return pd.DataFrame(rows)
