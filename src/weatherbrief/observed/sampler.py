"""Corridor sampler — one primitive, two call sites.

:func:`sample` takes a decoded frame window and a list of stations and returns,
for each station, one entry per requested radius.  The briefing passes a route
bbox and the ~200 stations along it; the pan-European follow-up passes a Europe
window and 620 airports.  Same function, same guarantees.

Three rules the implementation exists to enforce:

**Never per-station file access, never a full-grid read.**  The reader has
already pulled one window off disk.  Everything here is numpy on that window,
per-station sub-boxes sliced out of memory.  The per-airport research scripts
that re-opened the granule for each waypoint extrapolated to ~100–160 s for a
route; this path measures ~42 ms for radar and ~90–130 ms for CTTH.

**Parallax before corridor membership.**  For a frame carrying the satellite's
``delta_latitude``/``delta_longitude`` fields, a detected pixel belongs to the
station whose disc contains its *corrected* ground position, not the position
the image assigns it.  Measured displacement is 52 km median against a 37 km
corridor, so this is the difference between sampling the right place and
sampling a different one.  Clear-sky and no-coverage pixels carry no
displacement and use their nominal position.

**Absence stays three-state.**  Every count is reported: pixels the sensor
never looked at (including any part of the disc that fell outside the window
that was read, or off the product entirely) land in ``nodata_px``; pixels it
looked at and found empty land in ``undetect_px``.  Statistics are computed
over detections only, so an empty radar disc reports "no echo", never a rain
rate of zero averaged out of nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from weatherbrief.models.observed import (
    CLOUD_TOP_FL_BINS,
    ObservedAnnulus,
    ObservedFlashAnnulus,
    ObservedTopsAnnulus,
)

from .ctth import metres_to_fl
from .frames import FlashFrame, GridFrame
from .grid import GridWindow, haversine_km, km_to_nm, nm_to_km

logger = logging.getLogger(__name__)

# Radii sampled for every station.  All three ship in the payload together so
# the client's corridor selector is a pick, not a re-fetch.
DEFAULT_RADII_NM: tuple[float, ...] = (5.0, 10.0, 20.0)


@dataclass(frozen=True)
class SampleStation:
    """A point to sample around.  ``id`` is whatever the caller keys on."""

    id: str
    lat: float
    lon: float


def _subbox_indices(frame: GridFrame, station: SampleStation, reach_km: float):
    """Unclipped (row, col) index mesh covering ``reach_km`` around a station.

    Deliberately *unclipped*: indices outside the grid or outside the loaded
    window still take part, because a disc that runs off the edge of what was
    read has genuinely not been looked at and must be counted as ``nodata``
    rather than quietly shrinking the denominator.
    """
    grid = frame.grid
    col_c, row_c = grid.lonlat_to_colrow(station.lon, station.lat)
    if not (np.isfinite(col_c) and np.isfinite(row_c)):
        return None
    reach_m = reach_km * 1000.0
    half_cols = int(np.ceil(reach_m / abs(grid.dx))) + 1
    half_rows = int(np.ceil(reach_m / abs(grid.dy))) + 1
    rows = np.arange(int(np.floor(row_c)) - half_rows, int(np.ceil(row_c)) + half_rows + 1)
    cols = np.arange(int(np.floor(col_c)) - half_cols, int(np.ceil(col_c)) + half_cols + 1)
    return np.meshgrid(cols, rows)


def _gather(array: np.ndarray, local_rows: np.ndarray, local_cols: np.ndarray, inside: np.ndarray):
    """Index ``array`` where ``inside``, returning a safe value elsewhere."""
    safe_rows = np.clip(local_rows, 0, array.shape[0] - 1)
    safe_cols = np.clip(local_cols, 0, array.shape[1] - 1)
    picked = array[safe_rows, safe_cols]
    return picked, inside


def sample(
    frame: GridFrame,
    window: GridWindow | None,
    stations: list[SampleStation],
    radii_nm: tuple[float, ...] | list[float] = DEFAULT_RADII_NM,
) -> dict[str, list[ObservedAnnulus]]:
    """Sample ``frame`` in concentric discs around every station.

    Each returned entry is the *disc* of that radius, not a ring: "within
    10 NM" is the question a pilot asks, and cumulative discs answer it
    without the client having to add rings back together.

    ``window`` defaults to the window the frame was read with.  Pass it
    explicitly when the caller computed the window separately (both call sites
    do) — it is validated against the frame so a mismatched pair fails here
    rather than silently sampling the wrong pixels.

    When the frame carries CTTH's parallax and ``quality_method`` fields the
    entries are :class:`ObservedTopsAnnulus`, with the FL and method
    histograms filled in.
    """
    window = window or frame.window
    # Compare the full extent, not just the origin: a window with the right
    # corner but the wrong size would offset nothing yet describe a different
    # block, which is exactly the "silently sampling the wrong pixels" case
    # this check exists to prevent.
    if (window.row0, window.row1, window.col0, window.col1) != (
        frame.window.row0,
        frame.window.row1,
        frame.window.col0,
        frame.window.col1,
    ):
        raise ValueError(
            f"window {window} does not match the frame's own window {frame.window}"
        )

    is_tops = "quality_method" in frame.aux
    has_parallax = "delta_latitude" in frame.aux and "delta_longitude" in frame.aux
    radii = sorted(float(r) for r in radii_nm)
    max_reach_km = nm_to_km(max(radii)) if radii else 0.0
    if has_parallax and stations:
        from .ctth import parallax_pad_km

        # Scaled to the northernmost station: displacement grows with the
        # satellite zenith angle, so a fixed pad that works over France loses
        # high cloud over Norway.
        max_reach_km += parallax_pad_km(max(abs(s.lat) for s in stations))

    grid = frame.grid
    results: dict[str, list[ObservedAnnulus]] = {}

    for station in stations:
        mesh = _subbox_indices(frame, station, max_reach_km)
        if mesh is None:
            results[station.id] = _empty_annuli(radii, is_tops)
            continue
        cc, rr = mesh

        lon, lat = grid.colrow_to_lonlat(cc, rr)
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        finite = np.isfinite(lon) & np.isfinite(lat)

        on_grid = (rr >= 0) & (rr < grid.ny) & (cc >= 0) & (cc < grid.nx)
        in_window = (
            (rr >= frame.window.row0)
            & (rr < frame.window.row1)
            & (cc >= frame.window.col0)
            & (cc < frame.window.col1)
        )
        readable = on_grid & in_window & finite

        local_rows = rr - frame.window.row0
        local_cols = cc - frame.window.col0
        detected_px, _ = _gather(frame.detected, local_rows, local_cols, readable)
        detected_px = detected_px & readable
        undetect_px, _ = _gather(frame.undetect, local_rows, local_cols, readable)
        undetect_px = undetect_px & readable
        values, _ = _gather(np.asarray(frame.values), local_rows, local_cols, readable)

        dist_nominal = haversine_km(station.lat, station.lon, lat, lon)
        dist_nominal = np.where(finite, dist_nominal, np.inf)

        if has_parallax:
            dlat, _ = _gather(frame.aux["delta_latitude"], local_rows, local_cols, readable)
            dlon, _ = _gather(frame.aux["delta_longitude"], local_rows, local_cols, readable)
            corrected = haversine_km(
                station.lat, station.lon, lat + dlat, lon + dlon
            )
            dist_detected = np.where(finite, corrected, np.inf)
        else:
            dist_detected = dist_nominal

        # A detected pixel is placed by its corrected ground position; every
        # other pixel has no cloud to displace, so it keeps its own.
        distance = np.where(detected_px, dist_detected, dist_nominal)

        quality = None
        if is_tops:
            quality, _ = _gather(frame.aux["quality_method"], local_rows, local_cols, readable)
            # A pixel can carry a method code and still have no usable height
            # (or no usable parallax), in which case it is `nodata` and must
            # not appear in the histogram — the counts would then exceed
            # `detected_px` and stop adding up.  Clear-sky (code 0) is kept:
            # it is `undetect`, a real observation, and the histogram is where
            # "62% of this disc had no cloud" is legible.
            quality = np.where(detected_px | undetect_px, quality, -1)

        annuli: list[ObservedAnnulus] = []
        for radius_nm in radii:
            radius_km = nm_to_km(radius_nm)
            in_disc = distance <= radius_km
            # Part of the disc that was never looked at: off the product, or
            # outside the window that was read.
            unavailable = int(((dist_nominal <= radius_km) & ~readable).sum())
            counted = in_disc & readable

            n_detected = int((counted & detected_px).sum())
            n_undetect = int((counted & undetect_px).sum())
            n_nodata = int((counted & ~detected_px & ~undetect_px).sum()) + unavailable
            total = n_detected + n_undetect + n_nodata

            sample_values = values[counted & detected_px]
            sample_values = sample_values[np.isfinite(sample_values)]
            stats = _stats(sample_values)

            common = dict(
                radius_nm=radius_nm,
                total_px=total,
                valid_px=n_detected + n_undetect,
                nodata_px=n_nodata,
                undetect_px=n_undetect,
                detected_px=n_detected,
                **stats,
            )
            if is_tops:
                annuli.append(
                    ObservedTopsAnnulus(
                        **common,
                        fl_bins=_fl_histogram(sample_values),
                        quality_method=_quality_histogram(quality, counted),
                        highest_fl=(
                            float(metres_to_fl(sample_values.max()))
                            if sample_values.size
                            else None
                        ),
                    )
                )
            else:
                annuli.append(ObservedAnnulus(**common))
        results[station.id] = annuli

    return results


def sample_flashes(
    frame: FlashFrame,
    stations: list[SampleStation],
    radii_nm: tuple[float, ...] | list[float] = DEFAULT_RADII_NM,
) -> dict[str, list[ObservedFlashAnnulus]]:
    """Count flashes in concentric discs around every station.

    No coverage bookkeeping: the imager sees the whole disc, so zero flashes
    is an observation rather than a gap.
    """
    radii = sorted(float(r) for r in radii_nm)
    results: dict[str, list[ObservedFlashAnnulus]] = {}
    has_flashes = frame.lats.size > 0

    for station in stations:
        distances = (
            haversine_km(station.lat, station.lon, frame.lats, frame.lons)
            if has_flashes
            else np.empty(0)
        )
        annuli: list[ObservedFlashAnnulus] = []
        for radius_nm in radii:
            radius_km = nm_to_km(radius_nm)
            inside = distances <= radius_km if has_flashes else np.zeros(0, dtype=bool)
            count = int(inside.sum())
            latest = None
            if count:
                latest_np = frame.times[inside].max()
                latest = _to_datetime(latest_np)
            annuli.append(
                ObservedFlashAnnulus(
                    radius_nm=radius_nm,
                    flash_count=count,
                    area_km2=float(np.pi * radius_km**2),
                    window_minutes=frame.window_minutes,
                    # Scoped to THIS disc, not to the frame. Reporting the
                    # nearest flash anywhere would put a number outside the
                    # annulus on an annulus-scoped field — a 5 NM disc with no
                    # flashes in it reading "nearest 7.6 NM". The widest disc
                    # already answers "how far away is the nearest lightning".
                    nearest_flash_nm=(
                        float(km_to_nm(distances[inside].min())) if count else None
                    ),
                    latest_flash_time=latest,
                )
            )
        results[station.id] = annuli
    return results


# --- helpers ---------------------------------------------------------------


def _to_datetime(value):
    from datetime import timezone

    stamp = np.datetime64(value, "s").astype("datetime64[s]").astype(object)
    return stamp.replace(tzinfo=timezone.utc)


def _stats(values: np.ndarray) -> dict[str, float | None]:
    if values.size == 0:
        return {"max_value": None, "mean_value": None, "p90_value": None}
    return {
        "max_value": float(np.max(values)),
        "mean_value": float(np.mean(values)),
        "p90_value": float(np.percentile(values, 90)),
    }


def _fl_histogram(height_m: np.ndarray) -> dict[str, int]:
    if height_m.size == 0:
        return {label: 0 for label, _lo, _hi in CLOUD_TOP_FL_BINS}
    fls = np.asarray(metres_to_fl(height_m), dtype=float)
    return {
        label: int(((fls >= lo) & (fls < hi)).sum())
        for label, lo, hi in CLOUD_TOP_FL_BINS
    }


def _quality_histogram(quality: np.ndarray | None, counted: np.ndarray) -> dict[str, int]:
    """Per-method pixel counts inside the disc.

    Kept as the full breakdown rather than a single confidence number: ``9``
    is the multi-layer-suspect flag, and ``0`` is a positive observation of
    clear sky.  Codes below zero mark pixels with no method at all (off-disc
    or failed retrieval) and are excluded — those are already in ``nodata_px``.
    """
    if quality is None:
        return {}
    picked = quality[counted]
    picked = picked[picked >= 0]
    if picked.size == 0:
        return {}
    codes, counts = np.unique(picked, return_counts=True)
    return {str(int(code)): int(count) for code, count in zip(codes, counts)}


def _empty_annuli(radii: list[float], is_tops: bool) -> list[ObservedAnnulus]:
    """Annuli for a station that does not project onto the grid at all.

    Everything zero, which makes ``coverage_fraction`` zero and
    ``insufficient_coverage`` true — so the client renders "no coverage"
    rather than a confident clear.
    """
    factory = ObservedTopsAnnulus if is_tops else ObservedAnnulus
    return [factory(radius_nm=r) for r in radii]
