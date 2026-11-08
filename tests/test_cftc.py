"""Offline tests for the CFTC vendor: Socrata calls are monkeypatched."""

import pytest

pytestmark = pytest.mark.vendor("cftc")


def _row(date, code, mm_long, mm_short):
    return {
        "id": f"{date}{code}F", "market_and_exchange_names": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
        "report_date_as_yyyy_mm_dd": f"{date}T00:00:00.000", "cftc_contract_market_code": code,
        "cftc_commodity_code": "067 ", "open_interest_all": "1000",
        "m_money_positions_long_all": str(mm_long), "m_money_positions_short_all": str(mm_short),
    }


def test_cot_paginates_converts_and_nets(tmp_path, monkeypatch):
    import vendors.cftc as cftc
    pages = [[_row("2026-10-27", "067651", 150, 100), _row("2026-11-03", "067651", 120, 130)],
             [_row("2026-11-03", "067651", 120, 130)]]
    calls = []

    def fake_get(dataset_id, params):
        calls.append((dataset_id, params))
        return pages[len(calls) - 1]

    monkeypatch.setattr(cftc, "_get", fake_get)
    v = cftc.CftcVendor(data_dir=tmp_path)
    df = v.collect("cot", preset="wti", start="2026-10-01", limit=2)
    assert len(calls) == 2 and calls[1][1]["$offset"] == 2
    assert calls[0][0] == "72hh-3qpy"
    assert "cftc_contract_market_code in ('067651')" in calls[0][1]["$where"]
    assert df["m_money_net"].tolist() == [50, -10, -10]
    assert df["cftc_contract_market_code"].iloc[0] == "067651"  # codes stay text
    assert df["report_date_as_yyyy_mm_dd"].iloc[0] == "2026-10-27"
    assert len(v.load("cot")) == 2  # upserted on (date, code, report)


def test_cot_financial_preset_uses_tff(tmp_path, monkeypatch):
    import vendors.cftc as cftc
    seen = []
    monkeypatch.setattr(cftc, "_get", lambda ds, p: seen.append(ds) or [{
        "id": "x", "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000", "cftc_contract_market_code": "13874A",
        "lev_money_positions_long": "10", "lev_money_positions_short": "25",
    }])
    df = cftc.CftcVendor(data_dir=tmp_path).fetch("cot", preset="sp500_emini")
    assert seen == ["gpe5-46if"] and df["lev_money_net"].iloc[0] == -15 and df["report"].iloc[0] == "tff"


def test_markets_latest_and_app_token(tmp_path, monkeypatch):
    import vendors.cftc as cftc
    headers = []

    class Resp:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    def fake_get(url, params=None, headers=None, timeout=None, sent=headers):
        sent.append(headers)
        if "$select" in params and params["$select"].startswith("max("):
            return Resp([{"latest": "2026-11-03T00:00:00.000"}])
        assert params["$where"] == "report_date_as_yyyy_mm_dd = '2026-11-03T00:00:00.000'"
        return Resp([{"cftc_contract_market_code": "067651", "market_and_exchange_names": "WTI"},
                     {"cftc_contract_market_code": "067651", "market_and_exchange_names": "WTI"}])

    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok")
    monkeypatch.setattr(cftc.requests, "get", fake_get)
    df = cftc.CftcVendor(data_dir=tmp_path).collect("markets")
    assert len(df) == 1 and df["report_date_as_yyyy_mm_dd"].iloc[0] == "2026-11-03"
    assert headers == [{"X-App-Token": "tok"}] * 2
