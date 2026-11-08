"""Visibility-derivation inputs stored on forecast snapshots (#979, phase 0).

The derivation methods #979 lists (hydrometeor extinction after Stoelinga and
Warner 1999, Gultepe 2006 fog extinction, an RH haze term, a RAP-style blend,
fog rules incl. the Fog Stability Index) read a small, fixed set of fields.
Most of them a snapshot row already carries (2 m T/Td, 10 m wind,
precipitation and snowfall, low cloud cover, the model's own visibility).
This module fills the rest, in SI-ish snapshot units, from whatever a model's
snapshot path has, so every model writes the same columns:

- the lowest model level's cloud water, cloud ice, rain and snow mixing
  ratios (kg/kg) and cloud cover (%): ICON reads them from one model-level
  file per variable (:func:`icon_lowest_level_columns`);
- surface pressure (hPa), for air density;
- T, Td and wind speed at 850 hPa, the Fog Stability Index's upper level
  (:func:`level_850_columns`), from the sounding every snapshot path builds.

Every column is optional: a field a model does not publish is NULL, never 0,
so a method that needs it is skipped rather than fed a made-up value.
Nothing here changes a grade.
"""

from __future__ import annotations

from typing import Any, Iterable

#: The snapshot columns this module owns (``AirportForecastSnapshotRow``,
#: migration 116). ``_store_snapshots`` copies exactly these.
VISIBILITY_INPUT_COLUMNS: tuple[str, ...] = (
    "surface_pressure_hpa",
    "lowest_level_cloud_water_kg_kg",
    "lowest_level_cloud_ice_kg_kg",
    "lowest_level_rain_kg_kg",
    "lowest_level_snow_kg_kg",
    "lowest_level_cloud_cover_pct",
    "t850_c",
    "td850_c",
    "wind_speed_850_kt",
)

#: Key prefix of a lowest-model-level field inside an ICON snapshot step's
#: decoded surface dict (``GribSnapshotStep.surface``): the model-level files
#: decode through the one-field-per-file surface decoder, keyed by name, and
#: the prefix keeps them apart from the single-level fields of the same name.
LOWEST_LEVEL_PREFIX = "ml_"

#: DWD model-level variable → snapshot column.
LOWEST_LEVEL_RAW_KEYS: dict[str, str] = {
    "qc": "lowest_level_cloud_water_kg_kg",
    "qi": "lowest_level_cloud_ice_kg_kg",
    "qr": "lowest_level_rain_kg_kg",
    "qs": "lowest_level_snow_kg_kg",
    "clc": "lowest_level_cloud_cover_pct",
}

# A mixing ratio above this is not a real near-surface value (a decode or
# packing error): 10 g/kg of cloud water is already far beyond fog.
_MAX_MIXING_RATIO = 0.05

# The 850 hPa level is kept only where it is above the model's ground by at
# least this much: below ground a pressure-level value is the producer's
# extrapolation, not air the fog index can use.
_MIN_850_CLEARANCE_HPA = 10.0


def icon_lowest_level_columns(raw: dict[str, float] | None) -> dict[str, float]:
    """Snapshot columns from the ``ml_*`` keys of one ICON point's raw dict.

    Mixing ratios are clamped at 0 from below (GRIB packing can return a
    hair under zero) and dropped above :data:`_MAX_MIXING_RATIO`; cover is
    clamped to 0-100 %. A key the point lacks gives no column.
    """
    out: dict[str, float] = {}
    if not raw:
        return out
    for var, column in LOWEST_LEVEL_RAW_KEYS.items():
        value = raw.get(LOWEST_LEVEL_PREFIX + var)
        if value is None or value != value:  # None or NaN
            continue
        if var == "clc":
            out[column] = min(100.0, max(0.0, float(value)))
            continue
        value = max(0.0, float(value))
        if value > _MAX_MIXING_RATIO:
            continue
        out[column] = value
    return out


def _is_850(level: Any) -> bool:
    """True for an 850 hPa level, whether stored as int, float or string."""
    try:
        return abs(float(getattr(level, "pressure_hpa", None)) - 850.0) < 0.5
    except (TypeError, ValueError):
        return False


def level_850_columns(
    levels: Iterable[Any] | None,
    surface_pressure_hpa: float | None = None,
) -> dict[str, float]:
    """``t850_c`` / ``td850_c`` / ``wind_speed_850_kt`` from a sounding.

    *levels* are ``PressureLevelData``-like objects (``pressure_hpa``,
    ``temperature_c``, ``dewpoint_c``, ``wind_speed_kt``). Nothing is
    returned when there is no 850 hPa level, or when the surface pressure is
    known and the level is not at least ``_MIN_850_CLEARANCE_HPA`` above the
    ground (an elevated airport, where the index does not apply). A level
    field that is None gives no column.
    """
    if not levels:
        return {}
    if (
        surface_pressure_hpa is not None
        and surface_pressure_hpa < 850.0 + _MIN_850_CLEARANCE_HPA
    ):
        return {}
    level = next((lv for lv in levels if _is_850(lv)), None)
    if level is None:
        return {}
    out: dict[str, float] = {}
    for attr, column in (
        ("temperature_c", "t850_c"),
        ("dewpoint_c", "td850_c"),
        ("wind_speed_kt", "wind_speed_850_kt"),
    ):
        value = getattr(level, attr, None)
        if value is not None and value == value:
            out[column] = float(value)
    return out


def snapshot_input_columns(hourly: Any) -> dict[str, float | None]:
    """The model-agnostic part, from one ``HourlyForecast``.

    Surface pressure and the 850 hPa level; the ICON lowest-level fields are
    added by the ICON row builder, which has the raw step.
    """
    sp = getattr(hourly, "surface_pressure_hpa", None)
    out: dict[str, float | None] = {"surface_pressure_hpa": sp}
    out.update(level_850_columns(getattr(hourly, "pressure_levels", None), sp))
    return out
