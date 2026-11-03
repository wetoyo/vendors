import pandas as pd
import pytest

from vendors.core.storage import INGESTED_AT, VendorStorage


@pytest.fixture
def store(tmp_path):
    return VendorStorage("acme", root=tmp_path / "acme")


def test_upsert_on_key(store):
    store.write("px", pd.DataFrame({"sym": ["A", "B"], "v": [1.0, 2.0]}), key=["sym"])
    store.write("px", pd.DataFrame({"sym": ["B", "C"], "v": [20.0, 3.0]}), key=["sym"])
    df = store.read("px").set_index("sym")
    assert df["v"].to_dict() == {"A": 1.0, "B": 20.0, "C": 3.0}
    assert INGESTED_AT in df.columns


def test_append_without_key(store):
    for _ in range(2):
        store.write("snaps", pd.DataFrame({"sym": ["A"], "v": [1]}))
    assert len(store.read("snaps")) == 2


def test_schema_evolves_and_nested_values_become_json(store):
    store.write("t", pd.DataFrame({"k": [1]}), key=["k"])
    store.write("t", pd.DataFrame({"k": [2], "levels": [[[0.5, 10]]], "meta": [{"a": 1}]}), key=["k"])
    df = store.read("t").set_index("k")
    assert df.loc[2, "levels"] == "[[0.5, 10]]"
    assert df.loc[2, "meta"] == '{"a": 1}'
    assert pd.isna(df.loc[1, "levels"])


def test_read_filters_and_missing_table(store):
    store.write("t", pd.DataFrame({"k": [1, 2, 3], "s": ["x", "y", "x"]}), key=["k"])
    assert store.read("t", s="x")["k"].tolist() == [1, 3]
    assert store.read("t", where="k > ?", params=[1])["k"].tolist() == [2, 3]
    assert store.read("nope").empty


def test_files_live_under_vendor_folder(store, tmp_path):
    p = store.write_file("filings/a.xml", b"<x/>")
    assert p == tmp_path / "acme" / "files" / "filings" / "a.xml"
    assert p.read_bytes() == b"<x/>"


def test_rejects_bad_identifiers(store):
    with pytest.raises(ValueError):
        store.write("bad name", pd.DataFrame({"k": [1]}))
    with pytest.raises(KeyError):
        store.write("t", pd.DataFrame({"k": [1]}), key=["missing"])


def test_export(store):
    store.write("t", pd.DataFrame({"k": [1, 2]}), key=["k"])
    out = store.export("t", fmt="csv")
    assert out.parent.name == "exports" and len(pd.read_csv(out)) == 2
