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
    ThermodynamicIndices,
)

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
]


def _nwp_pct_to_coverage(pct: float) -> CloudCoverage:
    """Map NWP cloud cover percentage to METAR coverage category."""
    for threshold, coverage in _NWP_COVERAGE_THRESHOLDS:
        if pct >= threshold:
            return coverage
    return CloudCoverage.SCT


def build_nwp_cloud_layers(
    nwp_cloud_diagnostics: NWPCloudDiagnostics | None,
    nwp_cloud_low_pct: float | None = None,
    nwp_cloud_mid_pct: float | None = None,
    nwp_cloud_high_pct: float | None = None,
) -> list[EnhancedCloudLayer] | None:
    """Build cloud layers from NWP diagnostics.

    Returns None if insufficient data (no diagnostics, or no band has
    both base_ft and top_ft). Returns an empty list if diagnostics exist
    but all bands report zero coverage.
    """
    if nwp_cloud_diagnostics is None:
        return None

    layers: list[EnhancedCloudLayer] = []
    has_usable_band = False

    # Map band names to their diagnostics and fallback cover percentages
    bands = [
        (nwp_cloud_diagnostics.low, nwp_cloud_low_pct),
        (nwp_cloud_diagnostics.mid, nwp_cloud_mid_pct),
        (nwp_cloud_diagnostics.high, nwp_cloud_high_pct),
    ]

    for diag, fallback_pct in bands:
        if diag.base_ft is None or diag.top_ft is None:
            continue
        has_usable_band = True

        # Use the diagnostics cover_pct if available, else fall back to Open-Meteo
        cover_pct = diag.cover_pct if diag.cover_pct is not None else fallback_pct
        if cover_pct is None or cover_pct <= 0:
            continue

        layers.append(EnhancedCloudLayer(
            base_ft=round(diag.base_ft),
            top_ft=round(diag.top_ft),
            coverage=_nwp_pct_to_coverage(cover_pct),
            mean_temperature_c=diag.top_temp_c,
            mean_dewpoint_depression_c=None,
        ))

    # Convective layer
    diag_root = nwp_cloud_diagnostics
    if (diag_root.convective_base_ft is not None
            and diag_root.convective_top_ft is not None):
        has_usable_band = True
        conv_pct = diag_root.convective_cover_pct
        if conv_pct is not None and conv_pct > 0:
            layers.append(EnhancedCloudLayer(
                base_ft=round(diag_root.convective_base_ft),
                top_ft=round(diag_root.convective_top_ft),
                coverage=_nwp_pct_to_coverage(conv_pct),
                mean_dewpoint_depression_c=None,
            ))

    if not has_usable_band:
        return None  # Insufficient diagnostics (e.g. ICON-EU pattern)

    # Sort by base altitude
    layers.sort(key=lambda lyr: lyr.base_ft)
    return layers


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
