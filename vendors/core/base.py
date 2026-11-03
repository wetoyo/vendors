"""The vendor contract.

A vendor is a subclass of :class:`Vendor` that declares its datasets with the
:func:`dataset` decorator. Each dataset method takes keyword parameters, calls
the upstream API, and returns a ``pandas.DataFrame`` with one row per record.
:meth:`Vendor.fetch` then enforces the shared conventions (snake_case column
names, clean index). The base class does everything else:

    v = get_vendor("deribit")
    v.datasets()                         # what can I pull?
    df = v.fetch("instruments", currency="BTC")   # pull, don't store
    df = v.collect("instruments", currency="BTC") # pull + store in data/deribit/
    df = v.load("instruments", kind="option")     # read back what was stored
    async for msg in v.stream(...): ...           # live data, where supported
"""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from .storage import VendorStorage


def snake_case(name: str) -> str:
    """``"Adj Close"`` -> ``adj_close``, ``contractSymbol`` -> ``contract_symbol``."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()
    if not s:
        return "col"
    return f"c_{s}" if s[0].isdigit() else s


@dataclass(frozen=True)
class Dataset:
    """Metadata attached to a dataset method by the :func:`dataset` decorator."""

    name: str
    key: tuple[str, ...] | None
    description: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        return "upsert" if self.key else "append"


def dataset(
    name: str | None = None,
    *,
    key: Sequence[str] | None = None,
    description: str | None = None,
) -> Callable:
    """Marks a Vendor method as a dataset.

    Args:
        name: Dataset name (= SQLite table name). Defaults to the method name.
        key: Columns that uniquely identify a row. Stored rows are upserted on
            this key. Leave it ``None`` for append-only snapshot datasets.
        description: One-liner shown by ``python -m vendors datasets``.
            Defaults to the first line of the docstring.
    """

    def wrap(fn: Callable) -> Callable:
        fn.__vendor_dataset__ = {
            "name": name or fn.__name__,
            "key": tuple(key) if key else None,
            "description": description or (inspect.getdoc(fn) or "").split("\n")[0],
        }
        return fn

    return wrap


class Vendor:
    """Base class for every data vendor.

    Subclasses set ``name`` (also the storage folder name) and define one or
    more ``@dataset`` methods returning DataFrames. Optionally they override
    :meth:`stream` for live data.
    """

    name: ClassVar[str]
    description: ClassVar[str] = ""

    def __init__(self, data_dir: str | Path | None = None):
        self.storage = VendorStorage(self.name, data_dir)

    # --------------------------------------------------------------- discovery
    @classmethod
    def datasets(cls) -> dict[str, Dataset]:
        out: dict[str, Dataset] = {}
        for attr in dir(cls):
            fn = getattr(cls, attr, None)
            meta = getattr(fn, "__vendor_dataset__", None)
            if not meta:
                continue
            sig = inspect.signature(fn)
            params = {
                p.name: (None if p.default is inspect.Parameter.empty else p.default)
                for p in list(sig.parameters.values())[1:]
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            }
            out[meta["name"]] = Dataset(method=attr, params=params, **meta)
        return dict(sorted(out.items()))

    def _dataset(self, name: str) -> Dataset:
        ds = self.datasets()
        if name not in ds:
            raise KeyError(f"{self.name} has no dataset {name!r}; available: {sorted(ds)}")
        return ds[name]

    # ------------------------------------------------------------------- core
    def fetch(self, dataset: str, **params) -> pd.DataFrame:
        """Pulls ``dataset`` from the vendor and returns it without storing."""
        ds = self._dataset(dataset)
        df = getattr(self, ds.method)(**params)
        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(df)
        df = df.rename(columns=snake_case).rename_axis(columns=None)
        return df.loc[:, ~df.columns.duplicated()].reset_index(drop=True)

    def collect(self, dataset: str, **params) -> pd.DataFrame:
        """Pulls ``dataset`` and writes it to this vendor's storage folder."""
        ds = self._dataset(dataset)
        df = self.fetch(dataset, **params)
        self.storage.write(ds.name, df, key=ds.key)
        return df

    def load(self, dataset: str, **filters) -> pd.DataFrame:
        """Reads a previously collected dataset (see VendorStorage.read for filters)."""
        return self.storage.read(self._dataset(dataset).name, **filters)

    async def stream(self, *args, **kwargs) -> AsyncIterator[dict]:
        """Live data feed. Vendors without one raise NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not support streaming")
        yield  # pragma: no cover  (makes this an async generator)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} data={self.storage.root}>"
