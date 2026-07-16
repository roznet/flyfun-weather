"""Time-axis interpolation of GRIB-enriched fields.

GRIB enrichment writes data at native model forecast steps. Beyond ~90h lead
time, ECMWF / ICON-EU step out to 3-hourly cadence (then 6-hourly past 144h).
Open-Meteo provides hourly data interpolated from its own pipeline, but mixing
GRIB-anchor hours with OM-interpolated gap hours produces an inconsistent
single-source story.

Interpolation policy:

  - **Surface scalars** (HourlyForecast surface fields, ECMWF GRIB-anchored):
    linear in time between bracketing GRIB-anchor hours. Wind direction uses
    shortest-arc circular interpolation, with speed-gating below ~1 kt.
  - **Pressure-level soundings** (ECMWF / ICON-EU GRIB replacement): linear
    in time per field per level; dewpoint is **derived** from interpolated
    (T, RH) via the Magnus formula rather than interpolated directly. This
    matches operational practice (ECMWF MARS, WRF post-processing) and keeps
    derived quantities consistent with primitives.
  - **GFS cloud diagnostics** (when ``gfs_init`` provided): low/mid/high
    cover_pct interpolated linearly between **window midpoints** rather than
    step times — NCEP publishes the averaged form (LCDC/MCDC/HCDC) past f0,
    so the value at native step f is centred on the midpoint of its 1/2/3-h
    window. Layer geometry (base/top/temp) is held over from the higher-cover
    endpoint, not interpolated. Layers fall away when interpolated cover
    < 5%. Convective/boundary/total cover and ceiling stay step-anchored
    (instantaneous in pgrb2).
  - **Non-GFS cloud diagnostics**: forward-fill (persistence) — ICON-EU and
    ECMWF publish instantaneous cloud cover, and base/top interpolation
    between dissimilar geometries would produce phantom layers.
  - **GFS CLW/ICMR overlays** (CLW/ICMR added onto OM pressure_levels without
    list replacement): linear interp between native step times when
    ``gfs_init`` provided; forward-fill otherwise.

Interpolation rules (see also spatial_interpolation.py for the spatial axis):

    Time axis  — linear between bracketing native GRIB hours (this module)
    Spatial axis — linear between neighboring route points
    Vertical axis — linear in pressure, handled in sounding analysis
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from weatherbrief.fetch.open_meteo import magnus_dewpoint

if TYPE_CHECKING:
    from weatherbrief.models import (
        HourlyForecast,
        NWPCloudDiagnostics,
        NWPCloudLayerDiag,
        PressureLevelData,
        RouteCrossSection,
        WaypointForecast,
    )

logger = logging.getLogger(__name__)

# Minimum cover after midpoint interpolation below which an averaged-window
# layer is treated as dissipated (geometry dropped, cover set to None). See
# issue #148 — protects against phantom thin layers when the averaged window
# decays toward 0.
_GFS_LAYER_DROP_THRESHOLD_PCT = 5.0


def propagate_all(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    *,
    gfs_init: datetime | None = None,
) -> None:
    """Apply all time-axis fills: linear interp where appropriate, forward-fill
    where persistence is the right semantic.

    Called once after all GRIB enrichment (GFS + ICON-EU + ECMWF) completes,
    before the analysis stage.

    Args:
        gfs_init: GFS model run init time. When provided, GFS sections use
            window-midpoint linear interp for low/mid/high cover (fixing the
            averaged-window phantom-layer pathology — issue #148) and linear
            interp for CLW/ICMR. When None, all sections use forward-fill.
    """
    # ECMWF surface linear interp must run BEFORE cloud-diag fill: it uses
    # ``nwp_cloud_diagnostics is not None`` as the GRIB-anchor detector, and
    # diag fill propagates / interpolates diagnostics onto gap hours, making
    # every hour look like an anchor afterwards.
    _linear_interp_ecmwf_surface(sections, all_forecasts)
    _fill_cloud_diagnostics(sections, all_forecasts, gfs_init=gfs_init)
    _linear_interp_pressure_levels(sections, all_forecasts)
    # CLW/ICMR overlay onto OM pressure_levels (GFS path only — for ECMWF/ICON
    # the pressure-level interp above already populates CLW/ICMR within the
    # rebuilt PressureLevelData).
    _fill_cloud_water(sections, all_forecasts, gfs_init=gfs_init)


# ---------------------------------------------------------------------------
# Cloud diagnostics (NWPCloudDiagnostics on HourlyForecast)
# ---------------------------------------------------------------------------

def _fill_cloud_diagnostics(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    *,
    gfs_init: datetime | None,
) -> None:
    """Fill ``nwp_cloud_diagnostics`` on hours between native GRIB steps.

    For GFS sections, when ``gfs_init`` is provided, uses window-midpoint
    linear interpolation for low/mid/high cover (see _interp_gfs_diag_hourly).
    For non-GFS sections (ECMWF, ICON-EU) and the GFS fallback path, uses
    forward-fill (persistence) — cloud-layer geometry is categorical and
    slowly varying for these models.
    """
    from weatherbrief.models import ModelSource

    total = 0
    for cs in sections:
        gfs = cs.model == ModelSource.GFS and gfs_init is not None
        for wf in cs.point_forecasts:
            if gfs:
                total += _interp_gfs_diag_hourly(wf.hourly, gfs_init)
            else:
                total += _fill_diag_hourly(wf.hourly)
    for wf in all_forecasts:
        gfs = wf.model == ModelSource.GFS and gfs_init is not None
        if gfs:
            total += _interp_gfs_diag_hourly(wf.hourly, gfs_init)
        else:
            total += _fill_diag_hourly(wf.hourly)

    if total:
        logger.info(
            "Cloud diagnostics filled on %d hourly entries (gfs_midpoint=%s)",
            total, gfs_init is not None,
        )


def _fill_diag_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Forward-fill: each gap hour gets a shallow copy of the preceding
    anchor's diag, so a downstream in-place mutation (e.g. icing analysis
    setting a flag) on one hour cannot leak across other hours that share
    the same anchor."""
    filled = 0
    last_diag: NWPCloudDiagnostics | None = None
    for h in sorted(hourly_list, key=lambda h: h.time):
        if h.nwp_cloud_diagnostics is not None:
            last_diag = h.nwp_cloud_diagnostics
        elif last_diag is not None:
            h.nwp_cloud_diagnostics = last_diag.model_copy()
            filled += 1
    return filled


def _interp_gfs_diag_hourly(
    hourly_list: list[HourlyForecast],
    gfs_init: datetime,
) -> int:
    """Window-midpoint linear interp for GFS cloud diagnostics.

    NCEP publishes only the time-averaged form of LCDC/MCDC/HCDC (and the
    matching PRES bottoms/tops) for forecast hours > 0. Each native step f
    carries values averaged over the window ending at f, of length 1/2/3 h
    depending on f's position in the GFS 3-h reset cycle (all 3-h past f120).
    Anchoring the averaged value at the **window midpoint** rather than the
    step time avoids forward-fill smearing the previous window's cover
    forward across the snapshot hour.

    Algorithm per route point:

    1. Identify "anchor" hours (those with ``nwp_cloud_diagnostics`` set).
    2. For each pair of consecutive anchors, compute each anchor's window
       midpoint from its f-hour. Interpolate gap hours linearly between the
       two midpoints, clamping to [0, 1] (no extrapolation past the bracket).
    3. Layer geometry (base_ft / top_ft / top_temp_c) is **not** numerically
       interpolated — it uses the higher-cover endpoint's geometry. When the
       interpolated cover falls below 5%, the layer is dropped entirely.
    4. Instantaneous fields (convective_cover_pct, boundary_cover_pct,
       total_cover_pct, ceiling_ft, convective_base/top_ft, freezing_level_ft)
       stay step-time-anchored and interpolate linearly with no midpoint
       offset.
    5. Hours after the last anchor are forward-filled (no upper bracket).
    6. Hours before the first anchor are left as-is (matches the prior
       forward-fill semantics — no data to extrapolate from).
    """
    from weatherbrief.models import NWPCloudDiagnostics

    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    anchor_indices = [
        i for i, h in enumerate(sorted_hours)
        if h.nwp_cloud_diagnostics is not None
    ]
    if not anchor_indices:
        return 0

    filled = 0

    # Within-anchor segments: window-midpoint interp.
    for k in range(len(anchor_indices) - 1):
        prev_i = anchor_indices[k]
        next_i = anchor_indices[k + 1]
        if next_i - prev_i <= 1:
            continue  # adjacent anchors — no gap
        prev_h = sorted_hours[prev_i]
        next_h = sorted_hours[next_i]
        prev_diag = prev_h.nwp_cloud_diagnostics
        next_diag = next_h.nwp_cloud_diagnostics
        if prev_diag is None or next_diag is None:
            continue

        prev_fhour = _gfs_fhour(gfs_init, prev_h.time)
        next_fhour = _gfs_fhour(gfs_init, next_h.time)
        prev_mid = prev_h.time - timedelta(hours=_gfs_window_length_hours(prev_fhour) / 2.0)
        next_mid = next_h.time - timedelta(hours=_gfs_window_length_hours(next_fhour) / 2.0)
        mid_span = (next_mid - prev_mid).total_seconds()
        step_span = (next_h.time - prev_h.time).total_seconds()
        if mid_span <= 0 or step_span <= 0:
            continue

        for i in range(prev_i + 1, next_i):
            h = sorted_hours[i]
            # Averaged-window cover for low/mid/high uses midpoint anchoring.
            mid_frac = (h.time - prev_mid).total_seconds() / mid_span
            mid_frac = max(0.0, min(1.0, mid_frac))
            # Instantaneous fields use step-time anchoring.
            step_frac = (h.time - prev_h.time).total_seconds() / step_span
            step_frac = max(0.0, min(1.0, step_frac))

            interp_diag = _interp_diag_at(
                prev_diag, next_diag, mid_frac=mid_frac, step_frac=step_frac,
            )
            h.nwp_cloud_diagnostics = interp_diag
            filled += 1

    # After the last anchor: forward-fill (no upper bracket to interp against).
    # Each trailing gap hour gets its own shallow copy so a downstream
    # in-place mutation on one hour doesn't leak across the others.
    last_idx = anchor_indices[-1]
    last_diag = sorted_hours[last_idx].nwp_cloud_diagnostics
    for i in range(last_idx + 1, len(sorted_hours)):
        if sorted_hours[i].nwp_cloud_diagnostics is None:
            sorted_hours[i].nwp_cloud_diagnostics = last_diag.model_copy()
            filled += 1

    return filled


def _gfs_fhour(gfs_init: datetime, target: datetime) -> int:
    """Forecast hour from init. Rounds to nearest integer — native anchors
    sit on integer hours from init."""
    return round((target - gfs_init).total_seconds() / 3600.0)


def _gfs_window_length_hours(fhour: int) -> int:
    """Width of the averaging window ending at GFS forecast step ``fhour``.

    NCEP cadence:
      - f001 / f004 / f007 / … → 1 h
      - f002 / f005 / f008 / … → 2 h
      - f003 / f006 / f009 / … / f120 → 3 h
      - f > 120 → 3 h (3-hourly cadence past f120)

    f000 is analysis (no window) and gets returned as 0.
    """
    if fhour <= 0:
        return 0
    if fhour > 120:
        return 3
    r = fhour % 3
    return 3 if r == 0 else r


def _interp_diag_at(
    prev_diag: NWPCloudDiagnostics,
    next_diag: NWPCloudDiagnostics,
    *,
    mid_frac: float,
    step_frac: float,
) -> NWPCloudDiagnostics:
    """Build an interpolated NWPCloudDiagnostics for one gap hour.

    ``mid_frac`` is the interpolation fraction in window-midpoint space —
    used for low/mid/high cover_pct (the averaged-window fields).
    ``step_frac`` is the fraction in step-time space — used for
    instantaneous fields (convective, boundary, total, ceiling, etc.).
    """
    from weatherbrief.models import NWPCloudDiagnostics

    low = _interp_layer(prev_diag.low, next_diag.low, mid_frac)
    mid = _interp_layer(prev_diag.mid, next_diag.mid, mid_frac)
    high = _interp_layer(prev_diag.high, next_diag.high, mid_frac)

    return NWPCloudDiagnostics(
        low=low,
        mid=mid,
        high=high,
        convective_cover_pct=_lerp(
            prev_diag.convective_cover_pct, next_diag.convective_cover_pct, step_frac,
        ),
        convective_base_ft=_lerp(
            prev_diag.convective_base_ft, next_diag.convective_base_ft, step_frac,
        ),
        convective_top_ft=_lerp(
            prev_diag.convective_top_ft, next_diag.convective_top_ft, step_frac,
        ),
        total_cover_pct=_lerp(
            prev_diag.total_cover_pct, next_diag.total_cover_pct, step_frac,
        ),
        boundary_cover_pct=_lerp(
            prev_diag.boundary_cover_pct, next_diag.boundary_cover_pct, step_frac,
        ),
        ceiling_ft=_lerp(prev_diag.ceiling_ft, next_diag.ceiling_ft, step_frac),
        freezing_level_ft=_lerp(
            prev_diag.freezing_level_ft, next_diag.freezing_level_ft, step_frac,
        ),
    )


def _interp_layer(
    prev: NWPCloudLayerDiag,
    nxt: NWPCloudLayerDiag,
    frac: float,
) -> NWPCloudLayerDiag:
    """Interpolate a single ICAO band (low/mid/high).

    Cover interpolates linearly between the two endpoints (at midpoint frac
    in the caller). Geometry holds over from the higher-cover endpoint —
    interpolating altitudes between dissimilar layer geometries would create
    phantom intermediate layers. When interpolated cover falls below 5%, the
    whole layer drops out.
    """
    from weatherbrief.models import NWPCloudLayerDiag

    prev_cover = prev.cover_pct
    next_cover = nxt.cover_pct
    cover = _lerp(prev_cover, next_cover, frac)

    if cover is None or cover < _GFS_LAYER_DROP_THRESHOLD_PCT:
        # No supportable layer at this hour.
        return NWPCloudLayerDiag()

    # Hold geometry from whichever endpoint has the higher cover. Ties go to
    # the earlier endpoint (deterministic).
    if (next_cover or 0.0) > (prev_cover or 0.0):
        source = nxt
    else:
        source = prev
    return NWPCloudLayerDiag(
        cover_pct=cover,
        base_ft=source.base_ft,
        top_ft=source.top_ft,
        top_temp_c=source.top_temp_c,
    )


# ---------------------------------------------------------------------------
# Cloud water / ice mixing ratio (per-pressure-level on HourlyForecast)
# ---------------------------------------------------------------------------

def _fill_cloud_water(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    *,
    gfs_init: datetime | None,
) -> None:
    """Fill CLW and ICMR per pressure level on gap hours.

    For GFS sections, when ``gfs_init`` is provided, linearly interpolates
    between native step times (CLW/ICMR are instantaneous mixing ratios —
    no midpoint adjustment needed). For non-GFS or the GFS fallback path,
    forward-fills from the preceding anchor.

    Non-GFS sections normally have CLW/ICMR already populated by
    _linear_interp_pressure_levels (which rebuilds the full pressure_levels
    list for gap hours), so this is a no-op there.
    """
    from weatherbrief.models import ModelSource

    total = 0
    for cs in sections:
        gfs = cs.model == ModelSource.GFS and gfs_init is not None
        for wf in cs.point_forecasts:
            if gfs:
                total += _interp_gfs_clw_hourly(wf.hourly)
            else:
                total += _fill_clw_hourly(wf.hourly)
    for wf in all_forecasts:
        gfs = wf.model == ModelSource.GFS and gfs_init is not None
        if gfs:
            total += _interp_gfs_clw_hourly(wf.hourly)
        else:
            total += _fill_clw_hourly(wf.hourly)

    if total:
        logger.info(
            "Cloud water (CLW/ICMR) filled on %d (hour, level) entries"
            " (gfs_linear=%s)",
            total, gfs_init is not None,
        )


def _fill_clw_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Forward-fill CLW/ICMR per pressure level across time."""
    filled = 0
    # Track last known values per pressure level: {hpa: (clw, icmr)}
    last: dict[int, tuple[float | None, float | None]] = {}

    for h in sorted(hourly_list, key=lambda h: h.time):
        for pl in h.pressure_levels:
            p = pl.pressure_hpa
            if pl.cloud_liquid_water_kg_kg is not None:
                # Anchor — record this native-hour value
                last[p] = (
                    pl.cloud_liquid_water_kg_kg,
                    pl.ice_mixing_ratio_kg_kg,
                )
            elif p in last:
                prev_clw, prev_icmr = last[p]
                if prev_clw is not None:
                    pl.cloud_liquid_water_kg_kg = prev_clw
                    if prev_icmr is not None and pl.ice_mixing_ratio_kg_kg is None:
                        pl.ice_mixing_ratio_kg_kg = prev_icmr
                    filled += 1
    return filled


def _interp_gfs_clw_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Linear interp of CLW/ICMR between native step times.

    CLW/ICMR are instantaneous mixing ratios (no averaged-window pathology —
    that's the cover-fraction issue), so step-time anchoring is correct here.
    Hours after the last anchor forward-fill; hours before the first anchor
    are left as-is.

    Anchor invariant: anchor indexing is keyed on ``cloud_liquid_water_kg_kg``
    alone. ICMR rides along — its interp / forward-fill is gated by the
    corresponding CLW being present. This is safe because the GFS GRIB
    decoder writes CLW and ICMR together from the same byte-range fetch
    (`plan_byte_ranges` requests CLMR + ICMR atomically per native step).
    If a future decoder change ever populates ICMR independently of CLW
    for some pressure level, those ICMR values would silently miss the
    interp — update the anchor keying here.
    """
    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    # Per-level anchor index lists: {hpa: [hour_index, ...]}
    per_level_anchors: dict[int, list[int]] = {}
    for i, h in enumerate(sorted_hours):
        for pl in h.pressure_levels:
            if pl.cloud_liquid_water_kg_kg is not None:
                per_level_anchors.setdefault(pl.pressure_hpa, []).append(i)

    if not per_level_anchors:
        return 0

    # Index each hour's pressure levels by pressure for O(1) lookup.
    hour_levels: list[dict[int, PressureLevelData]] = [
        {pl.pressure_hpa: pl for pl in h.pressure_levels}
        for h in sorted_hours
    ]

    filled = 0
    for p, anchors in per_level_anchors.items():
        # Between-anchor segments: linear interp.
        for k in range(len(anchors) - 1):
            prev_i = anchors[k]
            next_i = anchors[k + 1]
            if next_i - prev_i <= 1:
                continue
            prev_pl = hour_levels[prev_i].get(p)
            next_pl = hour_levels[next_i].get(p)
            if prev_pl is None or next_pl is None:
                continue
            span = (sorted_hours[next_i].time - sorted_hours[prev_i].time).total_seconds()
            if span <= 0:
                continue
            for i in range(prev_i + 1, next_i):
                pl = hour_levels[i].get(p)
                if pl is None or pl.cloud_liquid_water_kg_kg is not None:
                    continue
                frac = (sorted_hours[i].time - sorted_hours[prev_i].time).total_seconds() / span
                clw = _lerp(prev_pl.cloud_liquid_water_kg_kg, next_pl.cloud_liquid_water_kg_kg, frac)
                if clw is not None:
                    pl.cloud_liquid_water_kg_kg = clw
                    if pl.ice_mixing_ratio_kg_kg is None:
                        icmr = _lerp(
                            prev_pl.ice_mixing_ratio_kg_kg,
                            next_pl.ice_mixing_ratio_kg_kg,
                            frac,
                        )
                        if icmr is not None:
                            pl.ice_mixing_ratio_kg_kg = icmr
                    filled += 1

        # After last anchor: forward-fill.
        last_i = anchors[-1]
        last_pl = hour_levels[last_i].get(p)
        if last_pl is None or last_pl.cloud_liquid_water_kg_kg is None:
            continue
        for i in range(last_i + 1, len(sorted_hours)):
            pl = hour_levels[i].get(p)
            if pl is None or pl.cloud_liquid_water_kg_kg is not None:
                continue
            pl.cloud_liquid_water_kg_kg = last_pl.cloud_liquid_water_kg_kg
            if pl.ice_mixing_ratio_kg_kg is None and last_pl.ice_mixing_ratio_kg_kg is not None:
                pl.ice_mixing_ratio_kg_kg = last_pl.ice_mixing_ratio_kg_kg
            filled += 1

    return filled


# ---------------------------------------------------------------------------
# GFS RH / condensate gate
# ---------------------------------------------------------------------------

# Per-band RH thresholds for the dry-layer gate (issue #148). When the
# averaged-window cover is positive but the pressure-level RH and condensate
# inside the layer don't support cloud, the layer is dropped. Conservative
# starting values — see issue's "Open question" for low-band marine
# stratocumulus calibration follow-up.
_GFS_GATE_RH_LOW_PCT = 60.0
_GFS_GATE_RH_MID_PCT = 70.0
_GFS_GATE_RH_HIGH_PCT = 70.0

_M_TO_FT = 3.28084


def apply_gfs_rh_condensate_gate(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Drop GFS averaged-cover layers that lack RH and condensate support.

    For each GFS hourly with both ``pressure_levels`` and
    ``nwp_cloud_diagnostics``, gates the low/mid/high bands:

      max(RH within [base_ft, top_ft]) < threshold  AND
      sum(CLMR + ICMR within the same band) == 0

    If both conditions hold the layer is replaced with a no-layer
    ``NWPCloudLayerDiag()``. Convective and boundary bands are instantaneous
    in GFS pgrb2 — not gated. ICON-EU and ECMWF cloud cover is instantaneous
    too, so this routine is GFS-only by design.

    This is a no-op when GFS pressure_levels carry no CLMR/ICMR values
    (gate cannot verify "sum == 0" without observed zeros), preserving the
    forecast as-is rather than dropping confidently.
    """
    from weatherbrief.models import ModelSource

    dropped = 0
    for cs in sections:
        if cs.model != ModelSource.GFS:
            continue
        for wf in cs.point_forecasts:
            for h in wf.hourly:
                dropped += _gate_gfs_hourly(h)
    for wf in all_forecasts:
        if wf.model != ModelSource.GFS:
            continue
        for h in wf.hourly:
            dropped += _gate_gfs_hourly(h)

    if dropped:
        logger.info(
            "GFS RH/condensate gate dropped %d phantom cloud layers", dropped,
        )


def _gate_gfs_hourly(h: HourlyForecast) -> int:
    """Apply the gate to one hourly forecast. Returns # layers dropped.

    Only low/mid/high are gated. Convective, boundary, total, ceiling, and
    freezing level are *instantaneous* products in GFS pgrb2 (TCDC@atmosphere,
    TCDC@convectiveCloudLayer, TCDC@boundaryLayerCloudLayer, GH@cloudCeiling,
    HGT@0degC) — they don't suffer the averaged-window back-smear that
    motivates this gate, so they're forwarded unchanged.

    Caveat: ``ceiling_ft`` is computed by GFS from the column's lowest
    cloudy layer, which can include the layers we just gated. If the only
    cloudy layer at a step was the phantom one dropped here, the inherited
    ``ceiling_ft`` is stale. We don't recompute it because the relationship
    between GFS GH@cloudCeiling and the per-band geometry isn't a simple
    function we can invert at this stage. The downstream ceiling consumers
    cross-check against pressure-level RH anyway.
    """
    from weatherbrief.models import NWPCloudDiagnostics

    diag = h.nwp_cloud_diagnostics
    if diag is None or not h.pressure_levels:
        return 0

    new_low, drop_low = _gate_layer(diag.low, h.pressure_levels, _GFS_GATE_RH_LOW_PCT)
    new_mid, drop_mid = _gate_layer(diag.mid, h.pressure_levels, _GFS_GATE_RH_MID_PCT)
    new_high, drop_high = _gate_layer(diag.high, h.pressure_levels, _GFS_GATE_RH_HIGH_PCT)
    n_dropped = int(drop_low) + int(drop_mid) + int(drop_high)
    if n_dropped == 0:
        return 0

    h.nwp_cloud_diagnostics = NWPCloudDiagnostics(
        low=new_low, mid=new_mid, high=new_high,
        convective_cover_pct=diag.convective_cover_pct,
        convective_base_ft=diag.convective_base_ft,
        convective_top_ft=diag.convective_top_ft,
        total_cover_pct=diag.total_cover_pct,
        boundary_cover_pct=diag.boundary_cover_pct,
        ceiling_ft=diag.ceiling_ft,
        freezing_level_ft=diag.freezing_level_ft,
    )
    return n_dropped


def _gate_layer(
    layer: NWPCloudLayerDiag,
    pressure_levels: list[PressureLevelData],
    rh_threshold_pct: float,
) -> tuple[NWPCloudLayerDiag, bool]:
    """Return (possibly-replaced layer, dropped?).

    Drops only when:
    - layer has positive cover and known base/top
    - at least one pressure level falls within [base_ft, top_ft]
    - max(RH) over those levels < rh_threshold_pct
    - at least one level inside the band reports CLMR or ICMR (otherwise we
      can't claim "sum == 0" — we'd be conflating "no condensate" with
      "no data")
    - sum(CLMR + ICMR) over those levels == 0
    """
    from weatherbrief.models import NWPCloudLayerDiag

    if layer.cover_pct is None or layer.cover_pct <= 0:
        return layer, False
    if layer.base_ft is None or layer.top_ft is None:
        return layer, False

    base_ft = layer.base_ft
    top_ft = layer.top_ft
    if top_ft <= base_ft:
        return layer, False

    max_rh: float | None = None
    cond_sum = 0.0
    cond_observed = False
    in_band = False

    for pl in pressure_levels:
        alt_ft = _pressure_level_altitude_ft(pl)
        if alt_ft is None:
            continue
        if alt_ft < base_ft or alt_ft > top_ft:
            continue
        in_band = True
        if pl.relative_humidity_pct is not None:
            if max_rh is None or pl.relative_humidity_pct > max_rh:
                max_rh = pl.relative_humidity_pct
        if pl.cloud_liquid_water_kg_kg is not None:
            cond_observed = True
            cond_sum += pl.cloud_liquid_water_kg_kg
        if pl.ice_mixing_ratio_kg_kg is not None:
            cond_observed = True
            cond_sum += pl.ice_mixing_ratio_kg_kg

    # Need observations on both axes to gate confidently.
    if not in_band or max_rh is None or not cond_observed:
        return layer, False
    if max_rh >= rh_threshold_pct:
        return layer, False
    if cond_sum > 0:
        return layer, False

    return NWPCloudLayerDiag(), True


def _pressure_level_altitude_ft(pl: PressureLevelData) -> float | None:
    """Altitude in feet for a pressure level. Prefers reported geopotential
    height; falls back to standard-atmosphere pressure conversion."""
    from weatherbrief.models.analysis import pressure_hpa_to_altitude_m

    if pl.geopotential_height_m is not None:
        return pl.geopotential_height_m * _M_TO_FT
    if pl.pressure_hpa is None or pl.pressure_hpa <= 0:
        return None
    return pressure_hpa_to_altitude_m(float(pl.pressure_hpa)) * _M_TO_FT


# ---------------------------------------------------------------------------
# Linear interpolation helpers
# ---------------------------------------------------------------------------

# Threshold below which wind direction is unreliable. Used to gate circular
# interp at 10m where winds can drop to truly calm values; pressure-level
# winds are typically meaningful at any non-None speed.
_CALM_WIND_KT = 1.0


def _lerp(a: float | None, b: float | None, frac: float) -> float | None:
    """Linear interpolation; returns None if either endpoint is None."""
    if a is None or b is None:
        return None
    return a + (b - a) * frac


def _lerp_circ(a: float | None, b: float | None, frac: float) -> float | None:
    """Shortest-arc linear interpolation in degrees (0..360)."""
    if a is None or b is None:
        return None
    diff = ((b - a + 540) % 360) - 180
    return (a + diff * frac) % 360


def _wind_uv(speed_kt: float, direction_deg: float) -> tuple[float, float]:
    """Meteorological (speed, from-direction) → (u, v) components.

    Direction is where the wind comes FROM, so the vector points the opposite
    way: u = -speed·sin(dir), v = -speed·cos(dir).
    """
    rad = math.radians(direction_deg)
    return -speed_kt * math.sin(rad), -speed_kt * math.cos(rad)


def _lerp_wind(
    speed_a: float | None,
    dir_a: float | None,
    speed_b: float | None,
    dir_b: float | None,
    frac: float,
) -> tuple[float | None, float | None]:
    """Vector-correct temporal interpolation of a wind → (speed_kt, dir_deg).

    Interpolating scalar speed and circular direction independently is wrong:
    10 kt @ 090° → 10 kt @ 270° would hold 10 kt through an intermediate
    bearing instead of passing through near-calm. Reconstruct U/V, interpolate
    the components, then derive speed and direction. (#441 finding #7)

    When an endpoint lacks a direction (calm), fall back to scalar speed interp
    and keep whichever direction is defined. When the interpolated vector is
    near-calm, direction is ill-defined so copy the nearer anchor's direction.
    """
    if speed_a is None or speed_b is None:
        return None, None
    if dir_a is None or dir_b is None:
        spd = speed_a + (speed_b - speed_a) * frac
        return spd, (dir_a if dir_a is not None else dir_b)
    ua, va = _wind_uv(speed_a, dir_a)
    ub, vb = _wind_uv(speed_b, dir_b)
    u = ua + (ub - ua) * frac
    v = va + (vb - va) * frac
    spd = math.hypot(u, v)
    if spd < _CALM_WIND_KT:
        return spd, (dir_a if frac < 0.5 else dir_b)
    return spd, math.degrees(math.atan2(-u, -v)) % 360.0


# ---------------------------------------------------------------------------
# ECMWF surface scalars (HourlyForecast surface fields) — linear interp
# ---------------------------------------------------------------------------

# Instantaneous fields written by ``_apply_ecmwf_surface_to_hourly``.
# Precip/snow are *window-rate* — distributed at apply time across every hour
# in the differencing window, so they don't need temporal interpolation.
# Genuinely instantaneous surface scalars — linearly interpolable across gaps.
# NOT listed here (handled specially in _interp_surface_hourly):
#  - wind speed/direction → U/V-component interp (_lerp_wind)  (#441 #7)
#  - wind gust → window MAXIMUM, held over the covering interval (#441 #6)
_ECMWF_SURFACE_INSTANT_FIELDS: tuple[str, ...] = (
    "temperature_2m_c",
    "dewpoint_2m_c",
    "visibility_m",
    "cape_jkg",
    "surface_pressure_hpa",
    "nwp_k_index",
    "nwp_total_totals",
)


def _linear_interp_ecmwf_surface(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Linearly interpolate ECMWF GRIB-derived surface scalars across intra-window gaps.

    GRIB delivers surface fields at native step times (1h cadence to 90h, then
    3h, then 6h past 144h). Open-Meteo provides hourly values from its own
    pipeline; mixing them with GRIB-anchor hours produces inconsistent sources.

    We use ``nwp_cloud_diagnostics is not None`` as the GRIB-anchor detector:
    surface scalars and cloud diagnostics are written together at the same
    ``valid_utc`` from the same a1 file in
    ``_apply_ecmwf_surface_to_hourly`` / ``_apply_cloud_diagnostics_to_sections``,
    so the two are coupled. Only ECMWF cross-sections / waypoint forecasts are
    touched.

    INVARIANT: every hour written by ``_apply_ecmwf_surface_to_hourly`` must
    also have ``nwp_cloud_diagnostics`` set. This holds because both writes
    run together inside the same ECMWF a1 loop iteration, and ECMWF a1
    always carries cloud-cover fields (cc / lcc / mcc / hcc / ceiling).
    If a future a1 schema ever drops cloud fields,
    ``build_ecmwf_cloud_diagnostics`` returns None and this anchor signal
    will silently miss those steps — surface scalars at those hours would
    then get overwritten by interpolation from neighbouring anchors. Keep
    the two writes coupled, or replace this detector with an explicit
    anchor list passed from the caller.

    Wind speed/direction are interpolated as U/V components (``_lerp_wind``),
    not as scalars. Gust is a window maximum and is held over the covering
    interval rather than linearly interpolated. See findings #6/#7.
    """
    total = 0
    for cs in sections:
        if cs.model.value != "ecmwf":
            continue
        for wf in cs.point_forecasts:
            total += _interp_surface_hourly(wf.hourly)
    for wf in all_forecasts:
        if wf.model.value != "ecmwf":
            continue
        total += _interp_surface_hourly(wf.hourly)

    if total:
        logger.info(
            "ECMWF surface scalars linearly interpolated for %d gap hourly entries",
            total,
        )


def _interp_surface_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Linearly interpolate surface scalars on gap hours between two GRIB anchors.

    Anchors: hours with ``nwp_cloud_diagnostics`` set. Only fills gaps strictly
    between the first and last anchor; hours outside that range keep their
    existing values (Open-Meteo, in practice).
    """
    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    is_anchor = [h.nwp_cloud_diagnostics is not None for h in sorted_hours]
    anchor_indices = [i for i, a in enumerate(is_anchor) if a]
    if len(anchor_indices) < 2:
        return 0

    filled = 0
    for k in range(len(anchor_indices) - 1):
        prev_i = anchor_indices[k]
        next_i = anchor_indices[k + 1]
        if next_i - prev_i <= 1:
            continue  # adjacent anchors, no gap
        prev_h = sorted_hours[prev_i]
        next_h = sorted_hours[next_i]
        span = (next_h.time - prev_h.time).total_seconds()
        if span <= 0:
            continue
        for i in range(prev_i + 1, next_i):
            h = sorted_hours[i]
            frac = (h.time - prev_h.time).total_seconds() / span
            for f in _ECMWF_SURFACE_INSTANT_FIELDS:
                v = _lerp(getattr(prev_h, f), getattr(next_h, f), frac)
                if v is not None:
                    setattr(h, f, v)
            # Wind: interpolate as U/V components, then derive speed + direction.
            ws, wd = _lerp_wind(
                prev_h.wind_speed_10m_kt, prev_h.wind_direction_10m_deg,
                next_h.wind_speed_10m_kt, next_h.wind_direction_10m_deg,
                frac,
            )
            if ws is not None:
                h.wind_speed_10m_kt = ws
            if wd is not None:
                h.wind_direction_10m_deg = wd
            # Gust (10fg) is a window MAXIMUM ("max gust since previous
            # post-processing"), not an instantaneous value — a max is not
            # linearly interpolable. Hold the reported max of the covering
            # interval: the next anchor's window contains this gap hour. (#441 #6)
            if next_h.wind_gusts_10m_kt is not None:
                h.wind_gusts_10m_kt = next_h.wind_gusts_10m_kt
            filled += 1
    return filled


# ---------------------------------------------------------------------------
# Pressure-level sounding linear interp (ECMWF / ICON-EU GRIB replacement)
# ---------------------------------------------------------------------------

def _linear_interp_pressure_levels(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
) -> None:
    """Linearly interpolate the GRIB-replaced sounding across 3-hourly gaps.

    Beyond 90h lead time, ECMWF / ICON-EU step at 3-hourly cadence; past 144h,
    6-hourly. Within the flight window, GRIB replacement covers native steps
    while gap hours retain Open-Meteo data at lower vertical resolution.

    We rebuild ``pressure_levels`` on each gap hour by per-level linear
    interpolation of T, RH, wind speed/direction, geopotential height, vertical
    velocity, CLW/ICMR, and cloud cover. Dewpoint is **derived** from the
    interpolated (T, RH) via the Magnus formula rather than interpolated
    directly — this matches operational meteorology practice and keeps
    derived quantities consistent with primitives.

    Hours outside the flight window (before the first or after the last GRIB
    anchor) keep their existing values.
    """
    total = 0
    for cs in sections:
        for wf in cs.point_forecasts:
            total += _interp_levels_hourly(wf.hourly)
    for wf in all_forecasts:
        total += _interp_levels_hourly(wf.hourly)

    if total:
        logger.info(
            "GRIB sounding linearly interpolated for %d gap hourly entries",
            total,
        )


def _interp_levels_hourly(hourly_list: list[HourlyForecast]) -> int:
    """Linearly interpolate the full pressure_levels list on gap hours.

    Anchor detection mirrors the previous forward-fill: any hour whose
    pressure_levels count exceeds the baseline (Open-Meteo level count) is a
    GRIB-replaced anchor. Only fills gaps strictly between two anchors; hours
    outside the bracketing range are untouched.
    """
    sorted_hours = sorted(hourly_list, key=lambda h: h.time)
    if not sorted_hours:
        return 0

    level_counts = [len(h.pressure_levels) for h in sorted_hours]
    baseline = min(level_counts)
    max_levels = max(level_counts)
    if max_levels <= baseline:
        return 0

    is_anchor = [c > baseline for c in level_counts]
    anchor_indices = [i for i, a in enumerate(is_anchor) if a]
    if len(anchor_indices) < 2:
        return 0

    filled = 0
    for k in range(len(anchor_indices) - 1):
        prev_i = anchor_indices[k]
        next_i = anchor_indices[k + 1]
        if next_i - prev_i <= 1:
            continue
        prev_h = sorted_hours[prev_i]
        next_h = sorted_hours[next_i]
        span = (next_h.time - prev_h.time).total_seconds()
        if span <= 0:
            continue
        # Index next anchor's levels by pressure for matching
        next_by_p = {pl.pressure_hpa: pl for pl in next_h.pressure_levels}
        for i in range(prev_i + 1, next_i):
            h = sorted_hours[i]
            frac = (h.time - prev_h.time).total_seconds() / span
            new_levels = _interp_levels_at(prev_h.pressure_levels, next_by_p, frac)
            if new_levels:
                h.pressure_levels = new_levels
                filled += 1
    return filled


def _interp_levels_at(
    prev_levels: list[PressureLevelData],
    next_by_p: dict[int, PressureLevelData],
    frac: float,
) -> list[PressureLevelData]:
    """Build a new pressure_levels list by per-level linear interp at ``frac``."""
    from weatherbrief.models import PressureLevelData

    out: list[PressureLevelData] = []
    for prev in prev_levels:
        nxt = next_by_p.get(prev.pressure_hpa)
        if nxt is None:
            # Level present in prev anchor but not next — drop it on the gap
            # hour to keep the rebuilt level set self-consistent. Both
            # GRIB-replaced anchors normally carry the same level set; if
            # this branch fires repeatedly, anchor delivery has diverged
            # and is worth investigating.
            continue

        t_c = _lerp(prev.temperature_c, nxt.temperature_c, frac)
        rh = _lerp(prev.relative_humidity_pct, nxt.relative_humidity_pct, frac)
        # Dewpoint: derive from (T, RH) when both available; otherwise fall
        # back to direct linear interp of dewpoint.
        if t_c is not None and rh is not None:
            try:
                td_c = magnus_dewpoint(t_c, rh)
            except (ValueError, ZeroDivisionError):
                td_c = _lerp(prev.dewpoint_c, nxt.dewpoint_c, frac)
        else:
            td_c = _lerp(prev.dewpoint_c, nxt.dewpoint_c, frac)

        # Wind: U/V-component interpolation (see _lerp_wind), not scalar. (#441)
        ws, wd = _lerp_wind(
            prev.wind_speed_kt, prev.wind_direction_deg,
            nxt.wind_speed_kt, nxt.wind_direction_deg,
            frac,
        )

        out.append(PressureLevelData(
            pressure_hpa=prev.pressure_hpa,
            temperature_c=t_c,
            relative_humidity_pct=rh,
            dewpoint_c=td_c,
            wind_speed_kt=ws,
            wind_direction_deg=wd,
            geopotential_height_m=_lerp(prev.geopotential_height_m, nxt.geopotential_height_m, frac),
            vertical_velocity_pa_s=_lerp(prev.vertical_velocity_pa_s, nxt.vertical_velocity_pa_s, frac),
            cloud_liquid_water_kg_kg=_lerp(prev.cloud_liquid_water_kg_kg, nxt.cloud_liquid_water_kg_kg, frac),
            ice_mixing_ratio_kg_kg=_lerp(prev.ice_mixing_ratio_kg_kg, nxt.ice_mixing_ratio_kg_kg, frac),
            cloud_area_fraction_pct=_lerp(prev.cloud_area_fraction_pct, nxt.cloud_area_fraction_pct, frac),
            # ``clw_interpolated`` flags spatial fill, not temporal. Preserve
            # from prev so spatially-filled levels stay flagged across the
            # time-axis interp.
            clw_interpolated=prev.clw_interpolated or nxt.clw_interpolated,
        ))
    return out
