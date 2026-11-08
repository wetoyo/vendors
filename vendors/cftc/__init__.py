"""CFTC Commitments of Traders (futures-only) from publicreporting.cftc.gov (Socrata).

Optional: set ``SOCRATA_APP_TOKEN`` for higher rate limits.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import pandas as pd
import requests

from vendors.core import Vendor, dataset

BASE_URL = "https://publicreporting.cftc.gov/resource/{}.json"
DATE = "report_date_as_yyyy_mm_dd"
CODE = "cftc_contract_market_code"
NAME = "market_and_exchange_names"

# report -> (Socrata dataset id, net column, long column, short column)
REPORTS = {
    "legacy": ("6dca-aqww", "noncomm_net", "noncomm_positions_long_all", "noncomm_positions_short_all"),
    "disaggregated": ("72hh-3qpy", "m_money_net", "m_money_positions_long_all", "m_money_positions_short_all"),
    "tff": ("gpe5-46if", "lev_money_net", "lev_money_positions_long", "lev_money_positions_short"),
}

# preset -> (contract code, default report). Financial futures aren't in the disaggregated report.
PRESETS = {
    "wti": ("067651", "disaggregated"),        # WTI-PHYSICAL, NYMEX
    "brent": ("06765T", "disaggregated"),      # BRENT LAST DAY, NYMEX (ICE Europe Brent isn't CFTC-reported)
    "natgas": ("023651", "disaggregated"),     # NAT GAS NYME
    "gold": ("088691", "disaggregated"),       # GOLD, COMEX
    "copper": ("085692", "disaggregated"),     # COPPER- #1, COMEX
    "corn": ("002602", "disaggregated"),       # CORN, CBOT
    "soybeans": ("005602", "disaggregated"),   # SOYBEANS, CBOT
    "wheat": ("001602", "disaggregated"),      # WHEAT-SRW, CBOT
    "sp500_emini": ("13874A", "tff"),          # E-MINI S&P 500, CME
    "ust10y": ("043602", "tff"),               # UST 10Y NOTE, CBOT
    "eur": ("099741", "tff"),                  # EURO FX, CME
    "bitcoin": ("133741", "tff"),              # BITCOIN, CME
}

_TEXT = {"id", NAME, DATE, "yyyy_report_week_ww", "contract_units", "futonly_or_combined", "report"}


def _headers() -> dict:
    token = os.environ.get("SOCRATA_APP_TOKEN")
    return {"X-App-Token": token} if token else {}


def _get(dataset_id: str, params: dict) -> list[dict]:
    r = requests.get(BASE_URL.format(dataset_id), params=params, headers=_headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _report(report: str) -> tuple[str, str, str, str]:
    if report not in REPORTS:
        raise ValueError(f"Unknown report {report!r}; choose from {sorted(REPORTS)}")
    return REPORTS[report]


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if c in _TEXT or c.endswith(("_code", "_name", "_names")) or c.startswith("commodity"):
            continue
        conv = pd.to_numeric(df[c], errors="coerce")
        if conv.notna().sum() == df[c].notna().sum():
            df[c] = conv
    return df.copy()  # defragment after column-by-column conversion


class CftcVendor(Vendor):
    name = "cftc"
    description = "CFTC Commitments of Traders: legacy, disaggregated and TFF futures-only reports"

    @dataset(key=[DATE, CODE, "report"])
    def cot(
        self,
        report: str | None = None,
        market: str | None = None,
        code: Sequence[str] | str | None = None,
        preset: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 5000,
    ) -> pd.DataFrame:
        """Weekly COT positions per market, with a net speculator column."""
        codes = [c.strip() for c in code.split(",")] if isinstance(code, str) else [str(c) for c in code or []]
        if preset:
            if preset not in PRESETS:
                raise ValueError(f"Unknown preset {preset!r}; choose from {sorted(PRESETS)}")
            preset_code, preset_report = PRESETS[preset]
            codes.append(preset_code)
            report = report or preset_report
        report = report or "disaggregated"
        dataset_id, net, long_col, short_col = _report(report)

        where = []
        if codes:
            where.append(f"{CODE} in ({', '.join(_quote(c) for c in codes)})")
        if market:
            where.append(f"upper({NAME}) like {_quote('%' + market.upper() + '%')}")
        if start:
            where.append(f"{DATE} >= {_quote(start)}")
        if end:
            where.append(f"{DATE} <= {_quote(end)}")

        rows, offset = [], 0
        while True:
            params = {"$limit": limit, "$offset": offset, "$order": f"{DATE}, {CODE}, id"}
            if where:
                params["$where"] = " AND ".join(where)
            page = _get(dataset_id, params)
            rows.extend(page)
            if len(page) < limit:
                break
            offset += limit

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df[DATE] = df[DATE].str[:10]
        df.insert(0, "report", report)
        df = _numeric(df)
        if long_col in df and short_col in df:
            df[net] = df[long_col] - df[short_col]
        return df

    @dataset(key=[CODE])
    def markets(self, report: str = "disaggregated") -> pd.DataFrame:
        """Markets and contract codes in the latest report."""
        dataset_id = _report(report)[0]
        latest = _get(dataset_id, {"$select": f"max({DATE}) as latest"})[0]["latest"]
        rows = _get(dataset_id, {
            "$select": f"{CODE}, {NAME}, contract_market_name, commodity_name",
            "$where": f"{DATE} = {_quote(latest)}",
            "$order": CODE,
            "$limit": 50000,
        })
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df.insert(0, "report", report)
        df[DATE] = latest[:10]
        return df.drop_duplicates(subset=[CODE])
