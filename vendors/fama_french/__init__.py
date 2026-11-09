"""Kenneth French Data Library (Fama-French factors, no key).

``factors`` pulls the 3-factor file (Mkt-RF, SMB, HML, RF) as decimals, monthly
from 1926-07 or daily from 1926-07-01. ``mkt = mkt_rf + rf`` is the
dividend-inclusive US market return.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import requests

from vendors.core import Vendor, dataset

BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FILES = {
    "monthly": "F-F_Research_Data_Factors_CSV.zip",
    "daily": "F-F_Research_Data_Factors_daily_CSV.zip",
}


def download(file: str) -> str:
    """Returns the CSV text inside one of the library's zip files."""
    r = requests.get(f"{BASE_URL}/{file}", timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return z.read(z.namelist()[0]).decode("latin-1")


def parse_factors(text: str, frequency: str) -> pd.DataFrame:
    """Parses the first table of a factor CSV (the monthly file's annual block is skipped)."""
    lines = [l for l in text.splitlines() if l.strip()]
    start = next(i for i, l in enumerate(lines) if l.replace(" ", "").startswith(",Mkt-RF"))
    header = [h.strip() for h in lines[start].split(",")]
    width = 6 if frequency == "monthly" else 8
    rows = []
    for l in lines[start + 1:]:
        parts = [p.strip() for p in l.split(",")]
        if len(parts[0]) != width or not parts[0].isdigit():
            break
        rows.append(parts)
    df = pd.DataFrame(rows, columns=["period", *header[1:]])
    fmt = "%Y%m" if frequency == "monthly" else "%Y%m%d"
    dates = pd.to_datetime(df.pop("period"), format=fmt)
    if frequency == "monthly":
        dates = dates + pd.offsets.MonthEnd(0)
    out = df.astype(float) / 100.0
    out.insert(0, "date", dates.dt.strftime("%Y-%m-%d"))
    out.insert(0, "frequency", frequency)
    out["mkt"] = out["Mkt-RF"] + out["RF"]
    return out


class FamaFrenchVendor(Vendor):
    name = "fama_french"
    description = "Kenneth French Data Library factor returns"

    @dataset(key=["frequency", "date"])
    def factors(self, frequency: str = "monthly") -> pd.DataFrame:
        """Fama-French 3 factors + RF + market return, as decimals; monthly rows dated month-end."""
        if frequency not in FILES:
            raise ValueError(f"frequency must be one of {sorted(FILES)}")
        return parse_factors(download(FILES[frequency]), frequency)
