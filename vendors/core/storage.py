"""Per-vendor storage.

Every vendor owns exactly one folder, ``<data root>/<vendor>/``, and never
writes outside it:

    data/
      deribit/
        deribit.db          <- one SQLite table per dataset
        files/...           <- anything that isn't tabular (raw XML, PDFs, ...)
        exports/...         <- parquet/csv snapshots made with VendorStorage.export()
      kalshi/
        kalshi.db
      ...

The data root is ``$VENDORS_DATA_DIR`` if set, otherwise ``<repo>/data``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
INGESTED_AT = "_ingested_at"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def default_data_root() -> Path:
    env = os.environ.get("VENDORS_DATA_DIR")
    return Path(env).expanduser() if env else REPO_ROOT / "data"


def _ident(name: str) -> str:
    """Validates a table/column name and returns it double-quoted for SQL."""
    if not _IDENT.match(name):
        raise ValueError(f"Invalid SQL identifier {name!r}; use snake_case letters, digits and underscores")
    return f'"{name}"'


def _sqlite_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_integer_dtype(series):
        return "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def _to_sql_value(value):
    """Makes a single cell SQLite-safe: nested structures become JSON, timestamps ISO strings."""
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):  # numpy scalar
        value = value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


class VendorStorage:
    """Storage rooted at ``<data root>/<vendor>/``.

    Tabular data goes into ``<vendor>.db`` (one table per dataset, upserted on
    the dataset's key). Everything else goes under ``files/`` via ``path()``.
    """

    def __init__(self, vendor: str, root: str | Path | None = None):
        self.vendor = vendor
        self.root = Path(root) if root is not None else default_data_root() / vendor
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / f"{vendor}.db"

    # ------------------------------------------------------------------ files
    def path(self, *parts: str) -> Path:
        """Path for a non-tabular file under ``files/``; parent dirs are created."""
        p = self.root.joinpath("files", *parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_file(self, relpath: str, content: bytes | str) -> Path:
        p = self.path(*Path(relpath).parts)
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_bytes(content)
        return p

    # ----------------------------------------------------------------- tables
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def tables(self) -> list[str]:
        if not self.db_path.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def write(self, dataset: str, df: pd.DataFrame, key: Sequence[str] | None = None) -> int:
        """Writes ``df`` to table ``dataset``.

        With ``key``, rows are upserted (a re-fetch of the same key overwrites
        the old row). Without ``key``, rows are appended -- use that for
        snapshot/time-series datasets where every fetch is a new observation.
        The table is created on first write and new columns are added as they
        appear, so vendors don't need to declare schemas up front.
        """
        if df is None or df.empty:
            return 0
        df = df.copy()
        df[INGESTED_AT] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        key = list(key or [])
        missing = [k for k in key if k not in df.columns]
        if missing:
            raise KeyError(f"{self.vendor}.{dataset}: key columns {missing} not in data")
        if key:
            df = df.drop_duplicates(subset=key, keep="last")

        table = _ident(dataset)
        cols = list(df.columns)
        for c in cols:
            _ident(c)

        with self.connect() as conn:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                coldefs = ", ".join(f"{_ident(c)} {_sqlite_type(df[c])}" for c in cols)
                conn.execute(f"CREATE TABLE {table} ({coldefs})")
                if key:
                    conn.execute(
                        f"CREATE UNIQUE INDEX {_ident(f'ux_{dataset}_key')} ON {table} "
                        f"({', '.join(_ident(k) for k in key)})"
                    )
            else:
                for c in cols:
                    if c not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {_ident(c)} {_sqlite_type(df[c])}")

            col_sql = ", ".join(_ident(c) for c in cols)
            placeholders = ", ".join("?" for _ in cols)
            sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
            if key:
                updates = [c for c in cols if c not in key]
                sql += f" ON CONFLICT ({', '.join(_ident(k) for k in key)}) DO UPDATE SET " + ", ".join(
                    f"{_ident(c)}=excluded.{_ident(c)}" for c in updates
                )
            rows = [tuple(_to_sql_value(v) for v in row) for row in df.itertuples(index=False, name=None)]
            conn.executemany(sql, rows)
        return len(rows)

    def read(
        self,
        dataset: str,
        where: str | None = None,
        params: Iterable = (),
        columns: Sequence[str] | None = None,
        limit: int | None = None,
        **equals,
    ) -> pd.DataFrame:
        """Reads a dataset back as a DataFrame.

        ``equals`` filters are ANDed (``read("markets", status="open")``);
        ``where``/``params`` is an escape hatch for raw SQL predicates.
        """
        if dataset not in self.tables():
            return pd.DataFrame()
        col_sql = ", ".join(_ident(c) for c in columns) if columns else "*"
        clauses, args = [], list(params)
        if where:
            clauses.append(f"({where})")
        for col, val in equals.items():
            clauses.append(f"{_ident(col)} = ?")
            args.append(val)
        sql = f"SELECT {col_sql} FROM {_ident(dataset)}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=args)

    def export(self, dataset: str, fmt: str = "parquet") -> Path:
        """Dumps a dataset to ``exports/<dataset>.<fmt>`` (parquet or csv)."""
        df = self.read(dataset)
        out = self.root / "exports" / f"{dataset}.{fmt}"
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "parquet":
            df.to_parquet(out, index=False)
        elif fmt == "csv":
            df.to_csv(out, index=False)
        else:
            raise ValueError(f"Unsupported export format {fmt!r}")
        return out

    def __repr__(self) -> str:
        return f"VendorStorage({self.vendor!r}, root={str(self.root)!r})"
