import pandas as pd
import pytest

from vendors.core import REGISTRY, Vendor, dataset, vendor_class
from vendors.core.base import snake_case


class Acme(Vendor):
    name = "acme"

    @dataset(key=["sym"])
    def quotes(self, syms: str = "A,B", px: float = 1.0):
        """Quotes for some symbols."""
        return [{"Sym": s, "Last Price": px, "openInterest": 3} for s in syms.split(",")]

    @dataset("ticks")
    def _ticks(self):
        return pd.DataFrame({"sym": ["A"]})


def test_dataset_discovery():
    ds = Acme.datasets()
    assert set(ds) == {"quotes", "ticks"}
    assert ds["quotes"].key == ("sym",) and ds["quotes"].mode == "upsert"
    assert ds["quotes"].params == {"syms": "A,B", "px": 1.0}
    assert ds["quotes"].description == "Quotes for some symbols."
    assert ds["ticks"].mode == "append"


def test_fetch_normalizes_columns(tmp_path):
    df = Acme(data_dir=tmp_path).fetch("quotes")
    assert list(df.columns) == ["sym", "last_price", "open_interest"]


def test_collect_and_load_use_vendor_folder(tmp_path):
    v = Acme(data_dir=tmp_path / "acme")
    v.collect("quotes", px=2.0)
    v.collect("quotes", syms="B,C", px=5.0)
    out = v.load("quotes").set_index("sym")["last_price"].to_dict()
    assert out == {"A": 2.0, "B": 5.0, "C": 5.0}
    assert (tmp_path / "acme" / "acme.db").exists()


def test_unknown_dataset(tmp_path):
    with pytest.raises(KeyError):
        Acme(data_dir=tmp_path).fetch("nope")


@pytest.mark.parametrize("raw,expected", [
    ("Adj Close", "adj_close"), ("contractSymbol", "contract_symbol"),
    ("DATE", "date"), ("share %", "share"), ("1d", "c_1d"),
])
def test_snake_case(raw, expected):
    assert snake_case(raw) == expected


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_registered_vendors_are_well_formed(name):
    from conftest import vendor_missing

    if reason := vendor_missing(name):
        pytest.skip(reason)
    cls = vendor_class(name)
    assert cls.name == name
    assert cls.datasets(), f"{name} declares no datasets"
