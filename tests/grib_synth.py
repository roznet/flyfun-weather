"""Synthetic GRIB message writer for the decode tests.

Writes real GRIB messages with eccodes, shaped like the products the decoders
meet in production: ECMWF a1/a2 multi-grid files that mix GRIB1 and GRIB2 on
the same area, and ICON model-level blobs. Shared by
``test_grib_reader.py`` (which pins decoded output against committed fixtures)
and ``test_ecmwf_grid_seam.py`` (which drives the seam rules), so both exercise
the same encoder and the fixtures stay comparable.

Everything here is deterministic: field values come from a seeded
``default_rng``, so the same call writes byte-identical messages.
"""

from __future__ import annotations

import numpy as np

# ECMWF delivery areas (0.25°), as found in real a1/a2 files.
EUROPE = dict(lat_first=59.5, lat_last=35.0, lon_first=-17.5, lon_last=39.5)
# GRIB2 encodes the same Europe area as 342.5 → 39.5 (crosses the meridian).
EUROPE_G2 = dict(lat_first=59.5, lat_last=35.0, lon_first=342.5, lon_last=39.5)
NORDIC = dict(lat_first=71.5, lat_last=60.0, lon_first=2.5, lon_last=40.0)
US_G1 = dict(lat_first=50.0, lat_last=20.0, lon_first=-128.0, lon_last=-71.0)
US_G2 = dict(lat_first=50.0, lat_last=20.0, lon_first=232.0, lon_last=289.0)

# Shrunk stand-ins with the real seam edges: Europe tops out at 59.5, Nordic
# starts at 60.0 and only from 2.5°E (so 1.3°W is Europe-only, like EGPB).
SEAM_EUROPE = dict(lat_first=59.5, lat_last=57.75, lon_first=-5.0, lon_last=20.0)
SEAM_NORDIC = dict(lat_first=62.0, lat_last=60.0, lon_first=2.5, lon_last=20.0)


def field(lats: np.ndarray, lons: np.ndarray, *, base: float, amp: float, seed: int) -> np.ndarray:
    """Smooth, non-linear field plus noise, so bilinear results are non-trivial."""
    rng = np.random.default_rng(seed)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    smooth = np.sin(np.radians(la) * 7.0) * np.cos(np.radians(lo) * 5.0)
    return base + amp * (smooth + 0.2 * rng.standard_normal(la.shape))


def linear_field(lats: np.ndarray, lons: np.ndarray, *, offset: float) -> np.ndarray:
    """``lat + lon/100 + offset`` — linear, so bilinear reproduces it exactly.

    Lets a seam test assert the interpolated value in closed form, which is how
    a held edge row is told apart from a correctly placed one.
    """
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    return la + lo / 100.0 + offset


def write(
    f,
    *,
    sample: str,
    short: str,
    type_of_level: str,
    level: int,
    area: dict,
    step: float = 0.25,
    j_positive: bool = False,
    base: float = 0.0,
    amp: float = 1.0,
    seed: int = 0,
    mask=None,
    values=None,
) -> None:
    """Append one regular_ll message to the open file ``f``.

    ``mask(lats_2d, lons_2d) -> bool array`` marks cells to bitmap out.
    ``values(lats, lons) -> 2-D array`` replaces the default seeded field.
    """
    import eccodes

    gid = eccodes.codes_grib_new_from_samples(sample)
    try:
        lat_first, lat_last = area["lat_first"], area["lat_last"]
        if j_positive:
            lat_first, lat_last = min(lat_first, lat_last), max(lat_first, lat_last)
        lon_first, lon_last = area["lon_first"], area["lon_last"]
        nj = int(round(abs(lat_last - lat_first) / step)) + 1
        ni = int(round(((lon_last - lon_first) % 360.0) / step)) + 1
        eccodes.codes_set(gid, "typeOfLevel", type_of_level)
        eccodes.codes_set(gid, "level", level)
        eccodes.codes_set(gid, "shortName", short)
        eccodes.codes_set(gid, "Ni", ni)
        eccodes.codes_set(gid, "Nj", nj)
        eccodes.codes_set(gid, "latitudeOfFirstGridPointInDegrees", lat_first)
        eccodes.codes_set(gid, "latitudeOfLastGridPointInDegrees", lat_last)
        eccodes.codes_set(gid, "longitudeOfFirstGridPointInDegrees", lon_first)
        eccodes.codes_set(gid, "longitudeOfLastGridPointInDegrees", lon_last)
        eccodes.codes_set(gid, "iDirectionIncrementInDegrees", step)
        eccodes.codes_set(gid, "jDirectionIncrementInDegrees", step)
        eccodes.codes_set(gid, "jScansPositively", 1 if j_positive else 0)
        lats = np.linspace(lat_first, lat_last, nj)
        lons = lon_first + step * np.arange(ni)
        lons = np.where(lons > 180.0, lons - 360.0, lons)
        vals = (
            values(lats, lons) if values is not None
            else field(lats, lons, base=base, amp=amp, seed=seed)
        )
        if mask is not None:
            la, lo = np.meshgrid(lats, lons, indexing="ij")
            m = mask(la, lo)
            eccodes.codes_set(gid, "bitmapPresent", 1)
            eccodes.codes_set(gid, "missingValue", 9999.0)
            vals = np.where(m, 9999.0, vals)
        eccodes.codes_set_values(gid, np.asarray(vals, dtype=float).ravel())
        eccodes.codes_write(gid, f)
    finally:
        eccodes.codes_release(gid)
