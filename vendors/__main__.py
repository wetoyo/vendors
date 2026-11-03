"""Command line front-end shared by every vendor.

    python -m vendors list
    python -m vendors datasets deribit
    python -m vendors fetch   deribit index_price index_name=eth_usd
    python -m vendors collect kalshi markets series_ticker=KXFED
    python -m vendors load    kalshi markets status=active --limit 20
    python -m vendors export  yahoo prices --format csv

Parameters are ``key=value``; values are parsed as JSON when possible
(``max_pages=5``, ``expired=true``), otherwise kept as strings.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from .core import available, get_vendor, vendor_class


def _parse_params(items: list[str]) -> dict:
    out = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Bad parameter {item!r}; expected key=value")
        k, v = item.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _show(df: pd.DataFrame, rows: int) -> None:
    with pd.option_context("display.max_columns", 12, "display.width", 160):
        print(df.head(rows).to_string() if not df.empty else "(no rows)")
    print(f"\n[{len(df)} rows x {len(df.columns)} cols]")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vendors", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", help="Override this vendor's storage folder")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List registered vendors")
    d = sub.add_parser("datasets", help="List a vendor's datasets and their parameters")
    d.add_argument("vendor")
    for name, help_ in [("fetch", "Pull a dataset and print it (no storage)"),
                        ("collect", "Pull a dataset and store it in data/<vendor>/")]:
        c = sub.add_parser(name, help=help_)
        c.add_argument("vendor")
        c.add_argument("dataset")
        c.add_argument("params", nargs="*", metavar="key=value")
        c.add_argument("--rows", type=int, default=10)
    ld = sub.add_parser("load", help="Read a stored dataset (key=value equality filters)")
    ld.add_argument("vendor")
    ld.add_argument("dataset")
    ld.add_argument("filters", nargs="*", metavar="key=value")
    ld.add_argument("--limit", type=int)
    ld.add_argument("--rows", type=int, default=10)
    ex = sub.add_parser("export", help="Export a stored dataset to data/<vendor>/exports/")
    ex.add_argument("vendor")
    ex.add_argument("dataset")
    ex.add_argument("--format", default="parquet", choices=["parquet", "csv"])

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if a.cmd == "list":
        for name in available():
            try:
                cls = vendor_class(name)
                print(f"{name:10s} {cls.description}")
            except ImportError as e:
                print(f"{name:10s} (unavailable: {e})")
        return 0

    if a.cmd == "datasets":
        for ds in vendor_class(a.vendor).datasets().values():
            key = ",".join(ds.key) if ds.key else "-"
            print(f"{ds.name}  [{ds.mode}; key={key}]\n    {ds.description}")
            params = ", ".join(f"{k}={v!r}" for k, v in ds.params.items())
            print(f"    params: {params or '(none)'}")
        return 0

    v = get_vendor(a.vendor, data_dir=a.data_dir)
    if a.cmd == "fetch":
        _show(v.fetch(a.dataset, **_parse_params(a.params)), a.rows)
    elif a.cmd == "collect":
        df = v.collect(a.dataset, **_parse_params(a.params))
        _show(df, a.rows)
        print(f"stored in {v.storage.db_path} (table {a.dataset})")
    elif a.cmd == "load":
        _show(v.load(a.dataset, limit=a.limit, **_parse_params(a.filters)), a.rows)
    elif a.cmd == "export":
        print(v.storage.export(a.dataset, fmt=a.format))
    return 0


if __name__ == "__main__":
    sys.exit(main())
