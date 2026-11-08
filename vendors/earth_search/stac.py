"""Minimal STAC API client for Element84 Earth Search (keyless)."""

from __future__ import annotations

import requests

API = "https://earth-search.aws.element84.com/v1"
HEADERS = {"User-Agent": "vendors-client/0.1"}
TIMEOUT = 60


def search(body: dict, max_items: int | None = None) -> list[dict]:
    """POST /search, following ``next`` links until ``max_items`` features are collected."""
    features: list[dict] = []
    url, payload = f"{API}/search", dict(body)
    while url:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        page = r.json()
        features.extend(page.get("features", []))
        if max_items is not None and len(features) >= max_items:
            return features[:max_items]
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        if not nxt or not page.get("features"):
            break
        url = nxt.get("href")
        payload = {**payload, **(nxt.get("body") or {})}
    return features


def get_item(collection: str, item_id: str) -> dict:
    r = requests.get(f"{API}/collections/{collection}/items/{item_id}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def https_href(href: str) -> str:
    """``s3://bucket/key`` -> ``https://bucket.s3.amazonaws.com/key``; other hrefs unchanged."""
    if href.startswith("s3://"):
        bucket, _, key = href[5:].partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return href


def download(href: str) -> bytes:
    url = https_href(href)
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 403 and href.startswith("s3://"):
        raise PermissionError(f"{href} is in a requester-pays bucket; fetch it with AWS credentials")
    r.raise_for_status()
    return r.content
