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

import statistics
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
from weatherbrief.units import M_PER_SM as _M_PER_SM

# Two independent primary ceiling estimates — the LOWER (more conservative) is
# used when both exist, matching ``analysis.airport_conditions.reconcile_ceiling``
# so the airport-conditions pipeline and this snapshot pipeline derive the same
# per-model ceiling. ``cloud_base_ft`` / ``lcl_ft`` are lower-priority fallbacks
# used only when neither primary estimate is present.
_CEILING_PRIMARY = ("sounding_ceiling_ft", "nwp_ceiling_ft")
_CEILING_FALLBACK = ("cloud_base_ft", "lcl_ft")


def best_ceiling(snap: dict[str, Any]) -> float | None:
    """Pick the per-model ceiling estimate from a snapshot dict.

    Takes ``min(sounding_ceiling_ft, nwp_ceiling_ft)`` when either primary
    estimate is present (the conservative reconciliation shared with
    ``reconcile_ceiling``), otherwise falls back to ``cloud_base_ft`` then
    ``lcl_ft``. ``snap`` keys are the ``AirportForecastSnapshotRow`` column names.
    """
    primary = [snap.get(f) for f in _CEILING_PRIMARY]
    primary = [v for v in primary if v is not None]
    if primary:
        return min(primary)
    for field in _CEILING_FALLBACK:
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


# Numeric consensus fields and their "worse" direction. In worst mode the
# consensus takes the least-favourable value; in majority mode it takes the
# median within the winning-category pool (see ``consensus``). Wind direction is
# NOT here — it is not a more/less quantity and is always a circular mean.
#   * lower is worse → ``min`` in worst mode (ceiling, visibility, headwind —
#     a positive headwind helps, so the weakest headwind / strongest tailwind
#     is the conservative pick).
#   * higher is worse → ``max`` in worst mode (wind speed, crosswind, CAPE).
_WORST_IS_MIN = frozenset({"ceiling_ft", "visibility_m", "headwind_kt"})
_NUMERIC_FIELDS = (
    "wind_speed_kt", "ceiling_ft", "cape_jkg", "visibility_m", "crosswind_kt",
    "headwind_kt", "cloud_cover_pct",
)

# Convective-risk severity, low→high. Mirrors the client's ``RISK_ORDER`` in
# ``web/ts/visualization/weather-map-consensus.ts`` so worst/majority pick the
# same category on both sides.
_RISK_ORDER = ("none", "marginal", "low", "moderate", "high", "extreme")


def _ordinal_consensus(values: list[str], order: tuple[str, ...], mode: str) -> str:
    """Reduce categorical values by ordinal severity.

    ``worst`` → highest-ranked value; ``majority`` → modal value with the worst
    tied candidate breaking ties. Unknown values rank 0 (matches the client's
    ``?? 0`` fallback in ``ordinalConsensus``).
    """
    def rank(v: str) -> int:
        return order.index(v) if v in order else 0

    if mode == "worst":
        return max(values, key=rank)
    counts = Counter(values)
    max_count = max(counts.values())
    tied = [v for v, n in counts.items() if n == max_count]
    return max(tied, key=rank)


def _reduce_numeric(field: str, vals: list[float], mode: str) -> float:
    """Reduce per-model values for one field under the active consensus mode.

    ``majority`` → median (a robust "typical" value); ``worst`` → the
    least-favourable value per ``_WORST_IS_MIN``.
    """
    if mode == "majority":
        return statistics.median(vals)
    return min(vals) if field in _WORST_IS_MIN else max(vals)


def consensus(per_model: dict[str, dict], mode: str = "majority") -> dict[str, Any]:
    """Compute consensus across models for key variables.

    ``mode``:
      * ``"worst"`` — most restrictive category; every numeric field is the
        least-favourable value across ALL models.
      * ``"majority"`` — most common category (worst as tiebreaker); every
        numeric field is the MEDIAN within the *winning-category pool* (the
        models that voted for the shown category). Restricting the median to
        that pool guarantees the numbers can never contradict the category
        badge, while median (vs. worst) gives the typical reading that matches
        majority's intent. Wind direction is a circular mean of the pool.

    The same rule applies uniformly to every numeric field (ceiling, visibility,
    wind speed, crosswind, headwind, CAPE, cloud cover). Convective risk is an
    ordinal reduced over all models (worst rank / modal-with-worst-tiebreak),
    and wind direction is a circular mean — both special-cased. Returns
    per-variable agreement labels alongside consensus values.
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

    # Numeric reductions draw from the winning-category pool in majority mode
    # (so a shown VFR ceiling comes only from the VFR models), and from all
    # models in worst mode (the reduction is worst-across-all regardless).
    if mode == "majority":
        pool = [m for m in models_with_data if per_model[m].get("flight_category") == consensus_cat]
    else:
        pool = models_with_data

    # Per-variable agreement is about divergence across ALL models, independent
    # of the winning pool.
    agreement: dict[str, str] = {}
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

    result: dict[str, Any] = {
        "flight_category": consensus_cat,
        "agreement": agreement,
    }

    # Convective risk is ordinal like flight category, but reduced over ALL
    # models (not the winning-category pool) — matching the client, which maps
    # every model's risk before the ordinal reduction.
    risks = [(per_model[m].get("convective_risk") or "none") for m in models_with_data]
    result["convective_risk"] = _ordinal_consensus(risks, _RISK_ORDER, mode)

    for field in _NUMERIC_FIELDS:
        vals = [per_model[m].get(field) for m in pool]
        vals = [v for v in vals if v is not None]
        if vals:
            result[field] = round(_reduce_numeric(field, vals, mode), 1)

    # Wind direction: circular mean over the winning pool (not a worse/less
    # quantity, so it never takes a min/max/median).
    dir_vals = [per_model[m].get("wind_dir_deg") for m in pool]
    dir_vals = [v for v in dir_vals if v is not None]
    if dir_vals:
        mean, _ = circular_spread(dir_vals)
        result["wind_dir_deg"] = round(mean, 1)

    return result
