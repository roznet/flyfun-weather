"""Enhanced cloud layer detection from dewpoint depression profiles.

Uses DerivedLevel data (dewpoint depression, temperature) to identify cloud
layers with coverage classification. Replaces the simple RH-threshold approach
in analysis/clouds.py.
"""

from __future__ import annotations

import logging
from typing import cast

from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    NWPCloudDiagnostics,
    PressureLevelData,
    ThermodynamicIndices,
)

logger = logging.getLogger(__name__)

_M_TO_FT = 3.28084

# Dewpoint depression threshold for "in cloud" (degrees C)
IN_CLOUD_DD_THRESHOLD = 3.0

# Coverage mapping from mean dewpoint depression within cloud
_COVERAGE_THRESHOLDS = [
    (1.0, CloudCoverage.OVC),
    (2.0, CloudCoverage.BKN),
    (IN_CLOUD_DD_THRESHOLD, CloudCoverage.SCT),
]


def _classify_coverage(mean_dd: float) -> CloudCoverage:
    """Map mean dewpoint depression to cloud coverage category."""
    for threshold, coverage in _COVERAGE_THRESHOLDS:
        if mean_dd < threshold:
            return coverage
    return CloudCoverage.SCT


# Upper DD bound for each METAR coverage category, derived from
# _COVERAGE_THRESHOLDS so the breakpoints stay in one place. Used to find
# the boundary DD when interpolating altitude between two adjacent levels
# of different categories — the boundary is the upper bound of whichever
# endpoint sits in the denser (lower-DD) category.
_DD_CATEGORY_UPPER_BOUNDS: dict[CloudCoverage, float] = {
    coverage: threshold for threshold, coverage in _COVERAGE_THRESHOLDS
}


def _dd_category(
    dd: float | None,
    in_cloud_threshold: float = IN_CLOUD_DD_THRESHOLD,
) -> CloudCoverage | None:
    """Map dewpoint depression to METAR coverage category. Returns None for
    DD ≥ ``in_cloud_threshold`` (clear)."""
    if dd is None or dd >= in_cloud_threshold:
        return None
    return _classify_coverage(dd)


def _dd_crossing_edge(
    a: DerivedLevel,
    b: DerivedLevel,
    a_cat: CloudCoverage,
    b_cat: CloudCoverage | None,
) -> tuple[float, float] | None:
    """``(altitude_ft, pressure_hpa)`` where ``dewpoint_depression_c``
    linearly crosses the boundary DD separating a's and b's coverage
    categories.

    Mirrors ``_crossing_edge`` for the NWP-3D path but on DD: ``a`` is
    always a level inside a deck (so ``a_cat`` non-None and
    ``a_dd < dd_threshold``); ``b`` is the adjacent neighbor — possibly
    clear (``b_cat=None`` and ``b_dd >= dd_threshold``). The denser
    endpoint is the one with the lower DD.

    Both altitude and pressure are interpolated at the same fraction of
    the [a, b] segment, so the returned edge represents one consistent
    point in the sounding — needed by Skew-T (pressure axis) and the
    cross-section (altitude axis) views to render the same deck.

    Returns ``None`` if either endpoint lacks data, the two levels share
    the same DD (no crossing possible), the boundary DD lies outside
    [a_dd, b_dd] (would require extrapolation), or — defensively — the
    deck-membership invariant is violated (should be unreachable).
    """
    a_dd = a.dewpoint_depression_c
    b_dd = b.dewpoint_depression_c
    if (a_dd is None or b_dd is None
            or a.altitude_ft is None
            or b.altitude_ft is None
            or a_dd == b_dd):
        return None

    # Denser endpoint = lower DD. ``a`` is always in a real category, so
    # when ``a_dd <= b_dd`` the denser cat is ``a_cat``. When ``a_dd > b_dd``,
    # ``b`` is denser — and since ``a`` already has DD < dd_threshold,
    # ``b`` has even smaller DD and therefore also a non-None category.
    denser_cat = a_cat if a_dd <= b_dd else b_cat
    if denser_cat is None:
        # Defensive — invariant above says this is unreachable, but `assert`
        # would crash the whole sounding analysis on a single bad deck and
        # is also stripped under `python -O`. Treat as "no usable crossing".
        return None
    target = _DD_CATEGORY_UPPER_BOUNDS[denser_cat]

    frac = (target - a_dd) / (b_dd - a_dd)
    if not 0.0 <= frac <= 1.0:
        return None
    altitude_ft = a.altitude_ft + frac * (b.altitude_ft - a.altitude_ft)
    pressure_hpa = a.pressure_hpa + frac * (b.pressure_hpa - a.pressure_hpa)
    return (altitude_ft, pressure_hpa)


def _dd_midpoint_edge(
    inner: DerivedLevel,
    outer: DerivedLevel | None,
) -> tuple[float, float] | None:
    """Edge ``(altitude_ft, pressure_hpa)`` when threshold-crossing isn't usable.

    Two behaviours, both intentional:
      - ``outer`` present → midpoint between the deck-edge level and its
        neighbor (altitude AND pressure both averaged at frac=0.5).
      - ``outer is None`` (column floor / TOA) → the deck-edge level's
        own altitude/pressure, since there's no neighbor to interpolate.
    """
    if inner.altitude_ft is None:
        return None
    if outer is None or outer.altitude_ft is None:
        return (inner.altitude_ft, float(inner.pressure_hpa))
    altitude_ft = (inner.altitude_ft + outer.altitude_ft) / 2.0
    pressure_hpa = (inner.pressure_hpa + outer.pressure_hpa) / 2.0
    return (altitude_ft, pressure_hpa)


def _dd_layer_edge_below(
    levels: list[DerivedLevel],
    cats: list[CloudCoverage | None],
    i: int,
    cat: CloudCoverage,
) -> tuple[float, float] | None:
    """``(altitude_ft, pressure_hpa)`` of the lower edge of the run starting
    at index i."""
    if i > 0:
        edge = _dd_crossing_edge(levels[i], levels[i - 1], cat, cats[i - 1])
        if edge is not None:
            return edge
    return _dd_midpoint_edge(levels[i], levels[i - 1] if i > 0 else None)


def _dd_layer_edge_above(
    levels: list[DerivedLevel],
    cats: list[CloudCoverage | None],
    j: int,
    cat: CloudCoverage,
) -> tuple[float, float] | None:
    """``(altitude_ft, pressure_hpa)`` of the upper edge of the run ending
    at index j."""
    n = len(levels)
    if j + 1 < n:
        edge = _dd_crossing_edge(levels[j], levels[j + 1], cat, cats[j + 1])
        if edge is not None:
            return edge
    return _dd_midpoint_edge(levels[j], levels[j + 1] if j + 1 < n else None)


def detect_cloud_layers(
    levels: list[DerivedLevel],
    lcl_altitude_ft: float | None = None,
    dd_threshold: float = IN_CLOUD_DD_THRESHOLD,
) -> list[EnhancedCloudLayer]:
    """Detect cloud layers from derived-level dewpoint depression.

    Walks levels surface→TOA and groups consecutive levels by their METAR
    coverage category derived from DD (SCT/BKN/OVC). A category change
    starts a new layer; levels with DD ≥ ``dd_threshold`` (≥ 3.0 K by
    default) split layers (clear-air gaps).

    Each layer's base/top altitudes come from linear threshold-crossing on
    ``dewpoint_depression_c`` against the boundary DD separating the layer
    from its (different-category) neighbor — a moisture-defined edge
    instead of pinning to a level altitude. Half-pressure attribution
    falls back at the column ends.

    Mirrors ``build_nwp_cloud_layers_from_fraction`` for the NWP-3D path,
    just on the DD field with three categories instead of four (DD has no
    FEW analog; lightest in-cloud class is SCT).

    Args:
        levels: Derived levels sorted by descending pressure (surface first).
        lcl_altitude_ft: Accepted for API stability with the previous
            implementation (currently unused).
        dd_threshold: DD threshold for "in cloud". Defaults to 3.0 K.

    Returns:
        List of EnhancedCloudLayer, ordered from lowest to highest.
    """
    del lcl_altitude_ft  # unused — kept for backward compat
    if not levels:
        return []

    # Surface → TOA: descending pressure. Defensive sort (input is already
    # ordered, but mirrors the NWP-3D path).
    sorted_levels = sorted(levels, key=lambda lv: lv.pressure_hpa, reverse=True)
    cats: list[CloudCoverage | None] = []
    for lv in sorted_levels:
        if lv.dewpoint_depression_c is None or lv.altitude_ft is None:
            cats.append(None)
        else:
            cats.append(_dd_category(lv.dewpoint_depression_c, dd_threshold))

    cloud_layers: list[EnhancedCloudLayer] = []
    n = len(sorted_levels)
    i = 0
    while i < n:
        cat = cats[i]
        if cat is None:
            i += 1
            continue
        # Run [i..j] of identical category.
        j = i
        while j + 1 < n and cats[j + 1] == cat:
            j += 1

        base_edge = _dd_layer_edge_below(sorted_levels, cats, i, cat)
        top_edge = _dd_layer_edge_above(sorted_levels, cats, j, cat)
        if base_edge is None or top_edge is None or top_edge[0] <= base_edge[0]:
            logger.debug(
                "Dropping DD cloud deck at run [%d..%d] (cat=%s): "
                "base_edge=%s top_edge=%s — degenerate layer geometry",
                i, j, cat, base_edge, top_edge,
            )
            i = j + 1
            continue

        base_ft, base_pressure_hpa = base_edge
        top_ft, top_pressure_hpa = top_edge

        run = sorted_levels[i:j + 1]
        # Every level in the run has DD + altitude (cats[k] non-None requires
        # both). ``cast`` makes the runtime invariant visible to the type
        # checker.
        dd_vals = cast("list[float]", [lv.dewpoint_depression_c for lv in run])
        t_vals = [lv.temperature_c for lv in run if lv.temperature_c is not None]
        mean_dd = sum(dd_vals) / len(dd_vals)
        mean_t = round(sum(t_vals) / len(t_vals), 1) if t_vals else None

        cloud_layers.append(EnhancedCloudLayer(
            base_ft=round(base_ft),
            top_ft=round(top_ft),
            base_pressure_hpa=round(base_pressure_hpa),
            top_pressure_hpa=round(top_pressure_hpa),
            thickness_ft=round(top_ft - base_ft),
            mean_temperature_c=mean_t,
            coverage=cat,
            mean_dewpoint_depression_c=round(mean_dd, 1),
        ))
        i = j + 1

    return cloud_layers


# Coverage mapping from NWP cloud cover percentage (standard METAR oktas)
_NWP_COVERAGE_THRESHOLDS = [
    (87.5, CloudCoverage.OVC),  # 7-8 oktas
    (50.0, CloudCoverage.BKN),  # 5-6 oktas
    (25.0, CloudCoverage.SCT),  # 3-4 oktas
    (12.5, CloudCoverage.FEW),  # 1-2 oktas
]


def _nwp_pct_to_coverage(pct: float) -> CloudCoverage | None:
    """Map NWP cloud cover percentage to METAR coverage category.

    Returns None for sub-FEW coverage (<12.5%) — essentially clear sky.
    """
    for threshold, coverage in _NWP_COVERAGE_THRESHOLDS:
        if pct >= threshold:
            return coverage
    return None


# Lower CAF % bound for each METAR coverage category, derived from
# _NWP_COVERAGE_THRESHOLDS so the breakpoints stay in one place. Used to
# find the boundary CAF when interpolating altitude between two adjacent
# levels of different categories — the boundary is the lower bound of
# whichever endpoint sits in the higher category.
_NWP_CATEGORY_LOWER_BOUNDS: dict[CloudCoverage, float] = {
    coverage: threshold for threshold, coverage in _NWP_COVERAGE_THRESHOLDS
}


def _crossing_edge(
    a: PressureLevelData,
    b: PressureLevelData,
    a_cat: CloudCoverage,
    b_cat: CloudCoverage | None,
) -> tuple[float, float] | None:
    """``(altitude_ft, pressure_hpa)`` where ``cloud_area_fraction_pct``
    linearly crosses the boundary CAF separating a's and b's coverage
    categories.

    ``a`` is always a level inside a deck (so ``a_cat`` is non-None and
    ``a_caf >= 12.5 %``); ``b`` is the adjacent neighbor — possibly clear
    (``b_cat=None`` and ``b_caf < 12.5 %``). Used to anchor a deck's
    base/top to the model's own cloud field instead of pinning to a level
    altitude.

    Both altitude and pressure are interpolated at the same fraction of
    the [a, b] segment, so the returned edge represents one consistent
    point in the sounding — needed by Skew-T (pressure axis) and the
    cross-section (altitude axis) views to render the same deck.

    Returns ``None`` if either endpoint lacks data, the two levels share
    the same CAF (no crossing possible), the boundary CAF lies outside
    [a_caf, b_caf] (would require extrapolation), or — defensively — the
    deck-membership invariant is violated (should be unreachable).
    """
    a_caf = a.cloud_area_fraction_pct
    b_caf = b.cloud_area_fraction_pct
    if (a_caf is None or b_caf is None
            or a.geopotential_height_m is None
            or b.geopotential_height_m is None
            or a_caf == b_caf):
        return None

    # ``a`` is always in a real category (caller guarantees ``a_cat`` non-None
    # via the deck-membership check), so when ``a_caf >= b_caf`` the higher
    # category is ``a_cat``. When ``a_caf < b_caf``, ``b`` is in a denser
    # category — and since ``a`` is at least FEW (caf ≥ 12.5 %), ``b`` must
    # also be ≥ 12.5 % and therefore have a non-None category too.
    higher_cat = a_cat if a_caf >= b_caf else b_cat
    if higher_cat is None:
        # Defensive — invariant above says this is unreachable, but `assert`
        # would crash the whole sounding analysis on a single bad deck and
        # is also stripped under `python -O`. Treat as "no usable crossing".
        return None
    target = _NWP_CATEGORY_LOWER_BOUNDS[higher_cat]

    frac = (target - a_caf) / (b_caf - a_caf)
    if not 0.0 <= frac <= 1.0:
        return None
    a_ft = a.geopotential_height_m * _M_TO_FT
    b_ft = b.geopotential_height_m * _M_TO_FT
    altitude_ft = a_ft + frac * (b_ft - a_ft)
    pressure_hpa = a.pressure_hpa + frac * (b.pressure_hpa - a.pressure_hpa)
    return (altitude_ft, pressure_hpa)


def _midpoint_edge(
    inner: PressureLevelData,
    outer: PressureLevelData | None,
) -> tuple[float, float] | None:
    """Edge ``(altitude_ft, pressure_hpa)`` when threshold-crossing isn't usable.

    Two behaviours, both intentional:
      - ``outer`` present → midpoint between the deck-edge level and its
        neighbor (altitude AND pressure both averaged at frac=0.5).
      - ``outer is None`` (column floor / TOA) → the deck-edge level's
        own altitude/pressure, since there's no neighbor to interpolate.
    """
    if inner.geopotential_height_m is None:
        return None
    inner_ft = inner.geopotential_height_m * _M_TO_FT
    if outer is None or outer.geopotential_height_m is None:
        return (inner_ft, float(inner.pressure_hpa))
    altitude_ft = (inner_ft + outer.geopotential_height_m * _M_TO_FT) / 2.0
    pressure_hpa = (inner.pressure_hpa + outer.pressure_hpa) / 2.0
    return (altitude_ft, pressure_hpa)


def _layer_edge_below(
    levels: list[PressureLevelData],
    cats: list[CloudCoverage | None],
    i: int,
    cat: CloudCoverage,
) -> tuple[float, float] | None:
    """``(altitude_ft, pressure_hpa)`` of the lower edge of the run starting
    at index i."""
    if i > 0:
        edge = _crossing_edge(levels[i], levels[i - 1], cat, cats[i - 1])
        if edge is not None:
            return edge
    return _midpoint_edge(levels[i], levels[i - 1] if i > 0 else None)


def _layer_edge_above(
    levels: list[PressureLevelData],
    cats: list[CloudCoverage | None],
    j: int,
    cat: CloudCoverage,
) -> tuple[float, float] | None:
    """``(altitude_ft, pressure_hpa)`` of the upper edge of the run ending
    at index j."""
    n = len(levels)
    if j + 1 < n:
        edge = _crossing_edge(levels[j], levels[j + 1], cat, cats[j + 1])
        if edge is not None:
            return edge
    return _midpoint_edge(levels[j], levels[j + 1] if j + 1 < n else None)


def build_nwp_cloud_layers_from_fraction(
    pressure_levels: list[PressureLevelData],
) -> list[EnhancedCloudLayer] | None:
    """Build cloud layers from per-level model cloud fraction.

    ECMWF IFS delivers ``cc`` and ICON delivers ``clc`` as a full 3D cloud
    fraction (0–100%) at every pressure level. This is strictly richer than
    bulk band percentages: we can extract actual deck base/top from the
    model's own cloud scheme instead of inferring them from RH.

    Algorithm: walk levels surface→TOA and group consecutive levels by
    their METAR coverage category (FEW/SCT/BKN/OVC). A category change
    starts a new layer; sub-FEW (<12.5 %) levels and levels missing CAF or
    geopotential height split layers (clear-air gaps).

    Each layer's base/top altitudes come from linear threshold-crossing on
    ``cloud_area_fraction_pct`` against the boundary CAF separating the
    layer from its (different-category) neighbor — a model-derived edge
    instead of pinning to a level altitude. Half-pressure attribution
    falls back at the column ends.

    Returns ``None`` when no level carries ``cloud_area_fraction_pct``
    (caller falls back to bulk-%/synthesized path for GFS, Open-Meteo).
    Returns an empty list when CAF is present but every level is sub-FEW
    (genuinely clear column per the model).
    """
    if not pressure_levels:
        return None
    if not any(lv.cloud_area_fraction_pct is not None for lv in pressure_levels):
        return None

    # Surface → TOA: descending pressure.
    levels = sorted(pressure_levels, key=lambda lv: lv.pressure_hpa, reverse=True)
    cats: list[CloudCoverage | None] = []
    for lv in levels:
        if lv.cloud_area_fraction_pct is None or lv.geopotential_height_m is None:
            cats.append(None)
        else:
            cats.append(_nwp_pct_to_coverage(lv.cloud_area_fraction_pct))

    layers: list[EnhancedCloudLayer] = []
    n = len(levels)
    i = 0
    while i < n:
        cat = cats[i]
        if cat is None:
            i += 1
            continue
        # Run [i..j] of identical category.
        j = i
        while j + 1 < n and cats[j + 1] == cat:
            j += 1

        base_edge = _layer_edge_below(levels, cats, i, cat)
        top_edge = _layer_edge_above(levels, cats, j, cat)
        if base_edge is None or top_edge is None or top_edge[0] <= base_edge[0]:
            # Should not happen with well-formed sounding data: a category run
            # always has at least one level with valid CAF + geopotential, and
            # well-ordered geopotential should give top > base. Logged so a
            # pathological GRIB (e.g. inverted geopotential) doesn't silently
            # disappear from the cross-section.
            logger.warning(
                "Dropping NWP-3D cloud deck at run [%d..%d] (cat=%s): "
                "base_edge=%s top_edge=%s — degenerate layer geometry",
                i, j, cat, base_edge, top_edge,
            )
            i = j + 1
            continue

        base_ft, base_pressure_hpa = base_edge
        top_ft, top_pressure_hpa = top_edge

        run = levels[i:j + 1]
        # Every level in the run is guaranteed to carry CAF + geopotential
        # (cats[k] is non-None only when both are present), so caf_vals and
        # base/top altitudes are always populated here. ``cast`` makes the
        # runtime invariant visible to the type checker.
        caf_vals = cast("list[float]", [lv.cloud_area_fraction_pct for lv in run])
        t_vals = [lv.temperature_c for lv in run if lv.temperature_c is not None]
        mean_caf = sum(caf_vals) / len(caf_vals)
        mean_t = round(sum(t_vals) / len(t_vals), 1) if t_vals else None

        layers.append(EnhancedCloudLayer(
            base_ft=round(base_ft),
            top_ft=round(top_ft),
            base_pressure_hpa=round(base_pressure_hpa),
            top_pressure_hpa=round(top_pressure_hpa),
            thickness_ft=round(top_ft - base_ft),
            mean_temperature_c=mean_t,
            coverage=cat,
            mean_dewpoint_depression_c=None,
            mean_cloud_cover_pct=round(mean_caf, 1),
            source="nwp_3d",
        ))
        i = j + 1

    return layers


def build_nwp_cloud_layers(
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
    *,
    pressure_levels: list[PressureLevelData] | None = None,
) -> list[EnhancedCloudLayer] | None:
    """Build cloud layers from native NWP sources only.

    Two sources, tried in order of richness:
      1. **Per-level 3D cloud fraction** (ECMWF ``cc`` / ICON ``clc``)
         → ``source="nwp_3d"`` — real deck base/top from the model's own
         cloud scheme.
      2. **GRIB diagnostics** with base/top boundaries (GFS) → ``source="grib"``

    Returns ``None`` when neither source is available — the model has no
    native NWP cloud envelope. Callers must treat this as "no NWP layer
    data," not "model says clear sky."

    Returns an empty list when a native source is available but no level
    exceeded the threshold (genuine clear-sky forecast).

    Open-Meteo bulk ``cloud_cover_low/mid/high_pct`` are intentionally NOT
    used here: synthesizing layers from bulk percentages narrowed by DD
    evidence produces a hybrid that isn't really a model-native layer, and
    consumers downstream (Ogimet-NWP / IENG icing, the cross-section NWP
    toggle) need a clean "native or absent" signal.
    """
    if pressure_levels:
        layers_3d = build_nwp_cloud_layers_from_fraction(pressure_levels)
        if layers_3d is not None:
            return layers_3d

    return _build_grib_layers(
        nwp_cloud_diagnostics, nwp_cloud_low_pct, nwp_cloud_mid_pct, nwp_cloud_high_pct,
    )


def _build_grib_layers(
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None,
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_high_pct: float | None,
) -> list[EnhancedCloudLayer] | None:
    """Build layers from GRIB2 diagnostics with explicit base/top.

    Returns None if diagnostics are absent or no band has boundaries.
    """
    if nwp_cloud_diagnostics is None:
        return None

    layers: list[EnhancedCloudLayer] = []
    has_usable_band = False

    bands = [
        (nwp_cloud_diagnostics.low, nwp_cloud_low_pct),
        (nwp_cloud_diagnostics.mid, nwp_cloud_mid_pct),
        (nwp_cloud_diagnostics.high, nwp_cloud_high_pct),
    ]

    for diag, fallback_pct in bands:
        if diag.base_ft is None or diag.top_ft is None:
            continue
        has_usable_band = True

        cover_pct = diag.cover_pct if diag.cover_pct is not None else fallback_pct
        if cover_pct is None or cover_pct <= 0:
            continue

        coverage = _nwp_pct_to_coverage(cover_pct)
        if coverage is None:
            continue  # sub-FEW (<12.5%) — essentially clear

        layers.append(EnhancedCloudLayer(
            base_ft=round(diag.base_ft),
            top_ft=round(diag.top_ft),
            coverage=coverage,
            mean_temperature_c=diag.top_temp_c,
            mean_dewpoint_depression_c=None,
            mean_cloud_cover_pct=round(cover_pct, 1) if cover_pct is not None else None,
            source="grib",
        ))

    # Convective layer
    diag_root = nwp_cloud_diagnostics
    if (diag_root.convective_base_ft is not None
            and diag_root.convective_top_ft is not None):
        has_usable_band = True
        conv_pct = diag_root.convective_cover_pct
        if conv_pct is not None and conv_pct > 0:
            conv_cov = _nwp_pct_to_coverage(conv_pct)
            if conv_cov is not None:
                layers.append(EnhancedCloudLayer(
                    base_ft=round(diag_root.convective_base_ft),
                    top_ft=round(diag_root.convective_top_ft),
                    coverage=conv_cov,
                    mean_dewpoint_depression_c=None,
                    mean_cloud_cover_pct=round(conv_pct, 1),
                    source="grib",
                ))

    if not has_usable_band:
        return None

    layers.sort(key=lambda lyr: lyr.base_ft)
    return layers


# ── ICAO band constants used by apply_nwp_coverage ─────────────────


# ICAO altitude band boundaries (ft)
_LOW_TOP_FT = 6500
_MID_TOP_FT = 20000
_HIGH_TOP_FT = 45000

# Minimum NWP cloud cover (%) below which a band segment is treated as clear.
# 12.5% ≈ FEW (1-2 oktas).
_MIN_COVER_PCT = 12.5


def apply_nwp_coverage(
    dd_layers: list[EnhancedCloudLayer],
    nwp_cloud_low_pct: float | None,
    nwp_cloud_mid_pct: float | None,
    nwp_cloud_high_pct: float | None,
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None = None,
) -> list[EnhancedCloudLayer]:
    """Reclassify DD cloud layer coverage using NWP cloud percentages per ICAO band.

    DD detection identifies cloud vertical extent well but classifies coverage
    purely from dewpoint depression, which overestimates when the boundary layer
    is moist but the model's own cloud scheme says little cloud (e.g. ECMWF
    cloud_low=20% but DD < 3°C throughout → DD says OVC, should be SCT).

    This function constrains DD coverage to the NWP model's cloud percentage
    for the matching ICAO altitude band (low < 6500ft, mid 6500-20000ft,
    high 20000-45000ft).  Layers spanning multiple bands are split.

    GRIB diagnostics percentages (``nwp_cloud_diagnostics``) are preferred
    over Open-Meteo percentages when available, as they come directly from
    the model output and are more accurate.

    Segments where NWP says < 12.5% (FEW/trace) are dropped.
    If no NWP percentage is available for a band, DD coverage is kept.
    """
    if not dd_layers:
        return []

    # Prefer GRIB diagnostics cover_pct when available, fall back to Open-Meteo
    low_pct = nwp_cloud_low_pct
    mid_pct = nwp_cloud_mid_pct
    high_pct = nwp_cloud_high_pct
    if nwp_cloud_diagnostics is not None:
        if nwp_cloud_diagnostics.low.cover_pct is not None:
            low_pct = nwp_cloud_diagnostics.low.cover_pct
        if nwp_cloud_diagnostics.mid.cover_pct is not None:
            mid_pct = nwp_cloud_diagnostics.mid.cover_pct
        if nwp_cloud_diagnostics.high.cover_pct is not None:
            high_pct = nwp_cloud_diagnostics.high.cover_pct

    # ICAO band boundaries and their NWP percentages
    bands: list[tuple[float, float, float | None]] = [
        (0, _LOW_TOP_FT, low_pct),
        (_LOW_TOP_FT, _MID_TOP_FT, mid_pct),
        (_MID_TOP_FT, _HIGH_TOP_FT, high_pct),
    ]

    result: list[EnhancedCloudLayer] = []

    for layer in dd_layers:
        segments = _split_layer_by_bands(layer, bands)
        result.extend(segments)

    result.sort(key=lambda lyr: lyr.base_ft)
    return result


def _split_layer_by_bands(
    layer: EnhancedCloudLayer,
    bands: list[tuple[float, float, float | None]],
) -> list[EnhancedCloudLayer]:
    """Split a DD layer by ICAO bands and reclassify each segment's coverage."""
    segments: list[EnhancedCloudLayer] = []

    for band_floor, band_ceiling, nwp_pct in bands:
        # Compute overlap between layer and band
        seg_base = max(layer.base_ft, band_floor)
        seg_top = min(layer.top_ft, band_ceiling)
        if seg_base >= seg_top and not (seg_base == seg_top == layer.base_ft == layer.top_ft
                                        and band_floor <= layer.base_ft < band_ceiling):
            # No overlap (special-case: zero-thickness layer at band boundary)
            continue

        if nwp_pct is not None:
            if nwp_pct < _MIN_COVER_PCT:
                continue  # NWP says trace/no cloud in this band — drop segment
            coverage = _nwp_pct_to_coverage(nwp_pct)
        else:
            # No NWP data for this band — keep DD coverage
            coverage = layer.coverage

        segments.append(EnhancedCloudLayer(
            base_ft=round(seg_base),
            top_ft=round(seg_top),
            base_pressure_hpa=layer.base_pressure_hpa if seg_base == layer.base_ft else None,
            top_pressure_hpa=layer.top_pressure_hpa if seg_top == layer.top_ft else None,
            thickness_ft=round(seg_top - seg_base),
            mean_temperature_c=layer.mean_temperature_c,
            coverage=coverage,
            mean_dewpoint_depression_c=layer.mean_dewpoint_depression_c,
            source=layer.source,
            theoretical_max_top_ft=layer.theoretical_max_top_ft,
        ))

    # Portion above HIGH_TOP_FT — keep DD coverage unchanged
    if layer.top_ft > _HIGH_TOP_FT:
        seg_base = max(layer.base_ft, _HIGH_TOP_FT)
        segments.append(EnhancedCloudLayer(
            base_ft=round(seg_base),
            top_ft=round(layer.top_ft),
            base_pressure_hpa=None,
            top_pressure_hpa=layer.top_pressure_hpa,
            thickness_ft=round(layer.top_ft - seg_base),
            mean_temperature_c=layer.mean_temperature_c,
            coverage=layer.coverage,
            mean_dewpoint_depression_c=layer.mean_dewpoint_depression_c,
            source=layer.source,
            theoretical_max_top_ft=layer.theoretical_max_top_ft,
        ))

    return segments


def enrich_cloud_top_uncertainty(
    cloud_layers: list[EnhancedCloudLayer],
    indices: ThermodynamicIndices,
    cape_jkg: float | None,
) -> None:
    """Add theoretical max cloud top to each layer (in-place).

    Uses EL for convective conditions (CAPE > 500) or −20°C level for stratiform.
    Only sets theoretical_max_top_ft when it exceeds the sounding-derived top.
    """
    if not cloud_layers:
        return

    for layer in cloud_layers:
        theoretical_max: float | None = None

        if cape_jkg is not None and cape_jkg > 500 and indices.el_altitude_ft is not None:
            theoretical_max = indices.el_altitude_ft
        elif indices.minus20c_level_ft is not None:
            theoretical_max = indices.minus20c_level_ft

        if theoretical_max is not None and theoretical_max > layer.top_ft:
            layer.theoretical_max_top_ft = round(theoretical_max)
