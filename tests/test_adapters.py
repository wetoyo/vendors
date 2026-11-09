"""Offline tests: upstream API calls are monkeypatched; parsing/normalization is real."""

import pandas as pd
import pytest

from vendors import get_vendor
from vendors.core.submodules import import_external


@pytest.mark.vendor("deribit")
def test_deribit_order_books_use_derbit_cleaner(tmp_path, monkeypatch):
    d = import_external("derbit")
    monkeypatch.setattr(d, "get_order_book", lambda name: {
        "instrument_name": name, "best_bid_price": 0.1, "greeks": {"delta": 0.5},
        "stats": {"volume": 3.0}, "timestamp": 1,
    })
    v = get_vendor("deribit", data_dir=tmp_path)
    df = v.collect("order_books", instruments="BTC-X-C,BTC-Y-P")
    assert df["delta"].tolist() == [0.5, 0.5]
    assert len(v.load("order_books")) == 2


@pytest.mark.vendor("kalshi")
def test_kalshi_markets_and_orderbooks(tmp_path, monkeypatch):
    f = import_external("Kalshi.fetch_historical")
    monkeypatch.setattr(f, "fetch_markets", lambda **kw: [
        {"ticker": "KXFED-1", "title": "Fed rate?", "yes_sub_title": "Above 4%", "status": "active"},
    ])
    monkeypatch.setattr(f, "fetch_orderbook", lambda t, depth=None: {"yes_dollars": [["0.40", "10"]], "no_dollars": []})
    v = get_vendor("kalshi", data_dir=tmp_path)
    assert v.collect("markets")["raw_title"].iloc[0] == "Fed rate? Above 4%"
    v.collect("orderbooks", tickers="KXFED-1")
    v.collect("orderbooks", tickers=["KXFED-1"])
    assert len(v.load("orderbooks")) == 2  # append-only snapshots


SAMPLE_13D = b"""<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13D" xmlns:com="http://www.sec.gov/edgar/common">
  <formData><coverPageHeader><issuerInfo><issuerCIK>0000000042</issuerCIK><issuerName>Acme Corp</issuerName></issuerInfo></coverPageHeader>
  <reportingPersons><reportingPersonInfo><reportingPersonName>Big Fund LP</reportingPersonName>
  <aggregateAmountOwned>500</aggregateAmountOwned></reportingPersonInfo></reportingPersons></formData>
</edgarSubmission>"""


@pytest.mark.vendor("sec")
def test_sec_filings_download_parse_enrich(tmp_path, monkeypatch):
    from vendors.sec import edgar
    monkeypatch.setattr(edgar, "search_filings", lambda *a, **k: [
        {"_id": "0001-26-000001:primary_doc.xml",
         "_source": {"ciks": ["0000000042", "0000000099"], "form": "SCHEDULE 13D", "file_date": "2026-11-02"}},
        {"_id": "0001-26-000002:ex99.htm", "_source": {"ciks": ["1"], "form": "SCHEDULE 13D"}},
    ])
    monkeypatch.setattr(edgar, "download", lambda url: SAMPLE_13D)
    monkeypatch.setattr(edgar, "get_outstanding_shares", lambda cik: (10_000, "2026-09-30"))
    v = get_vendor("sec", data_dir=tmp_path / "sec")
    df = v.collect("filings")
    assert len(df) == 1  # xml_only skips the .htm exhibit
    row = df.iloc[0]
    assert (row.issuer, row.issuer_cik, row.share_pct) == ("Acme Corp", "0000000042", 0.05)
    assert (tmp_path / "sec" / "files" / "filings" / "0001-26-000001_primary_doc.xml").exists()


@pytest.mark.vendor("yahoo")
def test_yahoo_prices(tmp_path, monkeypatch):
    import yfinance as yf
    idx = pd.date_range("2026-01-01", periods=3, name="Date")
    monkeypatch.setattr(yf, "download", lambda **kw: pd.DataFrame(
        {"Open": [1.0, 2, 3], "High": 1.0, "Low": 1.0, "Close": [1.0, None, 3], "Volume": 10}, index=idx))
    v = get_vendor("yahoo", data_dir=tmp_path)
    df = v.collect("prices", tickers="aapl")
    assert list(df.columns[:3]) == ["ticker", "date", "open"]
    assert df["close"].tolist() == [1.0, 2.0, 3.0]  # fill_missing interpolates
    v.collect("prices", tickers="AAPL")
    assert len(v.load("prices")) == 3


@pytest.mark.vendor("sec")
def test_sec_requires_user_agent(monkeypatch):
    import pytest
    from vendors.sec import edgar
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(edgar.MissingUserAgent):
        edgar.user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.com")
    assert edgar.user_agent() == "Jane Doe jane@example.com"


@pytest.mark.vendor("polymarket")
def test_polymarket_markets_orderbooks_history(tmp_path, monkeypatch):
    f = import_external("Polymarket.fetch_historical")
    monkeypatch.setattr(f, "fetch_markets", lambda **kw: [{
        "id": "1", "question": " Will X happen? ", "active": True, "closed": False,
        "clobTokenIds": '["111", "222"]', "bestBid": "0.41", "events": [{"big": "nested"}],
    }])
    monkeypatch.setattr(f, "fetch_order_book", lambda tok: {"market": "0xabc", "bids": [{"price": "0.4", "size": "5"}], "asks": []})

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"history": [{"t": 1700000000, "p": 0.4}, {"t": 1700003600, "p": 0.45}]}
    import vendors.polymarket as pm
    monkeypatch.setattr(pm.requests, "get", lambda *a, **k: Resp())

    v = get_vendor("polymarket", data_dir=tmp_path)
    m = v.collect("markets")
    assert (m.raw_title.iloc[0], m.status.iloc[0], m.best_bid.iloc[0]) == ("Will X happen?", "active", 0.41)
    assert "events" not in m.columns
    ob = v.collect("orderbooks", token_ids="111,222")
    assert ob.token_id.tolist() == ["111", "222"] and ob.market.iloc[0] == "0xabc"
    ph = v.collect("price_history", token_ids=111)  # CLI passes bare numeric ids as int
    v.collect("price_history", token_ids=["111"])
    assert ph.token_id.unique().tolist() == ["111"] and len(v.load("price_history")) == 2


FF_SAMPLE = (
    "This file was created using the 202609 CRSP database.\r\r\n\r\r\n"
    ",Mkt-RF,SMB,HML,RF\r\r\n192607,   2.89,  -2.42,  -2.75,   0.22\r\r\n"
    "192608,   2.64,  -1.44,   4.13,   0.25\r\r\n\r\r\n"
    " Annual Factors: January-December \r\r\n,Mkt-RF,SMB,HML,RF\r\r\n  1927,  29.47,  -2.04,  -3.54,   3.12\r\r\n"
)


@pytest.mark.vendor("fama_french")
def test_fama_french_monthly_factors(tmp_path, monkeypatch):
    import vendors.fama_french as ff
    monkeypatch.setattr(ff, "download", lambda file: FF_SAMPLE)

    v = get_vendor("fama_french", data_dir=tmp_path)
    df = v.collect("factors")
    assert df.date.tolist() == ["1926-07-31", "1926-08-31"]  # annual block skipped
    assert df.mkt_rf.iloc[0] == pytest.approx(0.0289) and df.mkt.iloc[0] == pytest.approx(0.0311)
    assert len(v.load("factors", frequency="monthly")) == 2
