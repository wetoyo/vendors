"""Floating-roof shadow model.

A floating roof sits ``depth`` metres below the tank rim. With the sun at
elevation ``e`` the rim on the sun side shades the roof for a horizontal
distance ``s = depth / tan(e)``: a roof point ``p`` is shaded when ``p + s*u_sun``
falls outside the tank circle. For each candidate fill level we predict the
shaded fraction of every pixel, fit a lit roof brightness per band, and keep the
fill that best explains the pixels:

    pixel_b = roof_b * (1 - (1 - k) * shaded_fraction)

``k`` is the shadow/lit brightness ratio (skylight keeps shadows from going
black), fitted once per scene across all tanks.

The dark wall and rim seal blur into the edge pixels and look like a shadow of
about ``APPARENT_SHADOW_M`` at any sun angle, so the modelled shadow is
``s0 + depth / tan(e)``. Without it, high-sun scenes read far too empty. ``s0``
was chosen as the value that makes Cushing's site fill independent of sun
elevation over 2019-2026 (it was not fitted to EIA stocks).
"""

from __future__ import annotations

import math

import numpy as np

FILLS = np.linspace(0.0, 1.0, 41)
SHADOW_RATIOS = np.linspace(0.03, 0.75, 25)
SUBSAMPLES = 8
APPARENT_SHADOW_M = 1.75


def sun_vector(azimuth_deg: float) -> np.ndarray:
    """Horizontal unit vector (east, north) pointing toward the sun."""
    a = math.radians(azimuth_deg)
    return np.array([math.sin(a), math.cos(a)])


def interior_pixels(cx: float, cy: float, radius: float, x0: float, y0: float, res: float,
                    shape: tuple[int, int], wall_m: float = 1.0, min_inside: float = 0.97):
    """Rows/cols of pixels lying (almost) wholly inside the tank, plus their sub-sample coords.

    ``x0, y0`` is the top-left corner of the raster window; rows grow southward.
    """
    r_in = radius - wall_m
    c_lo = max(int((cx - radius - x0) // res), 0)
    c_hi = min(int((cx + radius - x0) // res) + 1, shape[1])
    r_lo = max(int((y0 - (cy + radius)) // res), 0)
    r_hi = min(int((y0 - (cy - radius)) // res) + 1, shape[0])
    if c_lo >= c_hi or r_lo >= r_hi:
        return np.empty(0, int), np.empty(0, int), np.empty((0, SUBSAMPLES**2, 2))
    offs = (np.arange(SUBSAMPLES) + 0.5) / SUBSAMPLES * res
    sx, sy = np.meshgrid(offs, -offs)
    sub = np.stack([sx.ravel(), sy.ravel()], axis=1)  # (S, 2), relative to pixel top-left
    rows, cols = np.mgrid[r_lo:r_hi, c_lo:c_hi]
    rows, cols = rows.ravel(), cols.ravel()
    tl = np.stack([x0 + cols * res, y0 - rows * res], axis=1)  # (P, 2)
    pts = tl[:, None, :] + sub[None, :, :] - np.array([cx, cy])  # (P, S, 2) relative to center
    inside = (np.hypot(pts[..., 0], pts[..., 1]) < r_in).mean(axis=1)
    keep = inside >= min_inside
    return rows[keep], cols[keep], pts[keep]


def shaded_fractions(pts: np.ndarray, radius: float, depths: np.ndarray, sun_elev_deg: float,
                     sun_az_deg: float, apparent_m: float = 0.0) -> np.ndarray:
    """(len(depths), P) fraction of each pixel's roof area in the rim's shadow."""
    u = sun_vector(sun_az_deg)
    shifts = apparent_m + depths / math.tan(math.radians(sun_elev_deg))  # (D,)
    moved = pts[None, :, :, :] + shifts[:, None, None, None] * u  # (D, P, S, 2)
    return (np.hypot(moved[..., 0], moved[..., 1]) > radius).mean(axis=2)


def fit_tank(values: np.ndarray, shaded: np.ndarray, k: float) -> tuple[int, np.ndarray]:
    """Best fill index and the SSE for every fill, for pixel ``values`` (P, B)."""
    g = 1.0 - (1.0 - k) * shaded  # (F, P)
    vg = g @ values  # (F, B)
    gg = (g * g).sum(axis=1)  # (F,)
    sse = (values**2).sum() - ((vg**2).sum(axis=1) / gg)
    # Ties (no visible shadow) resolve to the fullest level, not the emptiest.
    best = len(sse) - 1 - int(np.argmin(sse[::-1] + 1e-12 * np.arange(len(sse))))
    return best, sse


def estimate_scene(tanks: list[dict], sun_elev: float, sun_az: float, height_m: float,
                   apparent_m: float = APPARENT_SHADOW_M) -> list[dict]:
    """Fits every tank of one scene. Each tank dict has ``values`` (P, B), ``pts`` and ``radius``."""
    depths = (1.0 - FILLS) * height_m
    prepared = []
    for t in tanks:
        sh = shaded_fractions(t["pts"], t["radius"], depths, sun_elev, sun_az, apparent_m)
        prepared.append((t, sh))
    if not prepared:
        return []
    # One shadow/lit ratio per scene: the one that best explains all tanks together.
    totals = [sum(fit_tank(t["values"], sh, k)[1].min() for t, sh in prepared) for k in SHADOW_RATIOS]
    k = float(SHADOW_RATIOS[int(np.argmin(totals))])
    out = []
    for t, sh in prepared:
        i, sse = fit_tank(t["values"], sh, k)
        full = sse[-1] if sse[-1] > 0 else np.nan
        tol = sse[i] + 0.10 * abs(sse[i]) + 1e-12  # fills whose fit is within 10% of the best
        ok = FILLS[sse <= tol]
        out.append({
            "osm_id": t["osm_id"],
            "fill_pct": round(100 * float(FILLS[i]), 1),
            "fill_lo_pct": round(100 * float(ok.min()), 1),
            "fill_hi_pct": round(100 * float(ok.max()), 1),
            "roof_depth_m": round(float(depths[i]), 2),
            "fit_gain": round(float(1 - sse[i] / full), 4) if full == full else None,
            "n_px": int(len(t["values"])),
            "shadow_ratio": k,
        })
    return out
