"""SEC EDGAR client, ported from Funkles-SEC (FunklesScraper/scrape.py + scrape_utils.py).

Changes from the original:
  * the filing search calls EDGAR full-text search (efts.sec.gov) directly
    instead of going through ``datamule.Index`` -- same endpoint, same hit
    format, one less heavy dependency -- and follows pagination;
  * download, Schedule 13D XML parsing and the shares-outstanding lookup are
    split into separate functions so the vendor adapter can compose them;
  * the issuer's CIK is read from the 13D XML (``issuerCIK``) for the
    shares-outstanding lookup, rather than assuming ``ciks[0]`` is the issuer;
  * requests are throttled to stay under SEC's 10 req/s fair-access limit;
  * the User-Agent comes from ``$SEC_USER_AGENT`` (SEC requires real contact
    info). There is deliberately no fallback: the original hardcoded a
    personal name/email, and every caller must identify themselves.
"""

from __future__ import annotations

import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
DATA_URL = "https://data.sec.gov"

NS_13D = {"edgar": "http://www.sec.gov/edgar/schedule13D", "com": "http://www.sec.gov/edgar/common"}

_session = requests.Session()
_lock = threading.Lock()
_last_request = 0.0
MIN_INTERVAL = 0.12  # seconds between requests (< 10 req/s)


class MissingUserAgent(RuntimeError):
    pass


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise MissingUserAgent(
            "SEC requires a User-Agent with your contact info. "
            'Set it first, e.g. export SEC_USER_AGENT="Your Name you@example.com"'
        )
    return ua


def _get(url: str, params: dict | None = None, retries: int = 4) -> requests.Response:
    global _last_request
    for attempt in range(retries):
        with _lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
        try:
            r = _session.get(
                url, params=params, timeout=20,
                headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"},
            )
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 503) and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")


def search_filings(form_type: str, start: date, end: date, max_results: int | None = 500) -> list[dict]:
    """Full-text-search hits (``{"_id": "accession:filename", "_source": {...}}``) for a form type."""
    hits: list[dict] = []
    offset = 0
    while True:
        params = {
            "forms": form_type,
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": offset,
        }
        payload = _get(EFTS_URL, params).json()
        page = payload.get("hits", {}).get("hits", [])
        hits.extend(page)
        total = payload.get("hits", {}).get("total", {}).get("value", 0)
        offset += len(page)
        if not page or offset >= total or (max_results and len(hits) >= max_results):
            break
    return hits[:max_results] if max_results else hits


def filing_url(cik: str, accession: str, filename: str) -> str:
    return f"{ARCHIVES_URL}/{int(cik)}/{accession.replace('-', '')}/{filename}"


def download(url: str) -> bytes:
    return _get(url).content


def parse_schedule_13d(xml_bytes: bytes) -> dict:
    """Issuer, reporting persons and aggregate shares owned from a Schedule 13D XML doc."""
    root = ET.fromstring(xml_bytes)

    def text(path: str) -> str | None:
        el = root.find(path, NS_13D)
        return el.text.strip() if el is not None and el.text else None

    reporting_persons = [
        rp.text.strip() for rp in root.findall(".//edgar:reportingPersonName", NS_13D) if rp.text
    ]
    share_amount = None
    for info in root.findall(".//edgar:reportingPersonInfo", NS_13D):
        agg = info.find("edgar:aggregateAmountOwned", NS_13D)
        if agg is not None and agg.text:
            share_amount = float(agg.text)
    return {
        "issuer": text(".//edgar:issuerName"),
        "issuer_cik": text(".//edgar:issuerCIK"),
        "issuer_cusip": text(".//edgar:issuerCUSIP"),
        "reporting_persons": reporting_persons,
        "aggregate_amount_owned": share_amount,
    }


def company_facts(cik: str) -> dict:
    return _get(f"{DATA_URL}/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json").json()


def get_outstanding_shares(cik: str) -> tuple[float | None, str | None]:
    """Most recent dei:EntityCommonStockSharesOutstanding as ``(value, period_end)``."""
    try:
        units = company_facts(cik)["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]
    except (requests.RequestException, KeyError, ValueError):
        return None, None
    entries = [e for unit in units.values() for e in unit]
    if not entries:
        return None, None
    latest = max(entries, key=lambda e: e["end"])
    return latest["val"], latest["end"]


def submissions(cik: str) -> dict:
    """Company metadata and recent filing index from data.sec.gov/submissions."""
    return _get(f"{DATA_URL}/submissions/CIK{str(cik).zfill(10)}.json").json()
