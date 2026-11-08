"""Offline aisstream tests: the websocket is faked, normalization is real."""

import asyncio
import json
from contextlib import aclosing

import pytest

from vendors.aisstream import AOI_PAD, AisstreamError, AisstreamVendor, MissingApiKey, normalize, subscription

POSITION = {
    "MessageType": "PositionReport",
    "MetaData": {
        "MMSI": 538001234, "ShipName": "FRONT ALTAIR   ", "latitude": 25.2, "longitude": 56.36,
        "time_utc": "2026-11-08 12:00:00.123456789 +0000 UTC",
    },
    "Message": {"PositionReport": {
        "UserID": 538001234, "Latitude": 25.201, "Longitude": 56.361, "Sog": 0.1, "Cog": 360,
        "TrueHeading": 511, "NavigationalStatus": 1,
    }},
}


def static(mmsi, ship_type, dest):
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": mmsi, "ShipName": "X", "latitude": 25.2, "longitude": 56.36,
                     "time_utc": "2026-11-08 12:00:01 +0000 UTC"},
        "Message": {"ShipStaticData": {
            "UserID": mmsi, "Name": "NAME@@@@", "Type": ship_type, "Destination": dest + "  ",
            "MaximumStaticDraught": 14.5, "CallSign": "ABC1", "ImoNumber": 9000001,
            "Dimension": {"A": 200, "B": 50, "C": 20, "D": 24},
        }},
    }


class FakeWS:
    def __init__(self, frames):
        self.frames = [json.dumps(f).encode() for f in frames]
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            await asyncio.sleep(3600)  # live feed: idle until the caller's timeout
        return self.frames.pop(0)


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    monkeypatch.setenv("AISSTREAM_API_KEY", "test-key")
    return AisstreamVendor(data_dir=tmp_path)


def fake(monkeypatch, vendor, frames):
    ws = FakeWS(frames)
    monkeypatch.setattr(vendor, "_connect", lambda: ws)
    return ws


@pytest.mark.vendor("aisstream")
def test_normalize_position_and_static():
    row = normalize(POSITION)
    assert row["mmsi"] == 538001234 and row["ship_name"] == "FRONT ALTAIR"
    assert (row["lat"], row["lon"], row["sog"]) == (25.201, 56.361, 0.1)
    assert row["cog"] is None and row["heading"] is None  # AIS "not available" sentinels
    assert row["nav_status"] == 1 and row["message_type"] == "PositionReport"
    assert row["time_utc"] == "2026-11-08T12:00:00.123456+00:00"

    s = normalize(static(1, 84, "FUJAIRAH"))
    assert s["ship_type"] == 84 and s["destination"] == "FUJAIRAH" and s["draught"] == 14.5
    assert s["ship_name"] == "NAME" and s["length"] == 250 and s["beam"] == 44
    assert s["time_utc"] == "2026-11-08T12:00:01+00:00"


@pytest.mark.vendor("aisstream")
def test_subscription_is_lat_lon_and_aoi_is_padded(vendor, monkeypatch):
    sub = subscription("k", (56.33, 25.15, 56.40, 25.23), mmsi="1, 2", message_types=["PositionReport"])
    assert sub["BoundingBoxes"] == [[[25.15, 56.33], [25.23, 56.40]]]
    assert sub["FiltersShipMMSI"] == ["1", "2"] and sub["FilterMessageTypes"] == ["PositionReport"]

    ws = fake(monkeypatch, vendor, [POSITION])

    async def first():
        async with aclosing(vendor.stream(aoi="fujairah")) as feed:
            async for row in feed:
                return row

    assert asyncio.run(first())["mmsi"] == 538001234
    (sent,) = ws.sent
    assert sent["APIKey"] == "test-key"
    (lo, hi), = sent["BoundingBoxes"]
    assert lo == pytest.approx([25.15 - AOI_PAD, 56.33 - AOI_PAD])
    assert hi == pytest.approx([25.23 + AOI_PAD, 56.40 + AOI_PAD])
    assert sent["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]
    assert "FiltersShipMMSI" not in sent


@pytest.mark.vendor("aisstream")
def test_positions_snapshot_appends(vendor, monkeypatch):
    fake(monkeypatch, vendor, [POSITION, POSITION])
    df = vendor.collect("positions", aoi="fujairah", seconds=0.2)
    assert len(df) == 2 and set(df["aoi"]) == {"fujairah"}
    assert {"mmsi", "lat", "lon", "sog", "time_utc"} <= set(df.columns)

    fake(monkeypatch, vendor, [POSITION] * 5)
    assert len(vendor.collect("positions", bbox="56,25,57,26", seconds=5, max_messages=3)) == 3
    assert len(vendor.load("positions")) == 5  # no key -> append


@pytest.mark.vendor("aisstream")
def test_vessels_latest_per_mmsi_with_tanker_flag(vendor, monkeypatch):
    ws = fake(monkeypatch, vendor, [static(1, 84, "OLD"), static(2, 70, "ROTTERDAM"), static(1, 84, "NEW")])
    df = vendor.collect("vessels", aoi="rotterdam", seconds=0.2)
    assert ws.sent[0]["FilterMessageTypes"] == ["ShipStaticData"]
    by = df.set_index("mmsi")
    assert len(df) == 2 and by.loc[1, "destination"] == "NEW"
    assert bool(by.loc[1, "is_tanker"]) and not bool(by.loc[2, "is_tanker"])


@pytest.mark.vendor("aisstream")
def test_missing_key_and_server_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AISSTREAM_API_KEY", raising=False)
    v = AisstreamVendor(data_dir=tmp_path)
    with pytest.raises(MissingApiKey, match="AISSTREAM_API_KEY"):
        v.fetch("positions", aoi="cushing", seconds=0.1)

    monkeypatch.setenv("AISSTREAM_API_KEY", "bad")
    fake(monkeypatch, v, [{"error": "Api Key Is Not Valid"}])
    with pytest.raises(AisstreamError, match="Not Valid"):
        v.fetch("positions", aoi="cushing", seconds=1)
