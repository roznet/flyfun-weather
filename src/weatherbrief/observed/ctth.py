"""MTG FCI L2 Cloud Top Temperature & Height reader (EO:EUM:DAT:0681).

Two things about this product drive the whole design.

**Parallax is not a refinement.**  The satellite sits over the equator, so its
line of sight to a cloud at 50°N continues past the cloud and strikes the
ground *north* of it.  The pixel that contains a cloud-top therefore claims a
ground position tens of kilometres from where the cloud actually is: the
measured median displacement is **52 km** against a **37 km** corridor.  A
sample taken at the nominal pixel is not a slightly-wrong sample — it is a
sample of a different place.  The product ships per-pixel ``delta_latitude`` /
``delta_longitude`` giving the correction, and this module applies it *before*
anything decides which pixels belong to a station.  ``tests/observed`` pins
this with a fixture that fails if the correction is dropped.

**One cloud top per pixel.**  For a cirrus-over-stratus stack the retrieval
commits to whichever layer wins, and adjacent 2 km pixels flip between the
two as opacity wobbles.  A single-pixel sample gets an arbitrary slice of
that; only the histogram over an annulus recovers the structure, which is why
:class:`~weatherbrief.models.observed.ObservedTopsAnnulus` carries FL bins and
a full ``quality_method`` breakdown rather than one number.

``quality_method == 0`` means *no cloud* — a positive observation, and mapped
to ``undetect``, not to ``nodata``.  Off-disc and failed retrievals are
``nodata``.  See ``designs/future/satellite-cloud-top-validation.md`` for the
empirical method-code table this reading rests on.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from weatherbrief.models.observed import ObservedAttribution

from .frames import GridFrame
from .grid import GridSpec, GridWindow

logger = logging.getLogger(__name__)

CTTH_COLLECTION = "EO:EUM:DAT:0681"
LI_COLLECTION = "EO:EUM:DAT:0691"

# Widest parallax displacement the product produces at the latitude the CTTH
# investigation measured: dlat reaches ~-0.65° for FL400 cirrus at 50°N, i.e.
# ~72 km.  The sampler pads its read window by this much so the pixels whose
# *corrected* position lands on a station are actually inside the block that
# was read.  A pad smaller than the displacement silently truncates the
# high-cloud tail — the exact signal "can I get on top?" depends on, and it
# fails with no error, just missing cloud.
#
# This is the 50°N figure and the floor for `parallax_pad_km`, which scales it
# with latitude.  Use that function rather than this constant for any window
# that has to reach real displacements; the constant alone is only safe for a
# route no further north than the reference.
PARALLAX_PAD_KM = 75.0

# Reference latitude the 75 km figure was measured at.
_PARALLAX_REFERENCE_LAT = 50.0
# Beyond this the viewing geometry degenerates (the limb), displacement grows
# without bound and the retrieval is unusable anyway — clamp rather than pad a
# window to the size of a continent.
_PARALLAX_MAX_LAT = 70.0

_EARTH_RADIUS_KM = 6371.0
_GEO_ORBIT_RADIUS_KM = 42157.0


def _satellite_zenith_tangent(latitude_deg: float) -> float:
    """``tan`` of the satellite zenith angle at a given latitude.

    Not used to compute the displacement — the CTTH design notes record that
    the naive ``h × tan(zenith)`` formula *underestimates* the real dlat by
    roughly a factor of four, which is why the product ships a per-pixel
    correction field at all.  It is used only for its *ratio* between two
    latitudes, to scale an empirically-measured displacement to a latitude it
    was not measured at.
    """
    psi = math.radians(min(abs(latitude_deg), _PARALLAX_MAX_LAT))
    numerator = _GEO_ORBIT_RADIUS_KM * math.sin(psi)
    denominator = _GEO_ORBIT_RADIUS_KM * math.cos(psi) - _EARTH_RADIUS_KM
    if denominator <= 0:
        return math.tan(math.radians(89.0))
    return numerator / denominator


def parallax_pad_km(max_abs_latitude_deg: float) -> float:
    """Window pad (km) big enough to reach the displaced pixels at this latitude.

    The 75 km constant was measured at 50°N.  Displacement grows with the
    satellite zenith angle and so with latitude: by 65°N the same FL400 cirrus
    is thrown more than twice as far, and a route into Scandinavia padded to
    75 km would quietly lose its high cloud.  Scales the measured figure by the
    zenith-tangent ratio and never returns less than it.
    """
    reference = _satellite_zenith_tangent(_PARALLAX_REFERENCE_LAT)
    at_latitude = _satellite_zenith_tangent(max_abs_latitude_deg)
    return max(PARALLAX_PAD_KM, PARALLAX_PAD_KM * at_latitude / reference)

# Empirically-verified FCI L2 height-assignment methods (full-disc sample,
# 2026-05-04 08:00Z).  Kept as labels so the UI need not embed the table.
QUALITY_METHOD_LABELS: dict[int, str] = {
    0: "no cloud",
    1: "opaque IR window (low stratus/fog)",
    6: "opaque IR, cold cloud (Cb/thick cirrus)",
    7: "radiance ratio, thin cirrus",
    8: "CO2 slicing, semi-transparent high",
    9: "multi-layer suspect",
    10: "other semi-transparent",
}

DEFAULT_LICENSE = "© EUMETSAT — MTG FCI Level 2 Cloud Top Height"

FEET_PER_METRE = 1.0 / 0.3048


def metres_to_fl(height_m):
    """Geometric height in metres → flight level.

    Deliberately geometric, not pressure altitude.  The product also ships
    ``cloud_top_aviation_height``, which is the pressure-based quantity a
    pilot's altimeter would agree with, but its documented units (``FL/10``)
    have not been verified against a real granule; adopting it is a phase-2
    change once that is checked.  Until then the histogram bins are honest
    geometric heights labelled FL, matching the analysis scripts this reader
    descends from.
    """
    return np.asarray(height_m, dtype=float) * FEET_PER_METRE / 100.0


def _read_raw(dataset, name: str, rows: slice, cols: slice):
    """Read a slice with fill/scale handling done explicitly.

    netCDF4's automatic masking would hand back a masked array whose fill
    semantics we then have to unpick anyway; doing it here keeps "this pixel
    has no value" a single, visible decision.
    """
    var = dataset.variables[name]
    var.set_auto_maskandscale(False)
    raw = np.asarray(var[rows, cols])

    fill = None
    for attr in ("_FillValue", "missing_value"):
        if attr in var.ncattrs():
            fill = var.getncattr(attr)
            break
    missing = (
        np.isclose(raw.astype(np.float64), float(np.asarray(fill).ravel()[0]))
        if fill is not None
        else np.zeros(raw.shape, dtype=bool)
    )

    values = raw.astype(np.float64)
    if "scale_factor" in var.ncattrs():
        values = values * float(var.getncattr("scale_factor"))
    if "add_offset" in var.ncattrs():
        values = values + float(var.getncattr("add_offset"))
    values[missing] = np.nan
    return values, missing


def _projection_attrs(dataset):
    for name in ("mtg_geos_projection", "geostationary", "projection"):
        if name in dataset.variables:
            return dataset.variables[name]
    raise KeyError("CTTH granule has no geostationary projection variable")


def read_grid(dataset) -> GridSpec:
    """Build the geostationary :class:`GridSpec` from an open granule.

    The netCDF stores ``x``/``y`` as scan-angle radians; multiplying by the
    perspective-point height gives the projected metres the proj4 definition
    expects.  Doing that here means the sampler never learns that this grid is
    angular while OPERA's is metric.
    """
    proj = _projection_attrs(dataset)
    height = float(np.asarray(proj.getncattr("perspective_point_height")).ravel()[0])
    semi_major = float(np.asarray(proj.getncattr("semi_major_axis")).ravel()[0])
    semi_minor = float(np.asarray(proj.getncattr("semi_minor_axis")).ravel()[0])
    sweep = "y"
    if "sweep_angle_axis" in proj.ncattrs():
        sweep = str(proj.getncattr("sweep_angle_axis"))
    lon_0 = 0.0
    if "longitude_of_projection_origin" in proj.ncattrs():
        lon_0 = float(
            np.asarray(proj.getncattr("longitude_of_projection_origin")).ravel()[0]
        )

    proj4 = (
        f"+proj=geos +lon_0={lon_0} +h={height} +a={semi_major} +b={semi_minor} "
        f"+sweep={sweep} +units=m +no_defs"
    )

    x = np.asarray(dataset.variables["x"][:], dtype=float) * height
    y = np.asarray(dataset.variables["y"][:], dtype=float) * height
    return GridSpec(
        proj4=proj4,
        nx=int(x.size),
        ny=int(y.size),
        x0=float(x[0]),
        y0=float(y[0]),
        dx=float(x[1] - x[0]),
        dy=float(y[1] - y[0]),
    )


def _valid_time(dataset, path: Path) -> datetime | None:
    for attr in ("sensing_end_time", "end_time", "time_coverage_end", "sensing_start_time"):
        if attr in dataset.ncattrs():
            parsed = _parse_iso(str(dataset.getncattr(attr)))
            if parsed:
                return parsed
    # Fall back to the 14-digit stamp EUMETSAT embeds in the product filename.
    for part in path.stem.split("_"):
        if len(part) == 14 and part.isdigit():
            try:
                return datetime.strptime(part, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _parse_iso(text: str) -> datetime | None:
    text = text.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            parsed = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _attribution(dataset) -> ObservedAttribution:
    producer = None
    license_text = None
    url = None
    attrs = set(dataset.ncattrs())
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
    text = " · ".join(p for p in (producer, license_text) if p)
    return ObservedAttribution(producer=producer, license=license_text, url=url, text=text)


def read_metadata(path: Path | str) -> dict:
    """Sidecar metadata for one granule, without reading the 5568² arrays."""
    import netCDF4

    path = Path(path)
    with netCDF4.Dataset(str(path)) as dataset:
        grid = read_grid(dataset)
        valid = _valid_time(dataset, path)
        return {
            "quantity": "cloud_top_height",
            "valid_time": valid.isoformat() if valid else None,
            "window_minutes": 0.0,
            "grid": {
                "proj4": grid.proj4,
                "nx": grid.nx,
                "ny": grid.ny,
                "x0": grid.x0,
                "y0": grid.y0,
                "dx": grid.dx,
                "dy": grid.dy,
            },
            "attribution": _attribution(dataset).model_dump(),
        }


def read_window(path: Path | str, window: GridWindow, *, source: str) -> GridFrame:
    """Decode one row-strip of a granule, keeping the parallax fields.

    Only the *row* range narrows the read: the granule's chunks are
    ``[23, 5568]`` full-width strips, so trimming columns costs a partial-chunk
    decompression and saves nothing.  ``window.full_width`` records that the
    caller knows this.
    """
    import netCDF4

    path = Path(path)
    with netCDF4.Dataset(str(path)) as dataset:
        grid = read_grid(dataset)
        row0 = max(0, min(window.row0, grid.ny))
        row1 = max(row0, min(window.row1, grid.ny))
        if window.full_width:
            col0, col1 = 0, grid.nx
        else:
            col0 = max(0, min(window.col0, grid.nx))
            col1 = max(col0, min(window.col1, grid.nx))
        rows = slice(row0, row1)
        cols = slice(col0, col1)

        height, height_missing = _read_raw(dataset, "cloud_top_height", rows, cols)
        quality, quality_missing = _read_raw(dataset, "quality_method", rows, cols)
        dlat, _ = _read_raw(dataset, "delta_latitude", rows, cols)
        dlon, _ = _read_raw(dataset, "delta_longitude", rows, cols)

        valid = _valid_time(dataset, path)
        attribution = _attribution(dataset)

    if valid is None:
        raise ValueError(f"CTTH granule {path} carries no usable valid time")

    # quality_method == 0 is a *positive* observation of clear sky, so it is
    # `undetect`.  Anything with no usable method code at all — off-disc,
    # failed retrieval — is `nodata`: the retrieval did not answer.
    clear = np.isclose(quality, 0.0) & ~quality_missing
    detected = ~np.isnan(height) & ~height_missing & ~clear
    nodata = ~clear & ~detected
    undetect = clear

    values = np.where(detected, height, np.nan)

    return GridFrame(
        source=source,
        quantity="cloud_top_height",
        units="m",
        valid_time=valid,
        window_minutes=0.0,
        grid=grid,
        window=GridWindow(row0, row1, col0, col1, full_width=window.full_width),
        values=values.astype(np.float32),
        nodata=nodata,
        undetect=undetect,
        attribution=attribution,
        aux={
            "quality_method": np.where(quality_missing, -1, quality).astype(np.int16),
            "delta_latitude": np.nan_to_num(dlat, nan=0.0).astype(np.float32),
            "delta_longitude": np.nan_to_num(dlon, nan=0.0).astype(np.float32),
        },
    )
