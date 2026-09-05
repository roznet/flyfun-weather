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

**One retrieved cloud top per pixel.** Histograms over the sampling disc
describe the spatial distribution of retrieved tops. They do not establish
cloud bases or prove a vertical multilayer stack.
:class:`~weatherbrief.models.observed.ObservedTopsAnnulus` carries height bins
and a full ``quality_method`` breakdown rather than one number.

``quality_method == 0`` means not processed, either cloud free OR missing /
corrupt data. Only the separate cloud-free ``quality_status`` supports
``undetect``. Off-disc, failed and unknown retrievals remain ``nodata``.
The FCI CLM/CT/CTTH user guide, Table 10, defines these categorical flags;
the method is neither a confidence score nor a multilayer flag.
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
# This is the 50°N-on-the-sub-satellite-meridian figure and the floor for
# `parallax_pad_km`, which scales it with the viewing geometry.  Use that
# function rather than this constant for any window that has to reach real
# displacements; the constant alone is only safe near the reference point.
PARALLAX_PAD_KM = 75.0

# Longitude the satellite sits over.  MTG-I1 is at 0°, matching the granule's
# own `mtg_geos_projection.longitude_of_projection_origin`.
SUB_SATELLITE_LON = 0.0

# Reference sub-satellite angle the 75 km figure was measured at (a route near
# 50°N and only a few degrees off the 0° meridian, so the angle ≈ the latitude).
_PARALLAX_REFERENCE_ANGLE = 50.0
# Beyond this the viewing geometry degenerates (the limb), displacement grows
# without bound and the retrieval is unusable anyway — clamp rather than pad a
# window to the size of a continent.
_PARALLAX_MAX_ANGLE = 70.0

_EARTH_RADIUS_KM = 6371.0
_GEO_ORBIT_RADIUS_KM = 42157.0


def sub_satellite_angle_deg(lat_deg: float, lon_deg: float) -> float:
    """Great-circle angle from the sub-satellite point to (lat, lon).

    This — not latitude — is what the satellite zenith angle depends on.  A
    point due south of the satellite at 50°N is 50° away; the same latitude at
    25°E is 53.6° away, and is viewed more obliquely.  Using latitude alone
    under-reads the geometry everywhere off the sub-satellite meridian, which
    is most of Europe.
    """
    phi = math.radians(lat_deg)
    dlon = math.radians(lon_deg - SUB_SATELLITE_LON)
    cos_psi = math.cos(phi) * math.cos(dlon)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_psi))))


def _satellite_zenith_tangent(sub_satellite_angle: float) -> float:
    """``tan`` of the satellite zenith angle at a given sub-satellite angle.

    Not used to compute the displacement — the CTTH design notes record that
    the naive ``h × tan(zenith)`` formula *underestimates* the real dlat by
    roughly a factor of four, which is why the product ships a per-pixel
    correction field at all.  It is used only for its *ratio* between two
    geometries, to scale an empirically-measured displacement to a viewing
    angle it was not measured at.
    """
    psi = math.radians(min(abs(sub_satellite_angle), _PARALLAX_MAX_ANGLE))
    numerator = _GEO_ORBIT_RADIUS_KM * math.sin(psi)
    denominator = _GEO_ORBIT_RADIUS_KM * math.cos(psi) - _EARTH_RADIUS_KM
    if denominator <= 0:
        return math.tan(math.radians(89.0))
    return numerator / denominator


def parallax_pad_km(lats, lons) -> float:
    """Window pad (km) big enough to reach the displaced pixels anywhere here.

    The 75 km constant was measured at 50°N near the 0° meridian.  Displacement
    grows with the satellite zenith angle, which depends on angular distance
    from the sub-satellite point in **both** latitude and longitude — a
    latitude-only scaling under-pads everywhere east or west of the meridian
    (Warsaw by ~11 km, Riga ~18, Helsinki ~23), silently truncating exactly the
    high-cloud tail this pad exists to reach.

    Takes the worst point in the set and scales the measured figure by the
    zenith-tangent ratio, never returning less than it.
    """
    angles = [sub_satellite_angle_deg(la, lo) for la, lo in zip(lats, lons)]
    if not angles:
        return PARALLAX_PAD_KM
    reference = _satellite_zenith_tangent(_PARALLAX_REFERENCE_ANGLE)
    at_worst = _satellite_zenith_tangent(max(angles))
    return max(PARALLAX_PAD_KM, PARALLAX_PAD_KM * at_worst / reference)

# FCI CLM/CT/CTTH user guide, Table 10 (not the former empirical table):
# https://user.eumetsat.int/resources/user-guides/mtg-fci-clm-ct-and-ctth-data-guide
QUALITY_METHOD_LABELS: dict[int, str] = {
    0: "not processed (no/corrupt data or cloud free)",
    1: "opaque and RTM",
    2: "opaque minus RTM",
    3: "intercept IR10.5 / IR13.4",
    4: "intercept IR10.5 / IR6.3",
    5: "intercept IR10.5 / IR7.3",
    6: "radiance ratio IR10.5 / IR13.4",
    7: "radiance ratio IR10.5 / IR6.3",
    8: "radiance ratio IR10.5 / IR7.3",
    9: "opaque + RTM + inversion",
    10: "no solution",
}

DEFAULT_LICENSE = "© EUMETSAT — MTG FCI Level 2 Cloud Top Height"

FEET_PER_METRE = 1.0 / 0.3048


def metres_to_fl(height_m):
    """Geometric metres → hundreds of geometric feet MSL (legacy wire units).

    Deliberately geometric, not pressure altitude.  The product also ships
    ``cloud_top_aviation_height``, the pressure-based quantity a pilot's
    altimeter agrees with.  Its ``FL/10`` units are now **confirmed** against a
    real granule (123k cloudy pixels, 2026-08-26): geometric tops span
    30–44,200 ft and ``aviation × 10`` spans pressure FL10–440, correlation 0.83.

    It is carried alongside rather than instead of this, because it is coarse
    (``int8``, so 10 FL steps) and diverges from the geometric height by a
    median +15 FL with p90 +91 FL.  Two answers to two different questions:
    the histogram bins are NOT flight levels; the pressure figure is reported
    separately for comparison with standard-pressure altimetry.
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


def acquisition_metadata(dataset) -> dict:
    """Actual documented interval only; never infer it from a filename/cadence."""
    result = {}
    for key, names in (
        ("acquisition_start", ("sensing_start_time", "start_time", "time_coverage_start")),
        ("acquisition_end", ("sensing_end_time", "end_time", "time_coverage_end")),
    ):
        value = next((str(dataset.getncattr(n)) for n in names if n in dataset.ncattrs()), None)
        parsed = _parse_iso(value) if value is not None else None
        result[key] = parsed.isoformat() if parsed is not None else None
    return result


def read_metadata(path: Path | str) -> dict:
    """Sidecar metadata for one granule, without reading the 5568² arrays."""
    import netCDF4

    path = Path(path)
    with netCDF4.Dataset(str(path)) as dataset:
        grid = read_grid(dataset)
        valid = _valid_time(dataset, path)
        return {
            "quantity": "cloud_top_height",
            **acquisition_metadata(dataset),
            "product_id": CTTH_COLLECTION + ":" + str(getattr(dataset, "product_version", "unspecified")),
            "decoder_version": "ctth_supplied_parallax_v1",
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
    """Decode a window, opening this granule once."""
    import netCDF4

    with netCDF4.Dataset(str(path)) as dataset:
        return read_dataset_window(dataset, window, source=source, path=Path(path))


def read_dataset_window(dataset, window: GridWindow, *, source: str, path: Path | str) -> GridFrame:
    """Decode a bounded block from a caller-owned, already open granule."""
    path = Path(path)
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
    dlat, dlat_missing = _read_raw(dataset, "delta_latitude", rows, cols)
    dlon, dlon_missing = _read_raw(dataset, "delta_longitude", rows, cols)
    # Three more variables over the SAME rows. The granule chunks are
    # [23, 5568] full-width strips, so the expensive part — seeking and
    # decompressing that row band — is already paid; each extra variable
    # measured ~6 ms on a route-sized window, about 10% of a payload build.
    #
    # Effective cloudiness is pixel cloud amount × emissivity at 10.5 µm,
    # not visual opacity, a cloud-cover fraction, or overflight guidance.
    # Preserve netCDF packing: measured granules decode to fractions,
    # despite the guide's percent label. That discrepancy is unresolved;
    # applying another /100 or masking values would guess an encoding.
    # Optional: a granule that does not carry one of these still decodes,
    # and the sampler reports the corresponding field as None. Height,
    # quality and the parallax pair are the only hard requirements —
    # without those the product cannot be placed or believed at all.
    optional = {
        name: _read_raw(dataset, name, rows, cols)
        for name in (
            "cloud_top_temperature",
            "effective_cloudiness",
            "cloud_top_aviation_height",
        )
        if name in dataset.variables
    }

    # Table 10 spells status "qualiy_status"; accept that spelling as
    # well as the conventional variable name. netCDF enum types decode
    # through _read_raw just like integer flags, including their fill.
    status_name = next(
        (name for name in ("quality_status", "qualiy_status") if name in dataset.variables),
        None,
    )
    status = _read_raw(dataset, status_name, rows, cols)[0] if status_name else None
    overall = (
        _read_raw(dataset, "quality_overall_processing", rows, cols)[0]
        if "quality_overall_processing" in dataset.variables else None
    )

    valid = _valid_time(dataset, path)
    attribution = _attribution(dataset)

    if valid is None:
        raise ValueError(f"CTTH granule {path} carries no usable valid time")

    # Method 0 conflates cloud-free and unprocessed; method 10 is no solution.
    # Require independent cloud-free status, with no contradictory height.
    clear = np.zeros(height.shape, dtype=bool)
    if status is not None:
        clear = (status == 1) & (quality == 0) & ~np.isfinite(height)
        if overall is not None:
            clear &= overall == 0
    detected = np.isfinite(height) & ~height_missing & np.isin(quality, range(1, 10))
    if status is not None:
        # Status 3 alone means CLOUDY and successful; dust/ash success (5/7)
        # is not a cloud top. Missing/unknown flags must not imply success.
        detected &= status == 3
    # Legacy granules lacking status may still supply positive evidence with
    # a valid method/height, but can never assert clear. Poor quality remains
    # a retrieval, not "no data"; no calibrated confidence threshold is added.
    if overall is not None:
        detected &= np.isin(overall, (1, 2))
    # A cloud top we cannot place is not a usable cloud top.  Parallax is not
    # a refinement on this product — the uncorrected position is tens of km
    # from the cloud — so a detection whose correction is missing would be
    # drawn and sampled at a location it is not at.  Demote it to `nodata`:
    # the retrieval did not give us an answer we can put on a map.  (Fill
    # values are already NaN by this point; this covers the pixel where the
    # height retrieval succeeded but the geometry correction did not.)
    unplaceable = detected & (dlat_missing | dlon_missing | np.isnan(dlat) | np.isnan(dlon))
    detected = detected & ~unplaceable
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
            **({"quality_status": status.astype(np.float32)} if status is not None else {}),
            **({"quality_overall_processing": overall.astype(np.float32)} if overall is not None else {}),
            "delta_latitude": np.nan_to_num(dlat, nan=0.0).astype(np.float32),
            "delta_longitude": np.nan_to_num(dlon, nan=0.0).astype(np.float32),
            # NaN where absent rather than a sentinel: these are sampled with
            # nan-aware reductions, and a -1 would quietly drag a mean down.
            # `cloud_top_aviation_height` is the pressure-based flight level an
            # altimeter agrees with, unlike our geometric metres, in FL/10.
            **{
                name: np.where(miss, np.nan, vals).astype(np.float32)
                for name, (vals, miss) in optional.items()
            },
        },
    )
