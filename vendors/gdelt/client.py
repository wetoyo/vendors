"""Throttled HTTP client for the GDELT DOC 2.0 API (about one request per 5 seconds)."""

from __future__ import annotations

import json
import threading
import time

import requests

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
HEADERS = {"User-Agent": "vendors-gdelt/0.1 (+https://github.com/wetoyo/vendors)"}
MIN_INTERVAL = 5.5
RETRIES = 3

_lock = threading.Lock()
_last = 0.0


class GdeltError(RuntimeError):
    """GDELT returned an error message instead of JSON."""


def _throttle() -> None:
    global _last
    with _lock:
        wait = _last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def get(url: str, params: dict) -> requests.Response:
    """GET with the shared throttle; retries 429s with backoff."""
    for attempt in range(RETRIES + 1):
        _throttle()
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        limited = r.status_code == 429 or r.text.startswith("Please limit requests")
        if not limited or attempt == RETRIES:
            r.raise_for_status()
            return r
        time.sleep(MIN_INTERVAL * (attempt + 1))
    raise AssertionError("unreachable")


def doc(query: str, mode: str, **params) -> dict:
    """Calls the DOC API in ``mode`` and returns the parsed JSON (``{}`` when no results)."""
    p = {"query": query, "mode": mode, "format": "json", **{k: v for k, v in params.items() if v is not None}}
    text = get(DOC_URL, p).text.strip()
    if not text:
        return {}
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        raise GdeltError(text[:300]) from None
