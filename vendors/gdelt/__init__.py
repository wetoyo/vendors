"""GDELT DOC 2.0 news search (keyless): articles, volume/tone timelines and tone histograms."""

from __future__ import annotations

import pandas as pd

from vendors.core import Vendor, dataset

from . import client

PRESETS: dict[str, str] = {
    "oil": '("crude oil" OR OPEC OR "oil prices")',
    "fed": '("Federal Reserve" OR FOMC) sourcelang:english',
    "shipping": '(tanker OR "strait of hormuz" OR "red sea shipping")',
    "crypto": '(bitcoin OR ethereum OR cryptocurrency OR "crypto market")',
    "geopolitics": '(sanctions OR "military strike" OR invasion OR ceasefire OR "trade war")',
}

TIMELINE_MODES = ("TimelineVol", "TimelineVolRaw", "TimelineTone")


def _query(query: str | None, preset: str | None) -> str:
    if preset:
        if preset not in PRESETS:
            raise KeyError(f"Unknown preset {preset!r}; available: {sorted(PRESETS)}")
        return PRESETS[preset]
    if not query:
        raise ValueError("Pass query= or preset=")
    return query


def _window(timespan: str | None, start: str | None, end: str | None) -> dict:
    """Uses start/end (YYYYMMDDHHMMSS) when given, otherwise timespan."""
    if start or end:
        return {k: v for k, v in (("startdatetime", start), ("enddatetime", end)) if v}
    return {"timespan": timespan}


def _parse_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce")


class GdeltVendor(Vendor):
    name = "gdelt"
    description = "GDELT DOC 2.0 global news search: articles, coverage volume and tone"

    @dataset(key=["url"])
    def articles(
        self,
        query: str | None = None,
        timespan: str = "1d",
        start: str | None = None,
        end: str | None = None,
        max_records: int = 250,
        sourcelang: str | None = None,
        sourcecountry: str | None = None,
        preset: str | None = None,
    ) -> pd.DataFrame:
        """News articles matching a query (up to 250 per call)."""
        q = _query(query, preset)
        full = q
        if sourcelang:
            full += f" sourcelang:{sourcelang}"
        if sourcecountry:
            full += f" sourcecountry:{sourcecountry}"
        data = client.doc(full, "ArtList", maxrecords=min(int(max_records), 250), sort="DateDesc",
                          **_window(timespan, start, end))
        cols = ["url", "title", "seendate", "domain", "language", "sourcecountry", "socialimage"]
        df = pd.DataFrame(data.get("articles") or [], columns=cols)
        df["seendate"] = _parse_date(df["seendate"])
        df.insert(0, "query", q)
        return df

    @dataset(key=["query", "mode", "date", "series"])
    def timeline(
        self,
        query: str | None = None,
        mode: str = "TimelineVolRaw",
        timespan: str = "1w",
        preset: str | None = None,
    ) -> pd.DataFrame:
        """Coverage volume or average tone over time (TimelineVol, TimelineVolRaw, TimelineTone)."""
        if mode not in TIMELINE_MODES:
            raise ValueError(f"mode must be one of {TIMELINE_MODES}")
        q = _query(query, preset)
        data = client.doc(q, mode, timespan=timespan)
        rows = [
            {"query": q, "mode": mode, "series": s.get("series"), **point}
            for s in data.get("timeline") or []
            for point in s.get("data") or []
        ]
        df = pd.DataFrame(rows, columns=["query", "mode", "series", "date", "value", "norm"])
        df["date"] = _parse_date(df["date"])
        return df.dropna(axis=1, how="all")

    @dataset()
    def tone(self, query: str | None = None, timespan: str = "1w", preset: str | None = None) -> pd.DataFrame:
        """Histogram of article tone, one row per tone bin (snapshot)."""
        q = _query(query, preset)
        data = client.doc(q, "ToneChart", timespan=timespan)
        rows = [
            {"query": q, "timespan": timespan, "bin": b.get("bin"), "count": b.get("count"),
             "top_articles": b.get("toparts") or []}
            for b in data.get("tonechart") or []
        ]
        return pd.DataFrame(rows, columns=["query", "timespan", "bin", "count", "top_articles"])
