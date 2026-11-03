"""SEC EDGAR filings (scraper ported from Funkles-SEC).

Set ``SEC_USER_AGENT="Your Name your@email"`` -- SEC requires real contact info.
Raw filing documents are saved under ``data/sec/files/filings/``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, timedelta

import pandas as pd

from vendors.core import Vendor, dataset

from . import edgar

log = logging.getLogger(__name__)


def _ciks(value: Sequence[str] | str) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip().zfill(10) for v in value if str(v).strip()]


class SecVendor(Vendor):
    name = "sec"
    description = "SEC EDGAR full-text search, filing documents and XBRL company facts"

    @dataset(key=["accession", "filename"])
    def filings(
        self,
        form_type: str = "SCHEDULE 13D",
        days: int = 1,
        start: str | None = None,
        end: str | None = None,
        download: bool = True,
        xml_only: bool = True,
        enrich: bool = True,
        max_results: int | None = 200,
    ) -> pd.DataFrame:
        """Filings of a form type in a date window; optionally downloads and parses them.

        Mirrors Funkles-SEC's scrape(): documents are saved to
        files/filings/<accession>_<filename>; Schedule 13D XML is parsed for
        issuer / reporting persons / aggregate shares, and (with ``enrich``)
        the stake is expressed as a share of the issuer's shares outstanding.
        """
        end_d = date.fromisoformat(end) if end else date.today()
        start_d = date.fromisoformat(start) if start else end_d - timedelta(days=days)
        hits = edgar.search_filings(form_type, start_d, end_d, max_results=max_results)

        rows = []
        shares_cache: dict[str, tuple] = {}
        for hit in hits:
            src = hit.get("_source", {})
            accession, filename = hit["_id"].split(":", 1)
            if xml_only and not filename.lower().endswith(".xml"):
                continue
            ciks = src.get("ciks") or []
            if not ciks:
                continue
            url = edgar.filing_url(ciks[0], accession, filename)
            row = {
                "accession": accession,
                "filename": filename,
                "form": src.get("form"),
                "file_type": src.get("file_type"),
                "file_date": src.get("file_date"),
                "ciks": ciks,
                "display_names": src.get("display_names"),
                "url": url,
                "path": None,
            }
            if download:
                try:
                    content = edgar.download(url)
                except Exception as e:
                    log.warning("Failed to download %s: %s", url, e)
                    rows.append(row)
                    continue
                path = self.storage.write_file(f"filings/{accession}_{filename}", content)
                row["path"] = str(path)
                if filename.lower().endswith(".xml") and "13D" in (src.get("form") or "").upper():
                    try:
                        row.update(edgar.parse_schedule_13d(content))
                    except Exception as e:
                        log.warning("Failed to parse %s: %s", url, e)
                    if enrich and row.get("issuer_cik") and row.get("aggregate_amount_owned"):
                        cik = row["issuer_cik"]
                        if cik not in shares_cache:
                            shares_cache[cik] = edgar.get_outstanding_shares(cik)
                        outstanding, _ = shares_cache[cik]
                        row["shares_outstanding"] = outstanding
                        row["share_pct"] = (
                            row["aggregate_amount_owned"] / outstanding if outstanding else None
                        )
            rows.append(row)
        return pd.DataFrame(rows)

    @dataset(key=["cik"])
    def shares_outstanding(self, ciks: Sequence[str] | str) -> pd.DataFrame:
        """Latest reported common shares outstanding (XBRL dei facts) per CIK."""
        rows = []
        for cik in _ciks(ciks):
            val, period_end = edgar.get_outstanding_shares(cik)
            rows.append({"cik": cik, "shares_outstanding": val, "period_end": period_end})
        return pd.DataFrame(rows)

    @dataset(key=["accession_number"])
    def company_filings(self, cik: str, form_type: str | None = None) -> pd.DataFrame:
        """A company's recent filing index (data.sec.gov/submissions), optionally one form type."""
        sub = edgar.submissions(cik)
        df = pd.DataFrame(sub.get("filings", {}).get("recent", {}))
        if df.empty:
            return df
        df.insert(0, "cik", str(cik).zfill(10))
        df.insert(1, "company", sub.get("name"))
        if form_type:
            df = df[df["form"] == form_type]
        return df
