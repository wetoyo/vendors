"""Minimal Reddit HTTP client: anonymous JSON or app-only OAuth, throttled, paginated."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import requests

WWW = "https://www.reddit.com"
OAUTH = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
DEFAULT_USER_AGENT = "vendors-client/0.1"
MIN_INTERVAL = 1.0  # seconds between requests
PAGE_SIZE = 100  # Reddit's max listing page


def user_agent() -> str:
    return os.environ.get("REDDIT_USER_AGENT") or DEFAULT_USER_AGENT


def credentials() -> tuple[str, str] | None:
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


class RedditClient:
    """Uses OAuth when REDDIT_CLIENT_ID/SECRET are set, else anonymous www.reddit.com JSON."""

    def __init__(self, min_interval: float = MIN_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0
        self._token: str | None = None
        self._token_expiry = 0.0

    @property
    def oauth(self) -> bool:
        return credentials() is not None

    def _throttle(self) -> None:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token
        self._throttle()
        r = requests.post(
            TOKEN_URL,
            auth=credentials(),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": user_agent()},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + float(body.get("expires_in", 3600)) - 60
        return self._token

    def get(self, path: str, **params) -> dict | list:
        headers = {"User-Agent": user_agent()}
        params = {"raw_json": 1, **{k: v for k, v in params.items() if v is not None}}
        if self.oauth:
            headers["Authorization"] = f"bearer {self.token()}"
            url = f"{OAUTH}{path}"
        else:
            url = f"{WWW}{path}.json"
        self._throttle()
        r = requests.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def listing(self, path: str, limit: int, **params) -> Iterator[dict]:
        """Yields up to ``limit`` children ``data`` dicts, following ``after``."""
        after, n = None, 0
        while n < limit:
            body = self.get(path, limit=min(PAGE_SIZE, limit - n), after=after, **params)
            data = body.get("data", {})
            children = data.get("children", [])
            for child in children:
                yield child.get("data", {})
                n += 1
                if n >= limit:
                    return
            after = data.get("after")
            if not after or not children:
                return
