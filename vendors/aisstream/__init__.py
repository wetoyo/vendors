"""AIS vessel traffic from the aisstream.io websocket.

Needs a free API key from https://aisstream.io in ``AISSTREAM_API_KEY``.
Built for watching tanker traffic at oil terminals: pass one of the shared
presets in ``vendors.core.aoi`` (padded by ``AOI_PAD`` degrees so ships at
anchor nearby are included) or your own bbox.

aisstream is stream-only (no history), so the datasets record the feed for a
fixed number of seconds and return what arrived.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from datetime import datetime

import pandas as pd

from vendors.core import Vendor, dataset
from vendors.core.aoi import resolve_bbox

URL = "wss://stream.aisstream.io/v0/stream"
API_KEY_ENV = "AISSTREAM_API_KEY"
AOI_PAD = 0.2  # degrees added on every side of a preset AOI


class MissingApiKey(RuntimeError):
    """Raised when AISSTREAM_API_KEY is not set."""


class AisstreamError(RuntimeError):
    """The server sent an error message (bad key, bad subscription, ...)."""


def api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise MissingApiKey(f"Set {API_KEY_ENV} to your aisstream.io API key (free at https://aisstream.io).")
    return key


def area(aoi: str | None = None, bbox: str | tuple | None = None) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat); preset AOIs are padded by AOI_PAD, explicit bboxes are not."""
    b = resolve_bbox(aoi=aoi, bbox=bbox)
    if bbox is None:
        b = (b[0] - AOI_PAD, b[1] - AOI_PAD, b[2] + AOI_PAD, b[3] + AOI_PAD)
    return b


def subscription(
    key: str,
    bbox: tuple[float, float, float, float],
    mmsi: Sequence[int | str] | int | str | None = None,
    message_types: Sequence[str] | None = None,
) -> dict:
    """Subscribe message. aisstream boxes are [[lat, lon], [lat, lon]] (lat first, unlike our bboxes)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    msg: dict = {"APIKey": key, "BoundingBoxes": [[[min_lat, min_lon], [max_lat, max_lon]]]}
    if mmsi is not None:
        ids = mmsi.split(",") if isinstance(mmsi, str) else ([mmsi] if isinstance(mmsi, int) else mmsi)
        msg["FiltersShipMMSI"] = [str(m).strip() for m in ids if str(m).strip()]
    if message_types:
        msg["FilterMessageTypes"] = [message_types] if isinstance(message_types, str) else list(message_types)
    return msg


def _time_utc(raw: str | None) -> str | None:
    """'2024-05-01 12:00:00.123456789 +0000 UTC' -> '2024-05-01T12:00:00.123456+00:00'."""
    if not raw:
        return None
    s = raw.replace(" UTC", "").strip()
    date_time, _, tz = s.rpartition(" ")
    if "." in date_time:
        whole, frac = date_time.split(".", 1)
        date_time = f"{whole}.{frac[:6]}"
    else:
        date_time += ".0"
    try:
        return datetime.strptime(f"{date_time} {tz}", "%Y-%m-%d %H:%M:%S.%f %z").isoformat()
    except ValueError:
        return raw


def _clean(value, missing):
    """AIS uses sentinel values for 'not available' (heading 511, sog 102.3, cog 360)."""
    return None if value is None or value == missing else value


def _text(value) -> str | None:
    s = (value or "").strip().strip("@").strip()
    return s or None


def normalize(raw: dict) -> dict:
    """Flattens one aisstream message into a row."""
    mtype = raw.get("MessageType")
    meta = raw.get("MetaData", {}) or {}
    body = (raw.get("Message", {}) or {}).get(mtype, {}) or {}
    row = {
        "mmsi": meta.get("MMSI") or body.get("UserID"),
        "ship_name": _text(meta.get("ShipName")),
        "lat": body.get("Latitude", meta.get("latitude")),
        "lon": body.get("Longitude", meta.get("longitude")),
        "sog": _clean(body.get("Sog"), 102.3),
        "cog": _clean(body.get("Cog"), 360),
        "heading": _clean(body.get("TrueHeading"), 511),
        "nav_status": body.get("NavigationalStatus"),
        "message_type": mtype,
        "time_utc": _time_utc(meta.get("time_utc")),
    }
    if mtype == "ShipStaticData":
        dim = body.get("Dimension") or {}
        row.update(
            ship_name=_text(body.get("Name")) or row["ship_name"],
            ship_type=body.get("Type"),
            destination=_text(body.get("Destination")),
            draught=body.get("MaximumStaticDraught"),
            call_sign=_text(body.get("CallSign")),
            imo=body.get("ImoNumber") or None,
            length=(dim.get("A") or 0) + (dim.get("B") or 0) or None,
            beam=(dim.get("C") or 0) + (dim.get("D") or 0) or None,
        )
    return row


def _aoi_label(aoi: str | None, bbox) -> str:
    if bbox is None:
        return aoi or ""
    return ",".join(str(v) for v in (bbox.split(",") if isinstance(bbox, str) else bbox))


class AisstreamVendor(Vendor):
    name = "aisstream"
    description = "aisstream.io live AIS vessel positions and static data (tankers at oil terminals)"

    def _connect(self):
        import websockets  # optional extra: pip install -e ".[aisstream]"

        return websockets.connect(URL, max_size=None)

    async def stream(
        self,
        aoi: str | None = None,
        bbox: str | tuple | None = None,
        mmsi: Sequence[int | str] | int | str | None = None,
        message_types: Sequence[str] | None = ("PositionReport", "ShipStaticData"),
    ) -> AsyncIterator[dict]:
        """Live normalized AIS messages inside an AOI preset (padded) or bbox."""
        sub = subscription(api_key(), area(aoi, bbox), mmsi, message_types)
        async with self._connect() as ws:
            await ws.send(json.dumps(sub))  # aisstream drops the socket if this isn't sent within 3 s
            async for frame in ws:
                raw = json.loads(frame)
                if "error" in raw:
                    raise AisstreamError(raw["error"])
                yield normalize(raw)

    def _record(self, seconds: float, max_messages: int | None, **stream_kwargs) -> list[dict]:
        rows: list[dict] = []

        async def run():
            async with aclosing(self.stream(**stream_kwargs)) as feed:
                async for row in feed:
                    rows.append(row)
                    if max_messages and len(rows) >= max_messages:
                        return

        async def bounded():
            try:
                await asyncio.wait_for(run(), timeout=seconds)
            except asyncio.TimeoutError:
                pass

        api_key()  # fail fast, before starting an event loop
        asyncio.run(bounded())
        return rows

    @dataset()
    def positions(
        self, aoi: str | None = None, bbox: str | None = None, seconds: float = 60, max_messages: int = 5000
    ) -> pd.DataFrame:
        """Position reports recorded for `seconds` (append snapshot)."""
        rows = self._record(seconds, max_messages, aoi=aoi, bbox=bbox, message_types=("PositionReport",))
        df = pd.DataFrame(rows)
        if not df.empty:
            df.insert(0, "aoi", _aoi_label(aoi, bbox))
        return df

    @dataset(key=["mmsi"])
    def vessels(self, aoi: str | None = None, bbox: str | None = None, seconds: float = 120) -> pd.DataFrame:
        """Ship static data (type, destination, draught), latest per MMSI, with an is_tanker flag."""
        rows = self._record(seconds, None, aoi=aoi, bbox=bbox, message_types=("ShipStaticData",))
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df = df.drop_duplicates(subset=["mmsi"], keep="last")
        df.insert(0, "aoi", _aoi_label(aoi, bbox))
        df["is_tanker"] = pd.to_numeric(df["ship_type"], errors="coerce").between(80, 89)  # AIS tanker types
        return df.reset_index(drop=True)
