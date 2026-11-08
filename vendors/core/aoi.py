"""Preset areas of interest shared by the satellite vendors.

bbox is (min_lon, min_lat, max_lon, max_lat) in WGS84. Coordinates are
approximate site extents; pass your own bbox for anything precise.
"""

AOIS: dict[str, dict] = {
    "cushing": {"bbox": (-96.80, 35.93, -96.70, 36.00), "desc": "Cushing, OK crude storage hub"},
    "houston_ship_channel": {"bbox": (-95.30, 29.68, -94.95, 29.78), "desc": "Houston Ship Channel tank farms"},
    "corpus_christi": {"bbox": (-97.55, 27.78, -97.35, 27.85), "desc": "Corpus Christi export terminals"},
    "louisiana_loop": {"bbox": (-90.25, 29.40, -90.15, 29.50), "desc": "LOOP Clovelly storage dome"},
    "ras_tanura": {"bbox": (50.10, 26.60, 50.20, 26.72), "desc": "Ras Tanura terminal, Saudi Arabia"},
    "fujairah": {"bbox": (56.33, 25.15, 56.40, 25.23), "desc": "Fujairah storage hub, UAE"},
    "rotterdam": {"bbox": (4.00, 51.88, 4.30, 51.98), "desc": "Rotterdam Europoort/Maasvlakte"},
    "singapore_jurong": {"bbox": (103.65, 1.22, 103.75, 1.30), "desc": "Jurong Island, Singapore"},
    "ningbo_zhoushan": {"bbox": (121.85, 29.85, 122.15, 30.05), "desc": "Ningbo-Zhoushan crude storage"},
    "qingdao": {"bbox": (120.15, 35.95, 120.35, 36.10), "desc": "Qingdao port tank farms"},
    "saldanha_bay": {"bbox": (17.90, -33.05, 18.05, -32.95), "desc": "Saldanha Bay storage, South Africa"},
}


def resolve_bbox(aoi: str | None = None, bbox: str | tuple | None = None) -> tuple[float, float, float, float]:
    """Return a bbox from a preset name or a 'min_lon,min_lat,max_lon,max_lat' string/tuple."""
    if bbox is not None:
        vals = [float(v) for v in (bbox.split(",") if isinstance(bbox, str) else bbox)]
        if len(vals) != 4:
            raise ValueError("bbox needs 4 numbers: min_lon,min_lat,max_lon,max_lat")
        return tuple(vals)  # type: ignore[return-value]
    if aoi is None:
        raise ValueError(f"pass aoi=<one of {sorted(AOIS)}> or bbox=...")
    try:
        return tuple(AOIS[aoi]["bbox"])  # type: ignore[return-value]
    except KeyError:
        raise ValueError(f"unknown aoi {aoi!r}; presets: {sorted(AOIS)}") from None
