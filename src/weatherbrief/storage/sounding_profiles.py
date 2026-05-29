"""Shaped sounding-profile models, builder, and the gzipped pack sidecar.

This module is deliberately neutral: both ``api/packs.py`` (HTTP endpoints) and
``tasks/artifacts.py`` (refresh-time persistence) import from here, so it must
not import from either of them (no ``tasks → api`` cycle).

The *sidecar* (``sounding_profiles.json.gz``) is written at refresh time from
the in-memory route-analyses manifest — which still has ``derived_levels``
intact, before they are stripped from ``route_analyses.json`` for the online
viewer. Endpoints read the sidecar instead of recomputing the MetPy sounding
analysis for every (point × model). When the sidecar is absent (old packs, or
after T1 retention), callers fall back to :func:`_build_sounding_profile`,
which recomputes on the fly — so nothing ever hard-depends on the sidecar.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime
from math import exp
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Gzipped sidecar holding pre-shaped sounding profiles for every (point, model).
SIDECAR_FILENAME = "sounding_profiles.json.gz"


class SoundingProfileLevel(BaseModel):
    """A single pressure level in a sounding profile."""
    pressure_hpa: int
    altitude_ft: float | None = None
    temperature_c: float
    dewpoint_c: float | None = None
    wind_speed_kt: float | None = None
    wind_direction_deg: float | None = None
    # Extended DerivedLevel fields for side panels
    relative_humidity_pct: float | None = None
    dewpoint_depression_c: float | None = None
    wet_bulb_c: float | None = None
    theta_e_k: float | None = None
    lapse_rate_c_per_km: float | None = None
    icing_index: float | None = None
    icing_index_nwp: float | None = None
    sfip_100: float | None = None
    cloud_liquid_water_g_m3: float | None = None
    ice_mixing_ratio_g_kg: float | None = None
    cloud_area_fraction_pct: float | None = None
    richardson_number: float | None = None
    omega_pa_s: float | None = None
    w_fpm: float | None = None


class ParcelPathPointResponse(BaseModel):
    """A single point on the parcel path for CAPE/CIN shading."""
    pressure_hpa: float
    temperature_c: float


class SoundingProfileResponse(BaseModel):
    """Sounding profile data for client-side Skew-T rendering (web + iOS)."""
    point_index: int
    lat: float
    lon: float
    distance_from_origin_nm: float
    waypoint_icao: str | None = None
    model: str
    time: str
    levels: list[SoundingProfileLevel]
    cruise_altitude_ft: int | None = None
    track_deg: float | None = None
    label: str | None = None
    # Thermodynamic indices
    indices: dict | None = None
    # Parcel path for CAPE/CIN shading
    parcel_path: list[ParcelPathPointResponse] = Field(default_factory=list)
    # Overlay data from sounding analysis
    cloud_layers: list[dict] = Field(default_factory=list)
    nwp_cloud_layers: list[dict] = Field(default_factory=list)
    icing_zones: list[dict] = Field(default_factory=list)
    icing_ogimet_nwp_zones: list[dict] = Field(default_factory=list)
    sfip_zones: list[dict] = Field(default_factory=list)
    inversion_layers: list[dict] = Field(default_factory=list)
    convective: dict | None = None


def _build_sounding_profile(
    ra_data: dict, cs_data: dict, point_index: int, model: str,
) -> SoundingProfileResponse | None:
    """Build a SoundingProfileResponse from route analyses and cross-section data.

    Returns None if the point/model combination has no usable data.

    When ``ra_data`` still carries ``derived_levels`` (the in-memory manifest at
    refresh time), no recompute happens. When they have been stripped (the
    on-disk ``route_analyses.json``), this runs ``analyze_sounding`` on the fly.
    """
    from weatherbrief.models import WaypointForecast

    analyses = ra_data.get("analyses", [])
    point_data = next((a for a in analyses if a["point_index"] == point_index), None)
    if point_data is None:
        return None

    cross_sections = cs_data.get("cross_sections", [])
    cs_match = next((cs for cs in cross_sections if cs["model"] == model), None)
    if cs_match is None or point_index >= len(cs_match["point_forecasts"]):
        return None

    wf = WaypointForecast.model_validate(cs_match["point_forecasts"][point_index])
    interp_time = datetime.fromisoformat(point_data["interpolated_time"])
    hourly = wf.at_time(interp_time)
    if not hourly or not hourly.pressure_levels:
        return None

    sounding_data = point_data.get("sounding", {}).get(model, {})

    # Build a lookup from derived_levels for enriching profile levels.
    # derived_levels are excluded from route_analyses.json to save space,
    # so we run analyze_sounding on-the-fly to get the full set.
    # The result also provides parcel_path as fallback for old packs.
    derived_by_pressure: dict[int, dict] = {}
    onthefly_result = None
    stored_dls = sounding_data.get("derived_levels", [])
    if stored_dls:
        for dl in stored_dls:
            derived_by_pressure[dl.get("pressure_hpa", 0)] = dl
    else:
        # On-the-fly sounding analysis (~50-200ms) for icing indices, Ri, etc.
        try:
            from weatherbrief.analysis.sounding import analyze_sounding
            onthefly_result = analyze_sounding(hourly.pressure_levels, hourly)
            if onthefly_result and onthefly_result.derived_levels:
                for dl in onthefly_result.derived_levels:
                    derived_by_pressure[dl.pressure_hpa] = dl.model_dump()
        except Exception:
            logger.debug("On-the-fly sounding analysis failed", exc_info=True)

    sorted_levels = sorted(hourly.pressure_levels, key=lambda x: x.pressure_hpa, reverse=True)
    levels = []
    prev_alt_ft: float | None = None
    prev_temp_c: float | None = None
    for pl in sorted_levels:
        if pl.temperature_c is None:
            continue
        alt_ft = pl.geopotential_height_m * 3.28084 if pl.geopotential_height_m is not None else None
        dl = derived_by_pressure.get(pl.pressure_hpa, {})

        # Compute basic derived values inline when not available from stored analysis
        dd = dl.get("dewpoint_depression_c")
        rh = dl.get("relative_humidity_pct")
        lapse = dl.get("lapse_rate_c_per_km")
        if dd is None and pl.dewpoint_c is not None:
            dd = round(pl.temperature_c - pl.dewpoint_c, 1)
        if rh is None and pl.relative_humidity_pct is not None:
            rh = pl.relative_humidity_pct
        elif rh is None and pl.dewpoint_c is not None:
            # Magnus formula: RH ≈ 100 × exp(17.67×Td/(Td+243.5) - 17.67×T/(T+243.5))
            try:
                rh = round(100.0 * exp(
                    17.67 * pl.dewpoint_c / (pl.dewpoint_c + 243.5)
                    - 17.67 * pl.temperature_c / (pl.temperature_c + 243.5)
                ), 1)
            except Exception:
                pass
        if lapse is None and prev_alt_ft is not None and alt_ft is not None and prev_temp_c is not None:
            dz_km = (alt_ft - prev_alt_ft) / 3280.84
            if abs(dz_km) > 0.01:
                lapse = round((prev_temp_c - pl.temperature_c) / dz_km, 1)

        # θe: Bolton (1980) approximation
        theta_e = dl.get("theta_e_k")
        if theta_e is None and pl.dewpoint_c is not None:
            try:
                T_K = pl.temperature_c + 273.15
                # Mixing ratio from dewpoint
                e = 6.112 * exp(17.67 * pl.dewpoint_c / (pl.dewpoint_c + 243.5))
                r = 0.622 * e / (pl.pressure_hpa - e)  # kg/kg
                # Potential temperature
                theta = T_K * (1000.0 / pl.pressure_hpa) ** 0.2854
                # θe ≈ θ × exp(Lv × r / (cp × T))
                theta_e = round(theta * exp(2.501e6 * r / (1004.0 * T_K)), 1)
            except Exception:
                pass

        # Omega / vertical velocity from raw pressure level data
        omega = dl.get("omega_pa_s")
        w_fpm = dl.get("w_fpm")
        if omega is None and pl.vertical_velocity_pa_s is not None:
            omega = round(pl.vertical_velocity_pa_s, 3)
            # Convert omega (Pa/s) to w (ft/min): w ≈ -omega / (ρg) in m/s → ft/min
            # Using standard density approximation: ρ ≈ p/(Rd×T)
            if pl.temperature_c is not None:
                T_K = pl.temperature_c + 273.15
                rho = (pl.pressure_hpa * 100) / (287.05 * T_K)
                w_ms = -pl.vertical_velocity_pa_s / (rho * 9.81)
                w_fpm = round(w_ms * 196.85, 1)  # m/s → ft/min

        # CLW from GRIB (already in kg/kg on PressureLevelData)
        clw = dl.get("cloud_liquid_water_g_m3")
        if clw is None and pl.cloud_liquid_water_kg_kg is not None and pl.temperature_c is not None:
            T_K = pl.temperature_c + 273.15
            rho = (pl.pressure_hpa * 100) / (287.05 * T_K)
            clw = round(pl.cloud_liquid_water_kg_kg * rho * 1000, 4)  # kg/kg → g/m³

        # ICE mixing ratio from GRIB
        ice = dl.get("ice_mixing_ratio_g_kg")
        if ice is None and pl.ice_mixing_ratio_kg_kg is not None:
            ice = round(pl.ice_mixing_ratio_kg_kg * 1000, 4)  # kg/kg → g/kg

        levels.append(SoundingProfileLevel(
            pressure_hpa=pl.pressure_hpa,
            altitude_ft=alt_ft if alt_ft is not None else dl.get("altitude_ft"),
            temperature_c=pl.temperature_c,
            dewpoint_c=pl.dewpoint_c,
            wind_speed_kt=pl.wind_speed_kt,
            wind_direction_deg=pl.wind_direction_deg,
            relative_humidity_pct=rh,
            dewpoint_depression_c=dd,
            wet_bulb_c=dl.get("wet_bulb_c"),
            theta_e_k=theta_e,
            lapse_rate_c_per_km=lapse,
            icing_index=dl.get("icing_index"),
            icing_index_nwp=dl.get("icing_index_nwp"),
            sfip_100=dl.get("sfip_100"),
            cloud_liquid_water_g_m3=clw,
            ice_mixing_ratio_g_kg=ice,
            cloud_area_fraction_pct=pl.cloud_area_fraction_pct,
            richardson_number=dl.get("richardson_number"),
            omega_pa_s=omega,
            w_fpm=w_fpm,
        ))
        prev_alt_ft = alt_ft
        prev_temp_c = pl.temperature_c

    # Parcel path — from stored data, or fall back to on-the-fly analysis
    stored_parcel = sounding_data.get("parcel_path", [])
    if not stored_parcel and onthefly_result and onthefly_result.parcel_path:
        stored_parcel = [pp.model_dump() for pp in onthefly_result.parcel_path]
    parcel_path = [
        ParcelPathPointResponse(pressure_hpa=pp["pressure_hpa"], temperature_c=pp["temperature_c"])
        for pp in stored_parcel
    ]

    # Label: waypoint ICAO or name
    label = point_data.get("waypoint_icao") or point_data.get("waypoint_name")

    return SoundingProfileResponse(
        point_index=point_index,
        lat=point_data["lat"],
        lon=point_data["lon"],
        distance_from_origin_nm=point_data["distance_from_origin_nm"],
        waypoint_icao=point_data.get("waypoint_icao"),
        model=model,
        time=point_data["interpolated_time"],
        levels=levels,
        cruise_altitude_ft=ra_data.get("cruise_altitude_ft"),
        track_deg=point_data.get("track_deg"),
        label=label,
        indices=sounding_data.get("indices"),
        parcel_path=parcel_path,
        cloud_layers=sounding_data.get("cloud_layers", []),
        nwp_cloud_layers=sounding_data.get("nwp_cloud_layers", []) or [],
        icing_zones=sounding_data.get("icing_zones", []),
        icing_ogimet_nwp_zones=sounding_data.get("icing_ogimet_nwp_zones", []),
        sfip_zones=sounding_data.get("sfip_zones", []),
        inversion_layers=sounding_data.get("inversion_layers", []),
        convective=sounding_data.get("convective"),
    )


# ---------------------------------------------------------------------------
# Sidecar build / read
# ---------------------------------------------------------------------------


def _sounding_key(point_index: int, model: str) -> str:
    """Bundle/sidecar key for one (point, model) sounding profile."""
    return f"sounding-{point_index}-{model}"


def build_sounding_sidecar(ra_data: dict, cs_data: dict) -> dict[str, dict]:
    """Build the ``{ "sounding-{pt}-{model}": <profile dict>, ... }`` mapping.

    ``ra_data`` must be the *full* route-analyses dict (``derived_levels``
    present) so :func:`_build_sounding_profile` does **not** recompute. Keys and
    value shape are exactly what ``get_bundle`` produces today.
    """
    out: dict[str, dict] = {}
    models = ra_data.get("models", [])
    analyses = ra_data.get("analyses", [])
    for point_data in analyses:
        point_index = point_data["point_index"]
        for model_name in models:
            profile = _build_sounding_profile(ra_data, cs_data, point_index, model_name)
            if profile:
                out[_sounding_key(point_index, model_name)] = profile.model_dump(mode="json")
    return out


def write_sounding_sidecar(pack_dir: Path, ra_data: dict, cs_data: dict) -> int:
    """Build and gzip-write the sidecar to *pack_dir*.

    Returns the number of profiles written (0 if there was nothing to write, in
    which case no file is created). Built from the in-memory manifest — no added
    MetPy recompute.
    """
    sidecar = build_sounding_sidecar(ra_data, cs_data)
    if not sidecar:
        return 0
    payload = json.dumps(sidecar, separators=(",", ":")).encode()
    (pack_dir / SIDECAR_FILENAME).write_bytes(gzip.compress(payload))
    return len(sidecar)


def read_sounding_sidecar(pack_dir: Path) -> dict | None:
    """Read and gunzip the sidecar, returning *None* if absent or unreadable."""
    path = pack_dir / SIDECAR_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(gzip.decompress(path.read_bytes()))
    except Exception:
        logger.warning("Failed to read sounding sidecar %s — falling back to recompute", path, exc_info=True)
        return None


def load_or_build_sounding_profile(
    pack_dir: Path,
    ra_data: dict,
    cs_data: dict,
    point_index: int,
    model: str,
    *,
    _sidecar: dict | None = None,
) -> SoundingProfileResponse | None:
    """Return one sounding profile, preferring the sidecar over recompute.

    1. If the sidecar is present and holds ``sounding-{pt}-{model}``, validate
       and return it (no MetPy recompute).
    2. Otherwise fall back to :func:`_build_sounding_profile` (on-the-fly).

    Pass ``_sidecar`` to reuse an already-gunzipped sidecar across calls within
    one request.
    """
    sidecar = _sidecar if _sidecar is not None else read_sounding_sidecar(pack_dir)
    if sidecar is not None:
        entry = sidecar.get(_sounding_key(point_index, model))
        if entry is not None:
            return SoundingProfileResponse.model_validate(entry)
    return _build_sounding_profile(ra_data, cs_data, point_index, model)
