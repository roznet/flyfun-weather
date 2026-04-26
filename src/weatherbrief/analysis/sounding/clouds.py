"""Enhanced cloud layer detection from dewpoint depression profiles.

Uses DerivedLevel data (dewpoint depression, temperature) to identify cloud
layers with coverage classification. Replaces the simple RH-threshold approach
in analysis/clouds.py.
"""

from __future__ import annotations

from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    NWPCloudDiagnostics,
    PressureLevelData,
    ThermodynamicIndices,
)

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


def detect_cloud_layers(
    levels: list[DerivedLevel],
    lcl_altitude_ft: float | None = None,
    dd_threshold: float = IN_CLOUD_DD_THRESHOLD,
) -> list[EnhancedCloudLayer]:
    """Detect cloud layers from derived level dewpoint depression.

    Args:
        levels: Derived levels sorted by descending pressure (surface first).
        lcl_altitude_ft: Optional LCL altitude for convective base annotation.
        dd_threshold: Dewpoint depression threshold for cloud detection.

    Returns:
        List of EnhancedCloudLayer, ordered from lowest to highest.
    """
    if not levels:
        return []

    cloud_layers: list[EnhancedCloudLayer] = []
    in_cloud = False
    cloud_levels: list[DerivedLevel] = []

    for lv in levels:
        if lv.dewpoint_depression_c is None or lv.altitude_ft is None:
            continue

        if lv.dewpoint_depression_c < dd_threshold:
            if not in_cloud:
                in_cloud = True
                cloud_levels = []
            cloud_levels.append(lv)
        elif in_cloud:
            # End of cloud layer
            in_cloud = False
            layer = _build_layer(cloud_levels)
            if layer is not None:
                cloud_layers.append(layer)

    # Handle cloud extending to top of profile
    if in_cloud and cloud_levels:
        layer = _build_layer(cloud_levels)
        if layer is not None:
            cloud_layers.append(layer)

    return cloud_layers


def _build_layer(cloud_levels: list[DerivedLevel]) -> EnhancedCloudLayer | None:
    """Build an EnhancedCloudLayer from a group of consecutive cloud levels."""
    if not cloud_levels:
        return None

    base = cloud_levels[0]
    top = cloud_levels[-1]
    base_ft = base.altitude_ft
    top_ft = top.altitude_ft

    if base_ft is None or top_ft is None:
        return None

    # Mean dewpoint depression and temperature within the layer
    dd_vals = [lv.dewpoint_depression_c for lv in cloud_levels if lv.dewpoint_depression_c is not None]
    t_vals = [lv.temperature_c for lv in cloud_levels if lv.temperature_c is not None]

    mean_dd = sum(dd_vals) / len(dd_vals) if dd_vals else 2.0
    mean_t = round(sum(t_vals) / len(t_vals), 1) if t_vals else None

    return EnhancedCloudLayer(
        base_ft=round(base_ft),
        top_ft=round(top_ft),
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        thickness_ft=round(top_ft - base_ft),
        mean_temperature_c=mean_t,
        coverage=_classify_coverage(mean_dd),
        mean_dewpoint_depression_c=round(mean_dd, 1),
    )


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


def build_nwp_cloud_layers_from_fraction(
    pressure_levels: list[PressureLevelData],
    threshold_pct: float = 12.5,
) -> list[EnhancedCloudLayer] | None:
    """Build cloud layers from per-level model cloud fraction.

    ECMWF IFS delivers ``cc`` and ICON delivers ``clc`` as a full 3D cloud
    fraction (0–100%) at every pressure level. This is strictly richer than
    bulk band percentages: we can extract actual deck base/top from the
    model's own cloud scheme instead of inferring them from RH.

    Algorithm: walk levels surface→TOA, group consecutive levels with
    ``cloud_area_fraction_pct >= threshold_pct`` into a deck, compute
    base/top altitude from ``geopotential_height_m``, and classify coverage
    from the deck's peak fraction.

    Returns None when no level carries ``cloud_area_fraction_pct`` (so the
    caller can fall back to bulk-%/synthesized path for GFS, Open-Meteo).
    Returns an empty list when CAF is present but all levels are below
    threshold (genuinely clear column per the model).
    """
    if not pressure_levels:
        return None

    any_caf = any(lv.cloud_area_fraction_pct is not None for lv in pressure_levels)
    if not any_caf:
        return None

    # Surface → TOA: descending pressure
    sorted_levels = sorted(
        pressure_levels, key=lambda lv: lv.pressure_hpa, reverse=True,
    )

    layers: list[EnhancedCloudLayer] = []
    current: list[PressureLevelData] = []

    def _flush() -> None:
        if not current:
            return
        layer = _build_cc_layer(current)
        if layer is not None:
            layers.append(layer)
        current.clear()

    for lv in sorted_levels:
        caf = lv.cloud_area_fraction_pct
        if (caf is not None and caf >= threshold_pct
                and lv.geopotential_height_m is not None):
            current.append(lv)
        else:
            _flush()

    _flush()
    return layers


def _build_cc_layer(cc_levels: list[PressureLevelData]) -> EnhancedCloudLayer | None:
    """Build one EnhancedCloudLayer from consecutive cc-above-threshold levels."""
    if not cc_levels:
        return None

    base = cc_levels[0]
    top = cc_levels[-1]
    if base.geopotential_height_m is None or top.geopotential_height_m is None:
        return None

    base_ft = base.geopotential_height_m * _M_TO_FT
    top_ft = top.geopotential_height_m * _M_TO_FT

    caf_vals = [lv.cloud_area_fraction_pct for lv in cc_levels
                if lv.cloud_area_fraction_pct is not None]
    t_vals = [lv.temperature_c for lv in cc_levels if lv.temperature_c is not None]
    peak_caf = max(caf_vals) if caf_vals else 0.0
    mean_t = round(sum(t_vals) / len(t_vals), 1) if t_vals else None
    coverage = _nwp_pct_to_coverage(peak_caf) or CloudCoverage.FEW

    return EnhancedCloudLayer(
        base_ft=round(base_ft),
        top_ft=round(top_ft),
        base_pressure_hpa=base.pressure_hpa,
        top_pressure_hpa=top.pressure_hpa,
        thickness_ft=round(top_ft - base_ft),
        mean_temperature_c=mean_t,
        coverage=coverage,
        mean_dewpoint_depression_c=None,
        source="nwp_3d",
    )


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
