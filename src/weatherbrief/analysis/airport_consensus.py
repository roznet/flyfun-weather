"""Shared airport forecast-snapshot consensus math.

Pure functions that turn per-model forecast-snapshot dicts (keys == the
``AirportForecastSnapshotRow`` column names) into a flight category,
runway-relative wind, and a cross-model consensus.

Used by BOTH the pan-European forecast map (``tasks/map_queries.py``) and the
route-alternates stage (``tasks/alternates.py``) so the SAME airport yields the
SAME category / crosswind / consensus in both views. The forecast map feeds an
ORM-row→dict adapter; the alternates stage feeds the snapshot dicts produced by
``tasks/standalone_verification`` directly (same column keys).

Nothing here touches the database or the ORM — it operates on plain dicts so
both callers can share one code path. See ``designs/future/alternates.md``
("Seam 2") for the rationale.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from weatherbrief.analysis.airport_conditions import (
    classify_flight_category,
    compute_runway_winds,
)
from weatherbrief.analysis.comparison import (
    circular_spread,
    compare_models,
)
from weatherbrief.analysis.wind import compute_wind_components
from weatherbrief.models.airport_conditions import FlightCategory, RunwayEnd

_M_PER_SM = 1609.34

# Ceiling estimate priority — read from the snapshot dict's column-name keys.
_CEILING_FIELDS = ("sounding_ceiling_ft", "nwp_ceiling_ft", "cloud_base_ft", "lcl_ft")


def best_ceiling(snap: dict[str, Any]) -> float | None:
    """Pick the best ceiling estimate from a snapshot dict.

    ``snap`` keys are the ``AirportForecastSnapshotRow`` column names.
    """
    for field in _CEILING_FIELDS:
        val = snap.get(field)
        if val is not None:
            return val
    return None


def flight_category(snap: dict[str, Any]) -> str:
    """Derive flight category from a snapshot dict.

    Visibility is always converted from metres to statute miles before
    classification (the map and alternates must agree on units).
    """
    ceiling = best_ceiling(snap)
    vis_m = snap.get("visibility_m")
    vis_sm = vis_m / _M_PER_SM if vis_m is not None else None
    return classify_flight_category(ceiling, vis_sm).value


def snap_to_dict(snap: dict[str, Any]) -> dict[str, Any]:
    """Convert a column-keyed snapshot dict into the lightweight per-model dict.

    The output is the per-model shape consumed by :func:`enrich_wind` and
    :func:`consensus` (and surfaced verbatim in the forecast-map API response).
    """
    return {
        "ceiling_ft": best_ceiling(snap),
        "visibility_m": snap.get("visibility_m"),
        "wind_speed_kt": snap.get("wind_speed_10m_kt"),
        "wind_dir_deg": snap.get("wind_direction_10m_deg"),
        "wind_gust_kt": snap.get("wind_gusts_10m_kt"),
        "cloud_cover_pct": snap.get("cloud_cover_pct"),
        "cape_jkg": snap.get("cape_jkg"),
        "convective_risk": snap.get("sounding_convective_risk") or "none",
        "temperature_c": snap.get("temperature_2m_c"),
        "flight_category": flight_category(snap),
    }


def enrich_wind(d: dict, runway_ends: list[RunwayEnd]) -> None:
    """Add crosswind/headwind for the best runway to a per-model dict, in place."""
    ws, wd = d.get("wind_speed_kt"), d.get("wind_dir_deg")
    if not runway_ends or ws is None or wd is None:
        return
    all_rwy = compute_runway_winds(runway_ends, ws, wd)
    if not all_rwy:
        return
    best = min(all_rwy, key=lambda r: (r.crosswind_kt, -r.headwind_kt))
    d["crosswind_kt"] = best.crosswind_kt
    d["headwind_kt"] = round(best.headwind_kt, 1)
    d["best_runway_id"] = best.runway_id
    # Gust components on the same best runway
    gust = d.get("wind_gust_kt")
    if gust is not None:
        wc = compute_wind_components(gust, wd, best.heading_deg)
        d["gust_crosswind_kt"] = round(abs(wc.crosswind_kt), 1)
        d["gust_headwind_kt"] = round(wc.headwind_kt, 1)


_AGREEMENT_LABELS = {"good": "consistent", "moderate": "mixed", "poor": "divergent"}


def agreement_label(level: str) -> str:
    """Map internal agreement level to user-facing label."""
    return _AGREEMENT_LABELS.get(level, level)


def consensus(per_model: dict[str, dict], mode: str = "worst") -> dict[str, Any]:
    """Compute consensus across models for key variables.

    mode: "worst" = most restrictive category, "majority" = most common (worst as tiebreaker)
    Returns per-variable agreement labels alongside consensus values.
    """
    models_with_data = list(per_model.keys())
    if not models_with_data:
        return {"flight_category": "VFR", "agreement": {}}

    cats = [FlightCategory(m["flight_category"]) for m in per_model.values()]

    if mode == "majority":
        counts = Counter(cats)
        max_count = max(counts.values())
        tied = [c for c, n in counts.items() if n == max_count]
        consensus_cat = FlightCategory.worst(tied).value
    else:
        consensus_cat = FlightCategory.worst(cats).value

    # Per-variable agreement
    agreement: dict[str, str] = {}
    # Flight category agreement: based on whether models agree on the category
    unique_cats = set(c.value for c in cats)
    if len(unique_cats) == 1:
        agreement["flight_category"] = agreement_label("good")
    elif len(unique_cats) == len(cats):
        agreement["flight_category"] = agreement_label("poor")
    else:
        agreement["flight_category"] = agreement_label("moderate")

    for var in ("wind_speed_kt", "ceiling_ft", "cape_jkg", "visibility_m", "cloud_cover_pct"):
        vals = {m: per_model[m].get(var) for m in models_with_data}
        vals = {m: v for m, v in vals.items() if v is not None}
        if len(vals) >= 2:
            div = compare_models(var, vals)
            agreement[var] = agreement_label(div.agreement.value)

    # Means for numeric fields
    result: dict[str, Any] = {
        "flight_category": consensus_cat,
        "agreement": agreement,
    }
    for field in ("wind_speed_kt", "wind_dir_deg", "ceiling_ft", "cape_jkg", "visibility_m"):
        vals = [per_model[m].get(field) for m in models_with_data]
        vals = [v for v in vals if v is not None]
        if vals:
            if field == "wind_dir_deg":
                mean, _ = circular_spread(vals)
                result[field] = round(mean, 1)
            else:
                result[field] = round(sum(vals) / len(vals), 1)

    # Crosswind/headwind consensus: worst (max) across models
    for field in ("crosswind_kt", "headwind_kt"):
        vals = [per_model[m].get(field) for m in models_with_data]
        vals = [v for v in vals if v is not None]
        if vals:
            result[field] = round(max(vals), 1)

    return result
