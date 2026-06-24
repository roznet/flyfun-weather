"""Convective character advisory — VFR avoidability of route convection.

A second axis, orthogonal to the severity-grading ``convective`` advisory
(issue #294). Severity owns the colour for a big cell; this advisory grades
whether the convection is circumnavigable VFR:

    NONE → GREEN;  ISOLATED / SCATTERED → AMBER;
    WIDESPREAD / ORGANIZED / EMBEDDED → RED.

It never changes the severity advisory. Per model, because the realized-coverage
signals (showers, NWP cover/geometry) and K/TT are all per model — and model
disagreement on avoidability is itself signal.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import format_extent, showers_at_point
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.convective import (
    CHAR_COVER_REALIZED_PCT,
    ConvCharPoint,
    classify_convective_character,
)
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    CloudCoverage,
    ConvectiveCharacter,
    ConvectiveRisk,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    SoundingAnalysis,
    VerticalMotionClass,
)

# Severity order — character only considers points at/above the min risk.
_RISK_ORDER = [
    ConvectiveRisk.NONE,
    ConvectiveRisk.MARGINAL,
    ConvectiveRisk.LOW,
    ConvectiveRisk.MODERATE,
    ConvectiveRisk.HIGH,
    ConvectiveRisk.EXTREME,
]

# Character → advisory status. Severity owns RED for the cell itself; here RED
# means "not circumnavigable VFR", AMBER means "avoidable but committing".
_CHAR_STATUS: dict[ConvectiveCharacter, AdvisoryStatus] = {
    ConvectiveCharacter.NONE: AdvisoryStatus.GREEN,
    ConvectiveCharacter.UNKNOWN: AdvisoryStatus.GREEN,
    ConvectiveCharacter.ISOLATED: AdvisoryStatus.AMBER,
    ConvectiveCharacter.SCATTERED: AdvisoryStatus.AMBER,
    ConvectiveCharacter.WIDESPREAD: AdvisoryStatus.RED,
    ConvectiveCharacter.ORGANIZED: AdvisoryStatus.RED,
    ConvectiveCharacter.EMBEDDED: AdvisoryStatus.RED,
}

# Bulk low/mid cover (%) above which a BKN/OVC layer is treated as a deck the
# cells hide in (rather than just the cell's own cumulus).
_EMBED_DECK_COVER_PCT = 60.0


def _point_embedded(sounding: SoundingAnalysis, cruise_ft: float) -> bool:
    """True when a convective point sits under a stratiform deck (can't see cells).

    Requires a BKN/OVC layer based below cruise, corroborated by high bulk
    low/mid cover (a deck, not the cell's own cu). When bulk cover is absent
    (e.g. ECMWF via Open-Meteo), require OVC as the stronger standalone evidence.
    """
    bkn_ovc_below = [
        cl
        for cl in sounding.cloud_layers
        if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC) and cl.base_ft < cruise_ft
    ]
    if not bkn_ovc_below:
        return False
    covers = [
        c
        for c in (sounding.cloud_cover_low_pct, sounding.cloud_cover_mid_pct)
        if c is not None
    ]
    if covers:
        return max(covers) >= _EMBED_DECK_COVER_PCT
    return any(cl.coverage == CloudCoverage.OVC for cl in bkn_ovc_below)


def _front_present(ctx: RouteContext, model: str) -> bool:
    """Coarse: does this model have any detected front crossing on the route?

    Only relabels a *widespread* band as ORGANIZED (both RED), so a coarse read
    is safe — it never turns an avoidable band red on its own.
    """
    fronts = ctx.route_fronts
    if fronts is None:
        return False
    analyses = fronts.per_model.get(model)
    if not analyses:
        return False
    return any(getattr(a, "crossings", None) for a in analyses)


def _native_or_metpy(sounding: SoundingAnalysis) -> tuple[float | None, float | None]:
    """(K-index, Total Totals), preferring model-native (ECMWF kx/totalx)."""
    idx = sounding.indices
    if idx is None:
        return None, None
    k = idx.nwp_k_index if idx.nwp_k_index is not None else idx.k_index
    tt = idx.nwp_total_totals if idx.nwp_total_totals is not None else idx.total_totals
    return k, tt


@register
class ConvectiveCharacterEvaluator:
    """Grades the VFR-avoidability character of route convection, per model."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id="convective_character",
            name="Convective Character",
            short_description="Is convection circumnavigable VFR?",
            description=(
                "Classifies the character of route convection on a VFR-avoidability "
                "axis, independent of severity. Isolated/scattered cells in clear air "
                "(circumnavigable with see-and-avoid) grade AMBER; widespread, "
                "organized/frontal, or embedded convection (no reliable gaps, or cells "
                "hidden in cloud) grade RED. Does NOT change the Convective Activity "
                "(severity) advisory — a big cell still grades RED there regardless."
            ),
            category="convective",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="min_risk",
                    label="Min risk level",
                    description="Minimum severity that counts as a cell (3=MODERATE, 4=HIGH)",
                    type="number",
                    default=3,
                    min=2,
                    max=4,
                    step=1,
                ),
                AdvisoryParameterDef(
                    key="showers_mm",
                    label="Showers threshold",
                    description="Convective precip (mm) at a point to count it as a realized cell",
                    type="number",
                    unit="mm",
                    default=0.1,
                    min=0,
                    max=2,
                    step=0.1,
                ),
                AdvisoryParameterDef(
                    key="isolated_max_pct",
                    label="Isolated max %",
                    description="Realized coverage at/below this is isolated",
                    type="percent",
                    unit="%",
                    default=15,
                    min=5,
                    max=40,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="scattered_max_pct",
                    label="Scattered max %",
                    description="Realized coverage at/below this is scattered; above is widespread",
                    type="percent",
                    unit="%",
                    default=40,
                    min=20,
                    max=80,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="organized_shear_kt",
                    label="Organized shear (kt)",
                    description="0-6km shear at/above which widespread convection is organized",
                    type="number",
                    unit="kt",
                    default=35,
                    min=20,
                    max=60,
                    step=5,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        min_risk_idx = int(params.get("min_risk", 3))
        showers_mm = params.get("showers_mm", 0.1)
        isolated_max_pct = params.get("isolated_max_pct", 15)
        scattered_max_pct = params.get("scattered_max_pct", 40)
        organized_shear_kt = params.get("organized_shear_kt", 35)
        cruise_ft = ctx.cruise_altitude_ft
        loc = ctx.locale

        per_model: list[ModelAdvisoryResult] = []

        for model in ctx.models:
            points: list[ConvCharPoint] = []
            shear_max: float | None = None
            synoptic_ascent = False

            for rpa in ctx.analyses:
                sounding = rpa.sounding.get(model)
                if sounding is None:
                    continue

                conv = sounding.convective
                is_conv = (
                    conv is not None
                    and _RISK_ORDER.index(conv.risk_level) >= min_risk_idx
                )

                realized = False
                embedded = False
                if is_conv:
                    showers = showers_at_point(
                        ctx.cross_sections, model, rpa.point_index, rpa.forecast_hour
                    )
                    nwp = sounding.convective_nwp
                    cover = nwp.cover_pct if nwp is not None else None
                    has_geom = (
                        nwp is not None
                        and nwp.base_ft is not None
                        and nwp.top_ft is not None
                    )
                    realized = (
                        (showers is not None and showers >= showers_mm)
                        or (cover is not None and cover >= CHAR_COVER_REALIZED_PCT)
                        or has_geom
                    )
                    embedded = _point_embedded(sounding, cruise_ft)
                    if conv is not None and conv.bulk_shear_0_6km_kt is not None:
                        shear_max = max(shear_max or 0.0, conv.bulk_shear_0_6km_kt)
                    vm = sounding.vertical_motion
                    if vm is not None and vm.classification == VerticalMotionClass.SYNOPTIC_ASCENT:
                        synoptic_ascent = True

                k, tt = _native_or_metpy(sounding)
                points.append(
                    ConvCharPoint(
                        is_convective=is_conv,
                        realized=realized,
                        embedded=embedded,
                        k_index=k,
                        total_totals=tt,
                    )
                )

            if not points:
                per_model.append(
                    ModelAdvisoryResult.build(
                        model=model,
                        status=AdvisoryStatus.UNAVAILABLE,
                        detail=adv_t("no_data", loc),
                        affected=0,
                        total=0,
                        total_distance_nm=ctx.total_distance_nm,
                    )
                )
                continue

            character = classify_convective_character(
                points,
                shear_kt=shear_max,
                front_present=_front_present(ctx, model),
                synoptic_ascent=synoptic_ascent,
                isolated_max_pct=isolated_max_pct,
                scattered_max_pct=scattered_max_pct,
                organized_shear_kt=organized_shear_kt,
            )

            status = _CHAR_STATUS.get(character, AdvisoryStatus.GREEN)
            total = len(points)
            realized_count = sum(1 for p in points if p.is_convective and p.realized)
            detail = adv_t(f"convective_character.{character.value}", loc)
            if character not in (ConvectiveCharacter.NONE, ConvectiveCharacter.UNKNOWN):
                detail += f" ({format_extent(realized_count, total, ctx.total_distance_nm)})"

            per_model.append(
                ModelAdvisoryResult.build(
                    model=model,
                    status=status,
                    detail=detail,
                    affected=realized_count,
                    total=total,
                    total_distance_nm=ctx.total_distance_nm,
                )
            )

        return RouteAdvisoryResult.from_per_model("convective_character", per_model, params)
