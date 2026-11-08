"""Generic RSS / Atom news collector with preset feeds (stdlib XML parsing, no extra deps).

Set ``RSS_USER_AGENT`` to override the default generic User-Agent.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import pandas as pd
import requests

from vendors.core import Vendor, dataset

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; vendors-rss/1.0)"
TIMEOUT = 20
COLUMNS = ["feed", "group", "title", "link", "published", "summary", "author", "guid", "categories"]

# name -> (group, title, url)
_PRESETS: list[tuple[str, str, str, str]] = [
    # markets
    ("cnbc_top", "markets", "CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("marketwatch_top", "markets", "MarketWatch Top Stories", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("yahoo_finance", "markets", "Yahoo Finance S&P 500 Headlines",
     "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
    ("wsj_markets", "markets", "WSJ Markets", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ("ft_markets", "markets", "Financial Times Markets", "https://www.ft.com/markets?format=rss"),
    ("ft_home", "markets", "Financial Times Home", "https://www.ft.com/rss/home"),
    ("bloomberg_markets", "markets", "Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
    # energy
    ("oilprice", "energy", "OilPrice.com", "https://oilprice.com/rss/main"),
    ("eia_today", "energy", "EIA Today in Energy", "https://www.eia.gov/rss/todayinenergy.xml"),
    ("rigzone", "energy", "Rigzone Latest News", "https://www.rigzone.com/news/rss/rigzone_latest.aspx"),
    # central banks
    ("fed_press", "central_banks", "Federal Reserve Press Releases", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ecb_press", "central_banks", "ECB Press Releases", "https://www.ecb.europa.eu/rss/press.html"),
    ("boe_news", "central_banks", "Bank of England News", "https://www.bankofengland.co.uk/rss/news"),
    # macro
    ("bls_empsit", "macro", "BLS Employment Situation", "https://www.bls.gov/feed/empsit.rss"),
    ("bls_cpi", "macro", "BLS Consumer Price Index", "https://www.bls.gov/feed/cpi.rss"),
    ("bls_ppi", "macro", "BLS Producer Price Index", "https://www.bls.gov/feed/ppi.rss"),
    ("bls_jolts", "macro", "BLS JOLTS", "https://www.bls.gov/feed/jolts.rss"),
    ("bls_eci", "macro", "BLS Employment Cost Index", "https://www.bls.gov/feed/eci.rss"),
    # crypto
    ("coindesk", "crypto", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("the_block", "crypto", "The Block", "https://www.theblock.co/rss.xml"),
    ("cointelegraph", "crypto", "Cointelegraph", "https://cointelegraph.com/rss"),
    # general
    ("bbc_business", "general", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("nyt_business", "general", "NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    # tech
    ("techcrunch", "tech", "TechCrunch", "https://techcrunch.com/feed/"),
    ("ars_technica", "tech", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    # regulators
    ("sec_press", "sec", "SEC Press Releases", "https://www.sec.gov/news/pressreleases.rss"),
]

FEEDS: dict[str, dict[str, str]] = {
    name: {"group": group, "title": title, "url": url} for name, group, title, url in _PRESETS
}
GROUPS: dict[str, list[str]] = {}
for _name, _f in FEEDS.items():
    GROUPS.setdefault(_f["group"], []).append(_name)


# ------------------------------------------------------------------ parsing
def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _text(el, *names: str) -> str | None:
    """First non-empty text among child elements named ``names`` (namespace-agnostic)."""
    for name in names:
        for c in _children(el, name):
            t = "".join(c.itertext()).strip()
            if t:
                return t
    return None


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(text: str | None) -> str | None:
    """Removes HTML tags and entities and collapses whitespace."""
    if not text:
        return None
    out = _WS.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()
    return out or None


def parse_date(value: str | None) -> str | None:
    """RFC 822 or ISO 8601 date -> ISO 8601 string in UTC (naive taken as UTC; unparseable -> None)."""
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _atom_link(el) -> str | None:
    links = _children(el, "link")
    for want in ("alternate", None):
        for ln in links:
            rel = ln.get("rel")
            href = ln.get("href") or (ln.text or "").strip()
            if href and (rel == want or (want == "alternate" and rel is None)):
                return href
    for ln in links:
        href = ln.get("href") or (ln.text or "").strip()
        if href:
            return href
    return None


def _author(el) -> str | None:
    for a in _children(el, "author"):
        name = _text(a, "name")
        if name:
            return name
        t = (a.text or "").strip()
        if t:
            return t
    return _text(el, "creator")


def _categories(el) -> list[str]:
    out = []
    for c in _children(el, "category") + _children(el, "subject"):
        v = c.get("term") or c.get("label") or (c.text or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def parse_feed(content: bytes | str) -> list[dict]:
    """Parses RSS 2.0, RSS 1.0 (RDF) or Atom XML into item dicts."""
    root = ET.fromstring(content.encode() if isinstance(content, str) else content)
    rows = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        guid = _text(el, "guid", "id")
        link = _atom_link(el) or (guid if guid and guid.startswith("http") else None)
        summary = _text(el, "description", "summary") or _text(el, "encoded", "content")
        rows.append({
            "title": strip_html(_text(el, "title")),
            "link": link.strip() if link else None,
            "published": parse_date(_text(el, "pubDate", "published", "date", "updated", "issued", "modified")),
            "summary": strip_html(summary),
            "author": _author(el),
            "guid": guid,
            "categories": json.dumps(_categories(el)),
        })
    return rows


# ------------------------------------------------------------------- vendor
def _names(value: Sequence[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [v.strip() for v in value if v and v.strip()]


class RssVendor(Vendor):
    name = "rss"
    description = "RSS/Atom news feeds: markets, energy, central banks, macro, crypto, general, tech, SEC"

    def __init__(self, data_dir=None, user_agent: str | None = None):
        super().__init__(data_dir)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or os.environ.get("RSS_USER_AGENT") or DEFAULT_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        })

    def _get(self, url: str) -> bytes:
        r = self.session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.content

    def _fetch_one(self, name: str, group: str | None, url: str) -> list[dict]:
        rows = parse_feed(self._get(url))
        for r in rows:
            r["feed"], r["group"] = name, group
        return rows

    def _targets(self, feed, group, url) -> list[tuple[str, str | None, str]]:
        targets = []
        for u in _names(url):
            targets.append((urlparse(u).netloc or u, None, u))
        for g in _names(group):
            if g not in GROUPS:
                raise KeyError(f"Unknown rss group {g!r}; available: {sorted(GROUPS)}")
            targets += [(n, g, FEEDS[n]["url"]) for n in GROUPS[g]]
        for n in _names(feed):
            if n not in FEEDS:
                raise KeyError(f"Unknown rss feed {n!r}; available: {sorted(FEEDS)}")
            targets.append((n, FEEDS[n]["group"], FEEDS[n]["url"]))
        if not (feed or group or url):
            targets = [(n, f["group"], f["url"]) for n, f in FEEDS.items()]
        return list(dict.fromkeys(targets))

    @dataset(key=["link"])
    def items(
        self,
        feed: Sequence[str] | str | None = None,
        group: Sequence[str] | str | None = None,
        url: Sequence[str] | str | None = None,
    ) -> pd.DataFrame:
        """News items from preset feeds, a group, an arbitrary URL, or all presets."""
        targets = self._targets(feed, group, url)
        errors: list[Exception] = []

        def run(t):
            try:
                return self._fetch_one(*t)
            except Exception as e:  # one bad feed shouldn't kill the batch
                log.warning("rss feed %s (%s) failed: %s", t[0], t[2], e)
                errors.append(e)
                return []

        with ThreadPoolExecutor(max_workers=min(8, len(targets) or 1)) as ex:
            rows = [r for batch in ex.map(run, targets) for r in batch]
        if targets and len(errors) == len(targets):
            raise RuntimeError(f"All {len(targets)} rss feeds failed; last error: {errors[-1]}")
        df = pd.DataFrame(rows, columns=COLUMNS)
        df = df[df["link"].notna()]
        return df.drop_duplicates(subset=["link"], keep="first")

    @dataset(key=["name"])
    def feeds(self) -> pd.DataFrame:
        """Preset feeds with their group and URL."""
        return pd.DataFrame(
            [{"name": n, **f} for n, f in FEEDS.items()], columns=["name", "group", "title", "url"]
        )

    def search(self, query: str, group: Sequence[str] | str | None = None) -> pd.DataFrame:
        """Live items whose title or summary contains ``query`` (case-insensitive)."""
        df = self.fetch("items", group=group)
        q = query.lower()
        hit = df["title"].fillna("").str.lower().str.contains(q, regex=False) | df["summary"].fillna(
            ""
        ).str.lower().str.contains(q, regex=False)
        return df[hit].reset_index(drop=True)
