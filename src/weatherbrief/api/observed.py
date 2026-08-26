"""Observed-conditions imagery and flash endpoints (#574).

The sampled numbers ride inline on ``briefing.json``; **imagery never does**.
A 2 km composite clipped to a route corridor is hundreds of kilobytes of PNG,
and putting it in the briefing payload would make every pack load pay for a
layer most of them do not draw.  So the map asks for it here, once, when the
layer is switched on.

Three endpoints:

  GET /api/observed/status
      → which streams this deployment holds, how old each frame is, and the
        attribution to render.  Cheap: a directory listing of sidecars.

  GET /api/observed/overlay/{source}.png?south=&west=&north=&east=
      → the newest frame, clipped to the requested rectangle, as a plate-carrée
        RGBA PNG for a single Leaflet ``imageOverlay``.

  GET /api/observed/flashes?south=&west=&north=&east=
      → lightning as points with their own times, so the client can fade them
        by age rather than showing a ten-minute accumulation as one instant.

Auth mirrors the other flight-independent map endpoints: any authenticated
user.  Nothing here is user-specific, but none of it is public either.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from flyfun_common.db import current_user_id
from weatherbrief.observed.collect import observed_enabled
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
)
from weatherbrief.observed.grid import compute_window
from weatherbrief.observed.imagery import (
    AUX_FIELDS,
    OverlayBounds,
    legend_for,
    render_overlay,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observed", tags=["observed"])

# Largest rectangle we will render in one overlay.  A route corridor is a few
# degrees; anything much larger is a pan-European request this endpoint is not
# for (that is the follow-up issue's forecast-map path).
MAX_SPAN_DEG = 25.0

# Frames are immutable once written and are named by their valid time, so the
# bytes for a given (source, stamp, bbox) never change.  The URL carries the
# stamp, so a long cache is safe and the client re-requests when the stamp
# advances.
_CACHE_CONTROL = "private, max-age=240"

IMAGE_SOURCES = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, SOURCE_EUMETSAT_CTTH)

# Pseudo-sources: a different QUANTITY off a frame we already collect, exposed
# under its own id so the URL shape and the client's single "which overlay"
# string stay unchanged.  `eumetsat_ctth_temp` draws the CTTH granule's
# cloud-top temperature rather than its height — on a map there is no altitude
# axis, so temperature is genuinely new information rather than a restatement
# of position, which is exactly the opposite of the cross-section's case.
IMAGE_PSEUDO_SOURCES = dict(AUX_FIELDS)  # pseudo id -> (real source, field, stops)

PSEUDO_LABELS = {"eumetsat_ctth_temp": "Cloud-top temperature"}
# Kelvin on the wire, as the granule stores it; the client converts for display.
PSEUDO_UNITS = {"eumetsat_ctth_temp": "K"}


def _resolve_imagery(source: str) -> tuple[str, str | None]:
    """(real source, aux field) for an imagery id, or raise 404."""
    if source in IMAGE_SOURCES:
        return source, None
    entry = IMAGE_PSEUDO_SOURCES.get(source)
    if entry is not None:
        return entry[0], entry[1]
    raise HTTPException(status_code=404, detail="Unknown imagery source")


def _require_enabled() -> None:
    if not observed_enabled():
        raise HTTPException(status_code=404, detail="Observed conditions not enabled")


def _bounds(south: float, west: float, north: float, east: float) -> OverlayBounds:
    if north <= south or east <= west:
        raise HTTPException(status_code=400, detail="Empty bounding box")
    if (north - south) > MAX_SPAN_DEG or (east - west) > MAX_SPAN_DEG:
        raise HTTPException(status_code=400, detail="Bounding box too large")
    return OverlayBounds(south=south, west=west, north=north, east=east)


@router.get("/status")
def observed_status(_user_id: str = Depends(current_user_id)) -> dict[str, Any]:
    """What this deployment holds right now, per source.

    Each entry carries its own frame's valid time and age — there is no
    payload-level "as of", because the four streams do not share one.
    """
    _require_enabled()
    store = FrameStore()
    now = datetime.now(timezone.utc)
    sources = []
    for key, spec in SOURCE_SPECS.items():
        newest = store.latest(key, now=now)
        entry: dict[str, Any] = {
            "source": key,
            "label": spec.label,
            "units": spec.units,
            "interval_minutes": spec.interval.total_seconds() / 60.0,
            # Non-zero where the product is an accumulation or a rolling
            # maximum rather than an instant — a 10-minute rolling max is not
            # a snapshot and the client must be able to say so.
            "window_minutes": spec.window_minutes,
            "renders_imagery": key in IMAGE_SOURCES,
            "available": newest is not None,
            "legend": legend_for(key),
        }
        if newest is not None:
            age = newest.age_minutes(now)
            entry.update(
                valid_time=newest.valid_time.isoformat(),
                age_minutes=round(age, 1),
                stale=age > spec.max_display_age.total_seconds() / 60.0,
                attribution=newest.attribution.model_dump(),
            )
        sources.append(entry)

    # Pseudo-sources ride alongside, sharing the underlying frame's timing and
    # attribution but carrying their own units and ramp. The client needs them
    # in this list or it cannot draw a legend for a layer it can select.
    for pseudo, (real, _field, _stops) in IMAGE_PSEUDO_SOURCES.items():
        base = next((s for s in sources if s["source"] == real), None)
        if base is None:
            continue
        entry = dict(base)
        entry.update(
            source=pseudo,
            label=PSEUDO_LABELS.get(pseudo, pseudo),
            units=PSEUDO_UNITS.get(pseudo, ""),
            legend=legend_for(pseudo),
        )
        sources.append(entry)
    return {"sources": sources}


@router.get("/overlay/{source}.png")
def observed_overlay(
    source: str,
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    _user_id: str = Depends(current_user_id),
) -> Response:
    """Newest frame for ``source``, clipped to the rectangle, as one PNG."""
    _require_enabled()
    source, field = _resolve_imagery(source)
    bounds = _bounds(south, west, north, east)

    store = FrameStore()
    spec = SOURCE_SPECS[source]
    newest = store.latest(source, max_age=spec.max_display_age)
    if newest is None:
        # 410, not 404: the source exists and is configured, we just have
        # nothing current enough to draw.
        raise HTTPException(status_code=410, detail="No current frame")

    try:
        frame = _read_bbox(source, newest.path, bounds)
    except Exception as exc:
        logger.warning("Observed overlay render failed for %s", source, exc_info=True)
        raise HTTPException(status_code=500, detail="Frame unreadable") from exc

    png, _ = render_overlay(frame, bounds, field=field)
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": _CACHE_CONTROL,
            # The overlay's own age and provenance travel with the bytes so
            # the map's badge cannot drift from the image it labels.  The
            # attribution is percent-encoded: HTTP header values are latin-1
            # and real provenance strings carry em dashes and accented
            # producer names (one composite is Météo-France's).  The client
            # decodes with decodeURIComponent.
            "X-Observed-Valid-Time": frame.valid_time.isoformat(),
            "X-Observed-Attribution": quote(frame.attribution.text or ""),
        },
    )


@router.get("/flashes")
def observed_flashes(
    south: float = Query(...),
    west: float = Query(...),
    north: float = Query(...),
    east: float = Query(...),
    _user_id: str = Depends(current_user_id),
) -> dict[str, Any]:
    """Lightning flashes inside the rectangle, each with its own time.

    Returned as points rather than a raster: the map fades them by age, which
    a single accumulated image cannot express.
    """
    _require_enabled()
    bounds = _bounds(south, west, north, east)
    store = FrameStore()
    spec = SOURCE_SPECS[SOURCE_EUMETSAT_LI]

    from weatherbrief.observed import lightning

    # Every retained frame, not just the newest: the trail is the point.
    now = datetime.now(timezone.utc)
    horizon = now - spec.retention
    flashes: list[dict[str, Any]] = []
    attribution: dict[str, Any] = {}
    newest_valid: datetime | None = None

    for stored in store.list_frames(SOURCE_EUMETSAT_LI):
        if stored.valid_time < horizon:
            break
        try:
            frame = lightning.read_flashes(
                stored.path,
                source=SOURCE_EUMETSAT_LI,
                window_minutes=spec.window_minutes,
            )
        except Exception:
            logger.warning("Unreadable LI frame %s", stored.path, exc_info=True)
            continue
        if newest_valid is None:
            newest_valid = frame.valid_time
            attribution = frame.attribution.model_dump()
        inside = (
            (frame.lats >= bounds.south)
            & (frame.lats <= bounds.north)
            & (frame.lons >= bounds.west)
            & (frame.lons <= bounds.east)
        )
        for lat, lon, when in zip(
            frame.lats[inside], frame.lons[inside], frame.times[inside]
        ):
            flashes.append(
                {
                    "lat": round(float(lat), 4),
                    "lon": round(float(lon), 4),
                    "time": _iso(when),
                }
            )

    return {
        "flashes": flashes,
        "count": len(flashes),
        "newest_valid_time": newest_valid.isoformat() if newest_valid else None,
        "window_minutes": spec.window_minutes,
        "retention_minutes": spec.retention.total_seconds() / 60.0,
        "attribution": attribution,
    }


def _iso(value) -> str:
    stamp = np.datetime64(value, "s").astype("datetime64[s]").astype(object)
    return stamp.replace(tzinfo=timezone.utc).isoformat()


def _read_bbox(source: str, path, bounds: OverlayBounds):
    """Read just the pixels the rectangle needs.

    The corners alone do not bound a projected grid — a rectangle in lat/lon
    is a curved quadrilateral in LAEA or geostationary space — so the window
    is computed from a sampled perimeter rather than four points.
    """
    from weatherbrief.observed import ctth, opera

    lats, lons = _perimeter(bounds)
    if source in (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE):
        grid = opera.read_grid(path)
        window = compute_window(grid, lats, lons, radius_km=0.0, pad_km=4.0)
        if window.is_empty():
            raise ValueError("bounding box does not intersect the frame")
        return opera.read_window(
            path,
            SOURCE_SPECS[source].quantity,
            window,
            source=source,
            units=SOURCE_SPECS[source].units,
        )

    import netCDF4

    with netCDF4.Dataset(str(path)) as dataset:
        grid = ctth.read_grid(dataset)
    window = compute_window(
        grid,
        lats,
        lons,
        radius_km=0.0,
        # Enough slack to include the pixels whose parallax-corrected position
        # falls inside the rectangle even though their imagery position does
        # not — the overlay scatters each detection to its corrected position,
        # so those pixels are exactly the ones that end up drawn inside the
        # box.  Scaled to the rectangle's own viewing geometry: all four
        # corners, because the most obliquely-viewed one may be any of them
        # once longitude counts and not just latitude.
        pad_km=ctth.parallax_pad_km(
            [bounds.north, bounds.north, bounds.south, bounds.south],
            [bounds.west, bounds.east, bounds.west, bounds.east],
        ),
        full_width=True,
    )
    if window.is_empty():
        raise ValueError("bounding box does not intersect the frame")
    return ctth.read_window(path, window, source=source)


def _perimeter(bounds: OverlayBounds, steps: int = 16):
    """Sampled lat/lon perimeter of a rectangle, for projected-window bounds."""
    lats = np.linspace(bounds.south, bounds.north, steps)
    lons = np.linspace(bounds.west, bounds.east, steps)
    perimeter_lats = np.concatenate(
        [np.full(steps, bounds.south), np.full(steps, bounds.north), lats, lats]
    )
    perimeter_lons = np.concatenate(
        [lons, lons, np.full(steps, bounds.west), np.full(steps, bounds.east)]
    )
    return perimeter_lats, perimeter_lons
