"""MTG Lightning Imager L2 flash reader (EO:EUM:DAT:0691).

Total lightning — intra-cloud as well as cloud-to-ground — as a point product
rather than a grid.  That distinction runs through the payload: a flash list
has no coverage mask. Zero means no detections reported in its acquisition
window, not verified full-disc coverage or a guarantee of no lightning.
:class:`~weatherbrief.models.observed.ObservedFlashAnnulus` therefore has no
``nodata``/``undetect`` split; consumers must not infer one from zero counts.

Variable naming has moved between LI product baselines, so the reader looks
for any of the known spellings rather than pinning one.  A granule that
carries none of them raises instead of silently reporting zero flashes: a
quiet zero over a thunderstorm is the one failure mode worth being loud about.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from weatherbrief.models.observed import ObservedAttribution

from .ctth import LI_COLLECTION, _parse_iso, _valid_time, acquisition_metadata
from .frames import FlashFrame

logger = logging.getLogger(__name__)

DEFAULT_LICENSE = "© EUMETSAT — MTG Lightning Imager Level 2 flashes"

_LAT_NAMES = ("latitude", "flash_latitude", "lat", "flash_lat")
_LON_NAMES = ("longitude", "flash_longitude", "lon", "flash_lon")
_TIME_NAMES = ("flash_time", "time", "flash_start_time", "start_time")


def _walk_groups(dataset):
    """Yield the dataset and every nested group, breadth-first."""
    pending = [dataset]
    while pending:
        node = pending.pop(0)
        yield node
        pending.extend(node.groups.values())


def _find_variable(dataset, names):
    for node in _walk_groups(dataset):
        for name in names:
            if name in node.variables:
                return node.variables[name]
    return None


def _flash_times(var, count: int, fallback: datetime) -> np.ndarray:
    """Per-flash times as ``datetime64[s]``.

    Falls back to the granule's own valid time when the time variable is
    unreadable — the flash positions are still usable, and losing per-flash
    timing degrades the age fade rather than the presence of lightning.
    """
    if var is None:
        return np.full(count, np.datetime64(fallback.replace(tzinfo=None), "s"))
    try:
        import netCDF4

        var.set_auto_maskandscale(True)
        raw = np.asarray(var[:]).ravel()
        units = var.getncattr("units") if "units" in var.ncattrs() else None
        if units:
            converted = netCDF4.num2date(
                raw, units, only_use_cftime_datetimes=False, only_use_python_datetimes=True
            )
            return np.asarray(
                [np.datetime64(d.replace(tzinfo=None), "s") for d in np.atleast_1d(converted)]
            )
        return np.asarray(raw, dtype="datetime64[s]")
    except Exception:
        logger.debug("Unreadable LI flash time variable; using granule time", exc_info=True)
        return np.full(count, np.datetime64(fallback.replace(tzinfo=None), "s"))


def _attribution(dataset) -> ObservedAttribution:
    attrs = set(dataset.ncattrs())
    producer = None
    license_text = None
    url = None
    for name in ("institution", "originator", "creator_name"):
        if name in attrs:
            producer = str(dataset.getncattr(name))
            break
    for name in ("license", "licence", "copyright"):
        if name in attrs:
            license_text = str(dataset.getncattr(name))
            break
    for name in ("references", "creator_url", "url"):
        if name in attrs:
            url = str(dataset.getncattr(name))
            break
    if not license_text:
        license_text = DEFAULT_LICENSE
    return ObservedAttribution(
        producer=producer,
        license=license_text,
        url=url,
        text=" · ".join(p for p in (producer, license_text) if p),
    )


def _precise_flash_times(var, count, start, end):
    """Validate each original timestamp before any display-age fallback."""
    import netCDF4

    events = [None] * count
    reasons = [("window_only_time",)] * count
    if var is None:
        return events, reasons
    try:
        var.set_auto_maskandscale(True)
        raw = np.ma.asarray(var[:]).ravel()
        if raw.size != count:
            return events, [("time_array_mismatch", "window_only_time")] * count
        units = var.getncattr("units") if "units" in var.ncattrs() else None
        for i in range(count):
            if np.ma.is_masked(raw[i]):
                reasons[i] = ("invalid_flash_time", "window_only_time")
                continue
            try:
                if units:
                    if not np.isfinite(float(raw[i])):
                        raise ValueError("nonfinite time")
                    stamp = netCDF4.num2date(raw[i].item(), units, only_use_cftime_datetimes=False,
                                            only_use_python_datetimes=True).replace(tzinfo=timezone.utc)
                else:
                    # Numeric values without a documented epoch are not times.
                    if raw.dtype.kind not in "SU":
                        raise ValueError("missing epoch")
                    stamp = _parse_iso(str(raw[i]))
                    if stamp is None:
                        raise ValueError("invalid timestamp")
                if start is None or end is None or start > end:
                    reasons[i] = ("missing_acquisition", "window_only_time")
                elif not start <= stamp <= end:
                    reasons[i] = ("out_of_window_time", "window_only_time")
                else:
                    events[i], reasons[i] = stamp, ()
            except (ValueError, TypeError, OverflowError):
                reasons[i] = ("invalid_flash_time", "window_only_time")
    except Exception:
        return events, [("invalid_flash_time", "window_only_time")] * count
    return events, reasons


def read_metadata(path: Path | str) -> dict:
    import netCDF4

    path = Path(path)
    with netCDF4.Dataset(str(path)) as dataset:
        valid = _valid_time(dataset, path)
        lat_var = _find_variable(dataset, _LAT_NAMES)
        count = int(np.asarray(lat_var[:]).size) if lat_var is not None else 0
        return {
            "quantity": "flash",
            **acquisition_metadata(dataset),
            "product_id": LI_COLLECTION + ":" + str(getattr(dataset, "product_version", "unspecified")),
            "decoder_version": "li_individual_time_v1",
            "valid_time": valid.isoformat() if valid else None,
            "flash_count": count,
            "attribution": _attribution(dataset).model_dump(),
        }


def read_flashes(path: Path | str, *, source: str, window_minutes: float) -> FlashFrame:
    """Load one LI granule's flash positions and times."""
    import netCDF4

    path = Path(path)
    with netCDF4.Dataset(str(path)) as dataset:
        valid = _valid_time(dataset, path)
        if valid is None and "sensing_end_time" in dataset.ncattrs():
            valid = _parse_iso(str(dataset.getncattr("sensing_end_time")))
        if valid is None:
            raise ValueError(f"LI granule {path} carries no usable valid time")

        lat_var = _find_variable(dataset, _LAT_NAMES)
        lon_var = _find_variable(dataset, _LON_NAMES)
        if lat_var is None or lon_var is None:
            raise KeyError(
                f"LI granule {path} has no recognised flash position variables "
                f"(looked for {_LAT_NAMES} / {_LON_NAMES})"
            )
        lat_var.set_auto_maskandscale(True)
        lon_var.set_auto_maskandscale(True)
        lats = np.ma.filled(np.ma.asarray(lat_var[:]).ravel().astype(float), np.nan)
        lons = np.ma.filled(np.ma.asarray(lon_var[:]).ravel().astype(float), np.nan)
        acquisition = acquisition_metadata(dataset)
        start = _parse_iso(acquisition["acquisition_start"]) if acquisition["acquisition_start"] else None
        end = _parse_iso(acquisition["acquisition_end"]) if acquisition["acquisition_end"] else None
        events, reasons = _precise_flash_times(_find_variable(dataset, _TIME_NAMES), lats.size, start, end)
        times = _flash_times(_find_variable(dataset, _TIME_NAMES), lats.size, valid)
        attribution = _attribution(dataset)

    if lats.size != lons.size:
        raise ValueError("LI latitude/longitude array lengths differ")
    keep = np.isfinite(lats) & np.isfinite(lons) & (np.abs(lats) <= 90) & (np.abs(lons) <= 180)
    if times.size != lats.size:
        times = np.full(lats.size, np.datetime64(valid.replace(tzinfo=None), "s"))

    return FlashFrame(
        source=source,
        valid_time=valid.astimezone(timezone.utc),
        window_minutes=window_minutes,
        lats=lats[keep],
        lons=lons[keep],
        times=times[keep],
        attribution=attribution,
        time_precision=tuple("individual_time" if events[i] is not None else "window_only" for i in np.flatnonzero(keep)),
        time_reason_codes=tuple(reasons[i] for i in np.flatnonzero(keep)),
        event_times=tuple(events[i] for i in np.flatnonzero(keep)),
        acquisition_start=start,
        acquisition_end=end,
        sample_ids=tuple(int(i) for i in np.flatnonzero(keep)),
    )
