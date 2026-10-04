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

  GET /api/observed/frames/{source}
      → the frames a tiled layer can draw, newest first, with the tile URL
        template (#652).  Radar from the local store; ``satellite_ir`` from
        EUMETView's advertised cycles.

  GET /api/observed/tiles/{source}/{stamp}/{z}/{x}/{y}.png
      → one Web Mercator tile of one frame.  Keyed by the frame stamp, so the
        bytes never change and the response is cacheable for the frame's life
        — and a loop (#653) is a different stamp, not a different pipeline.

  GET /api/observed/cells/frames
  GET /api/observed/cells/{stamp}.json[?south=&west=&north=&east=]
      → the cell overlay pushed by the home node's analysis (#656): the stamps
        held, newest first, with the stale state; one frame's outlines and
        cells, optionally clipped to the route's box.  Own flag
        (``WB_CELLS_INGEST_ENABLED``); the listing says "disabled" rather than 404.

Auth mirrors the other flight-independent map endpoints: any authenticated
user.  Nothing here is user-specific, but none of it is public either.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from flyfun_common.db import current_user_id
from weatherbrief.observed.collect import observed_enabled
from weatherbrief.observed import satellite_ir, tiles
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    SOURCE_SPECS,
    FrameStore,
    frame_stamp,
    parse_frame_stamp,
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
# Tiles carry the frame stamp in their path, so their bytes are final.  A day
# outlives every retention window; `immutable` stops revalidation on reload.
_TILE_CACHE_CONTROL = "private, max-age=86400, immutable"

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


# --- Tiled layers (#652) -----------------------------------------------------------


def _tile_url_template(source: str) -> str:
    return f"/api/observed/tiles/{source}/{{stamp}}/{{z}}/{{x}}/{{y}}.png"


@router.get("/frames/{source}")
def observed_frames(
    source: str,
    _user_id: str = Depends(current_user_id),
) -> dict[str, Any]:
    """Frames a tiled layer can draw, newest first.

    Every frame still retained is listed, not only the newest: the map draws
    the first, and a loop (#653) steps through the rest.  Ages are each
    frame's own.
    """
    _require_enabled()
    now = datetime.now(timezone.utc)

    if source == satellite_ir.SOURCE_ID:
        if not satellite_ir.satellite_ir_enabled():
            raise HTTPException(status_code=404, detail="Satellite imagery not enabled")
        try:
            times = satellite_ir.available_times()
        except satellite_ir.SatelliteUnavailable as exc:
            raise HTTPException(status_code=503, detail="Satellite imagery unavailable") from exc
        frames = [_frame_entry(satellite_ir.stamp(t), t, now) for t in times]
        return {
            "source": source,
            "label": satellite_ir.LABEL,
            "frames": frames,
            "stale": not frames
            or (now - times[0]) > satellite_ir.MAX_DISPLAY_AGE,
            "window_minutes": 0.0,
            "attribution": {"text": satellite_ir.ATTRIBUTION},
            "tile_url_template": _tile_url_template(source),
            "min_zoom": satellite_ir.MIN_ZOOM,
            "max_zoom": satellite_ir.MAX_ZOOM,
        }

    if source not in tiles.TILE_SOURCES:
        raise HTTPException(status_code=404, detail="Not a tiled source")
    spec = SOURCE_SPECS[source]
    store = FrameStore()
    stored = store.list_frames(source)
    if tiles.tiles_enabled():
        # The collector builds each canvas right after publishing the frame,
        # so for a moment the listing could run ahead of it — and every tile
        # of that frame would then build it in the request path. Offer only
        # frames whose canvas is ready; the previous frame draws meanwhile.
        stored = [f for f in stored if tiles.has_canvas(store, source, f.valid_time)]
    frames = [_frame_entry(frame_stamp(f.valid_time), f.valid_time, now) for f in stored]
    newest = stored[0] if stored else None
    return {
        "source": source,
        "label": spec.label,
        "frames": frames,
        "stale": newest is None
        or (now - newest.valid_time) > spec.max_display_age,
        "window_minutes": spec.window_minutes,
        "attribution": newest.attribution.model_dump() if newest else {},
        "tile_url_template": _tile_url_template(source),
        "min_zoom": tiles.MIN_TILE_ZOOM,
        "max_zoom": tiles.MAX_TILE_ZOOM,
    }


def _frame_entry(stamp: str, valid_time: datetime, now: datetime) -> dict[str, Any]:
    return {
        "stamp": stamp,
        "valid_time": valid_time.isoformat(),
        "age_minutes": round((now - valid_time).total_seconds() / 60.0, 1),
    }


@router.get("/tiles/{source}/{stamp}/{z}/{x}/{y}.png")
def observed_tile(
    source: str,
    stamp: str,
    z: int,
    x: int,
    y: int,
    _user_id: str = Depends(current_user_id),
) -> Response:
    """One Web Mercator tile of one frame."""
    _require_enabled()
    try:
        valid_time = parse_frame_stamp(stamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bad frame stamp") from exc

    if source == satellite_ir.SOURCE_ID:
        if not satellite_ir.satellite_ir_enabled():
            raise HTTPException(status_code=404, detail="Satellite imagery not enabled")
        if not satellite_ir.valid_tile(z, x, y):
            raise HTTPException(status_code=404, detail="Tile out of range")
        try:
            # Only cycles we advertise: the proxy is not a window onto
            # EUMETView's whole archive.
            if valid_time not in satellite_ir.available_times():
                raise HTTPException(status_code=404, detail="Unknown satellite cycle")
            png = satellite_ir.fetch_tile(valid_time, z, x, y)
        except satellite_ir.SatelliteUnavailable as exc:
            raise HTTPException(status_code=503, detail="Satellite imagery unavailable") from exc
        # A young cycle may still have been completing upstream: cache it
        # briefly, so a partial tile is not pinned in the browser for a day.
        young = satellite_ir.is_young(valid_time)
        return _tile_response(png, _CACHE_CONTROL if young else _TILE_CACHE_CONTROL)

    if source not in tiles.TILE_SOURCES:
        raise HTTPException(status_code=404, detail="Not a tiled source")
    if not tiles.valid_tile(z, x, y):
        raise HTTPException(status_code=404, detail="Tile out of range")
    try:
        png = tiles.tile_png(FrameStore(), source, valid_time, z, x, y)
    except FileNotFoundError as exc:
        # Purged (older than retention) or never collected: gone, not broken.
        raise HTTPException(status_code=410, detail="Frame not stored") from exc
    except tiles.CanvasBusy as exc:
        raise HTTPException(
            status_code=503, detail="Frame being prepared", headers={"Retry-After": "2"}
        ) from exc
    except tiles.CanvasUnavailable as exc:
        # A frame whose canvas cannot be built is not coming back: answer
        # "gone" (the build failure is remembered, so this costs no re-read).
        logger.warning("Observed tile frame unusable for %s %s: %s", source, stamp, exc)
        raise HTTPException(status_code=410, detail="Frame unreadable") from exc
    except Exception as exc:
        logger.warning("Observed tile render failed for %s %s", source, stamp, exc_info=True)
        raise HTTPException(status_code=500, detail="Tile render failed") from exc
    return _tile_response(png)


def _tile_response(png: bytes, cache_control: str = _TILE_CACHE_CONTROL) -> Response:
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": cache_control},
    )


# --- Cell overlay (#656) -----------------------------------------------------------
#
# Display files pushed by the home node's cell analysis and ingested into
# DATA_DIR/observed/cells/display (observed/cells_display.py). Gated on
# WB_CELLS_INGEST_ENABLED rather than WB_OBSERVED_ENABLED: the listing answers
# "disabled" instead of 404 so the map's Cells toggle can say why it is empty.


@router.get("/cells/frames")
def observed_cell_frames(_user_id: str = Depends(current_user_id)) -> dict[str, Any]:
    """Overlay stamps newest first, each with its valid and received time.

    ``stale`` / ``unavailable_since`` carry "cell analysis unavailable since
    HH:MMZ": the newest overlay's own time when it is too old to draw.
    """
    from weatherbrief.observed import cells_display

    if not cells_display.cells_ingest_enabled():
        return cells_display.disabled_status()
    return cells_display.frames_status(cells_display.DisplayStore())


@router.get("/cells/{stamp}.json")
def observed_cell_display(
    stamp: str,
    request: Request,
    south: float | None = Query(None),
    west: float | None = Query(None),
    north: float | None = Query(None),
    east: float | None = Query(None),
    _user_id: str = Depends(current_user_id),
) -> Response:
    """One frame's overlay, optionally clipped to a box (the route map's).

    Keyed by stamp, so the body never changes: cacheable like the tiles.
    """
    import json as _json

    from weatherbrief.observed import cells_display

    if not cells_display.cells_ingest_enabled():
        raise HTTPException(status_code=404, detail="Cell analysis not enabled")
    try:
        parse_frame_stamp(stamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bad frame stamp") from exc
    box = (south, west, north, east)
    if any(v is not None for v in box) and any(v is None for v in box):
        raise HTTPException(status_code=400, detail="Give all of south, west, north, east or none")
    store = cells_display.DisplayStore()
    data = store.read(stamp)
    if data is None:
        # Purged (older than retention), never received, or invalid.
        raise HTTPException(status_code=410, detail="Overlay not stored")
    headers = {"Cache-Control": _TILE_CACHE_CONTROL}
    if south is None and "gzip" in request.headers.get("accept-encoding", "").lower():
        # The whole of Europe (the "Now" tab): the stored file already is
        # gzipped JSON (~150 KB vs ~1 MB raw) and was validated just above, so
        # hand its bytes over as-is and let the browser inflate them.
        try:
            raw = store.path(stamp).read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=410, detail="Overlay not stored") from exc
        return Response(content=raw, media_type="application/json",
                        headers={**headers, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    if south is not None:
        if north <= south or east <= west:
            raise HTTPException(status_code=400, detail="Empty bounding box")
        data = cells_display.filter_bbox(data, south, west, north, east)
    return Response(
        content=_json.dumps(data, separators=(",", ":")),
        media_type="application/json",
        headers={**headers, "Vary": "Accept-Encoding"},
    )
