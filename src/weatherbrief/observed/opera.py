"""OPERA ODIM_H5 composite reader (DBZH reflectivity, RATE rain rate).

The EUMETNET OPERA composite is the only pan-European radar mosaic, and its
defining property for us is that **half of it is not radar at all**: 49.4% of
the grid carries the ODIM ``nodata`` marker — sea, mountain shadow, and the
edges beyond any member radar's range.  ODIM distinguishes that from
``undetect``, which means a radar looked at the pixel and found nothing.  Both
decode to "no value", and conflating them turns "we cannot see there" into
"it is clear there", which is the single most dangerous thing this module
could do.  :class:`~weatherbrief.observed.frames.GridFrame` therefore carries
the two as separate masks and this reader never collapses them.

Geolocation comes from the file: ``/where/projdef`` is a proj4 string and the
corner lat/lons pin it.  We project the *upper-left* corner rather than
trusting a nominal grid origin, because the OPERA domain has been re-cut
before and the file is the only thing that knows which cut it is.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from weatherbrief.models.observed import ObservedAttribution

from .frames import GridFrame
from .grid import GridSpec, GridWindow

logger = logging.getLogger(__name__)

# OPERA composite key template on the anonymous 24-hour S3 cache.  Keys are
# fully deterministic from (valid_time, quantity) — no listing required, which
# is what lets the collector run as a cheap timed poll rather than a crawler.
OPERA_S3_BUCKET = "openradar-24h"
OPERA_KEY_TEMPLATE = (
    "{yyyy}/{mm}/{dd}/OPERA/COMP/OPERA@{yyyy}{mm}{dd}T{hh}{mi}@0@{quantity}.h5"
)

DEFAULT_LICENSE = (
    "EUMETNET OPERA radar composite, provided under the OPERA data policy"
)


def opera_key(valid_time: datetime, quantity: str) -> str:
    """S3 key for one OPERA composite frame."""
    dt = valid_time.astimezone(timezone.utc)
    return OPERA_KEY_TEMPLATE.format(
        yyyy=f"{dt.year:04d}",
        mm=f"{dt.month:02d}",
        dd=f"{dt.day:02d}",
        hh=f"{dt.hour:02d}",
        mi=f"{dt.minute:02d}",
        quantity=quantity,
    )


def _attr(group, name: str):
    value = group.attrs.get(name)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, np.ndarray) and value.dtype.kind in "SU":
        return value.astype(str).tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _parse_odim_time(date_str: str | None, time_str: str | None) -> datetime | None:
    if not date_str or not time_str:
        return None
    try:
        return datetime.strptime(
            f"{date_str}{time_str[:6]}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _grid_from_where(where) -> GridSpec:
    """Build a :class:`GridSpec` from an ODIM ``/where`` group.

    ODIM's corner lat/lons describe the *outer* edges of the corner pixels, so
    the centre of pixel (0, 0) sits half a pixel inside the upper-left corner.
    Getting this wrong shifts every sample by one kilometre — invisible in a
    smoke test and wrong at exactly the scale a 5 NM annulus cares about.
    """
    proj4 = _attr(where, "projdef")
    if not proj4:
        raise ValueError("ODIM /where has no projdef")
    nx = int(_attr(where, "xsize"))
    ny = int(_attr(where, "ysize"))
    xscale = float(_attr(where, "xscale"))
    yscale = float(_attr(where, "yscale"))

    spec_probe = GridSpec(proj4=proj4, nx=nx, ny=ny, x0=0.0, y0=0.0, dx=xscale, dy=-yscale)
    ul_x, ul_y = spec_probe.lonlat_to_xy(
        float(_attr(where, "UL_lon")), float(_attr(where, "UL_lat"))
    )
    return GridSpec(
        proj4=proj4,
        nx=nx,
        ny=ny,
        x0=float(ul_x) + xscale / 2.0,
        y0=float(ul_y) - yscale / 2.0,
        dx=xscale,
        # ODIM composites are stored north-first: row 0 is the northernmost.
        dy=-yscale,
    )


def _attribution(handle) -> ObservedAttribution:
    """Read provenance out of the frame's own metadata.

    Not a constant.  One sampled composite was produced by Météo-France rather
    than centrally by EUMETNET, and ``/how`` says which — so we read it per
    frame instead of stamping a label that is right most of the time.
    """
    producer = None
    license_text = None
    url = None
    what = handle.get("what")
    how = handle.get("how")
    if what is not None:
        producer = _attr(what, "source") or None
    if how is not None:
        license_text = _attr(how, "license") or _attr(how, "copyright") or None
        url = _attr(how, "reference") or None
        system = _attr(how, "system")
        if system and not producer:
            producer = system
    if not license_text:
        license_text = DEFAULT_LICENSE
    parts = [p for p in ("EUMETNET OPERA", producer) if p]
    text = " · ".join(parts)
    if license_text:
        text = f"{text} — {license_text}" if text else license_text
    return ObservedAttribution(
        producer=producer, license=license_text, url=url, text=text
    )


def _find_data_group(handle, quantity: str):
    """Locate the ``/datasetN/dataM`` group holding ``quantity``."""
    for dataset_name in sorted(k for k in handle.keys() if k.startswith("dataset")):
        dataset = handle[dataset_name]
        for data_name in sorted(k for k in dataset.keys() if k.startswith("data")):
            data = dataset[data_name]
            what = data.get("what")
            if what is None:
                continue
            if (_attr(what, "quantity") or "").upper() == quantity.upper():
                return dataset, data
    raise KeyError(f"ODIM file has no {quantity} dataset")


def read_metadata(path: Path | str, quantity: str) -> dict:
    """Sidecar metadata for one composite, without reading the pixel array."""
    import h5py

    with h5py.File(str(path), "r") as handle:
        grid = _grid_from_where(handle["where"])
        dataset, _data = _find_data_group(handle, quantity)
        nominal_time = _parse_odim_time(
            _attr(handle["what"], "date"), _attr(handle["what"], "time")
        )
        valid_time = _parse_odim_time(
            _attr(dataset["what"], "enddate"), _attr(dataset["what"], "endtime")
        ) or _parse_odim_time(
            _attr(handle["what"], "date"), _attr(handle["what"], "time")
        )
        start_time = _parse_odim_time(
            _attr(dataset["what"], "startdate"), _attr(dataset["what"], "starttime")
        )
        window_minutes = 0.0
        if start_time and valid_time:
            window_minutes = max(0.0, (valid_time - start_time).total_seconds() / 60.0)
        nodes = None
        if handle.get("how") is not None:
            nodes = _attr(handle["how"], "nodes")
        return {
            "quantity": quantity,
            "valid_time": valid_time.isoformat() if valid_time else None,
            # ODIM root /what is the product's nominal target. Preserve the
            # legacy observation valid_time above, but never use acquisition
            # end as the motion reference or infer a missing nominal target.
            "motion_valid_time": nominal_time.isoformat() if nominal_time else None,
            "acquisition_start": start_time.isoformat() if start_time else None,
            "acquisition_end": (_parse_odim_time(
                _attr(dataset["what"], "enddate"), _attr(dataset["what"], "endtime")
            ).isoformat() if _parse_odim_time(
                _attr(dataset["what"], "enddate"), _attr(dataset["what"], "endtime")
            ) else None),
            "product_id": f"OPERA:{quantity}:{_attr(handle['what'], 'version') or 'unspecified'}",
            "decoder_version": "odim_ground_v1",
            "window_minutes": window_minutes,
            "grid": {
                "proj4": grid.proj4,
                "nx": grid.nx,
                "ny": grid.ny,
                "x0": grid.x0,
                "y0": grid.y0,
                "dx": grid.dx,
                "dy": grid.dy,
            },
            "nodes": nodes,
            "attribution": _attribution(handle).model_dump(),
        }


def read_grid(path: Path | str) -> GridSpec:
    import h5py

    with h5py.File(str(path), "r") as handle:
        return _grid_from_where(handle["where"])


def read_window(
    path: Path | str,
    quantity: str,
    window: GridWindow,
    *,
    source: str,
    units: str,
) -> GridFrame:
    """Decode one pixel block of a composite into physical units.

    Only ``window`` is read off disk.  A full-grid read of the OPERA composite
    is ~4.4 million pixels for a corridor that needs a few thousand of them,
    and the per-station alternative — re-opening the file once per airport —
    is three orders of magnitude worse again.  One windowed read, then all
    sampling happens in memory.
    """
    import h5py

    with h5py.File(str(path), "r") as handle:
        grid = _grid_from_where(handle["where"])
        dataset, data = _find_data_group(handle, quantity)
        what = data["what"]
        gain = float(_attr(what, "gain") if _attr(what, "gain") is not None else 1.0)
        offset = float(_attr(what, "offset") if _attr(what, "offset") is not None else 0.0)
        nodata_raw = _attr(what, "nodata")
        undetect_raw = _attr(what, "undetect")

        row0 = max(0, min(window.row0, grid.ny))
        row1 = max(row0, min(window.row1, grid.ny))
        col0 = max(0, min(window.col0, grid.nx))
        col1 = max(col0, min(window.col1, grid.nx))
        raw = np.asarray(data["data"][row0:row1, col0:col1])

        valid_time = _parse_odim_time(
            _attr(dataset["what"], "enddate"), _attr(dataset["what"], "endtime")
        ) or _parse_odim_time(
            _attr(handle["what"], "date"), _attr(handle["what"], "time")
        )
        start_time = _parse_odim_time(
            _attr(dataset["what"], "startdate"), _attr(dataset["what"], "starttime")
        )
        attribution = _attribution(handle)

    if valid_time is None:
        raise ValueError(f"ODIM file {path} carries no usable valid time")

    nodata_mask = (
        np.isclose(raw, float(nodata_raw)) if nodata_raw is not None
        else np.zeros(raw.shape, dtype=bool)
    )
    undetect_mask = (
        np.isclose(raw, float(undetect_raw)) if undetect_raw is not None
        else np.zeros(raw.shape, dtype=bool)
    )
    # A raw value that matches both markers (some producers set them equal) is
    # the pessimistic case: treat it as "not looked at", never as "clear".
    undetect_mask &= ~nodata_mask

    values = offset + gain * raw.astype(np.float32)
    values[nodata_mask | undetect_mask] = np.nan

    window_minutes = 0.0
    if start_time and valid_time:
        window_minutes = max(0.0, (valid_time - start_time).total_seconds() / 60.0)

    return GridFrame(
        source=source,
        quantity=quantity,
        units=units,
        valid_time=valid_time,
        window_minutes=window_minutes,
        grid=grid,
        window=GridWindow(row0, row1, col0, col1, full_width=window.full_width),
        values=values,
        nodata=nodata_mask,
        undetect=undetect_mask,
        attribution=attribution,
    )
