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

import math
from typing import NamedTuple

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


# Default below-base clearance buffer (#298): mirror of the severity-side overfly
# filter (`top_clearance_ft`, default 2000). A buffer this far *under* the cell
# bases, in VMC, is what makes isolated/scattered cells circumnavigable VFR.
CHAR_BASE_CLEARANCE_FT = 2000.0


def _vmc_below_base(
    sounding: SoundingAnalysis, cruise_ft: float, base_ft: float | None
) -> bool:
    """Is the layer between cruise and the convective base free of a stratiform deck?

    The below-base avoidability premise (#298) is see-and-avoid in VMC *underneath*
    the cells. It only holds if the air you would fly in — from cruise up to the
    cell base — is clear. A BKN/OVC layer intruding into that band means descending
    below the cells puts you in cloud (IMC): that is the embedded case from below,
    no see-and-avoid. Returns False when such a deck overlaps the band (or when the
    base is unresolved / at-or-below cruise, where the band can't be bounded).

    Only BKN/OVC breaks VMC — FEW/SCT is see-and-avoid-compatible by definition.
    Bulk low/mid cover is deliberately NOT used here: at/under cumuliform bases it
    reflects the cells' own cu, so it would over-suppress a genuinely clear sub-base
    layer. The explicit layer geometry is the honest signal.
    """
    if base_ft is None or base_ft <= cruise_ft:
        return False
    for cl in sounding.cloud_layers:
        if (
            cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
            and cl.base_ft < base_ft
            and cl.top_ft > cruise_ft
        ):
            return False
    return True


class _BelowBase(NamedTuple):
    """Outcome of the below-base avoidability geometry (i18n-free for testing)."""

    kind: str  # none | unresolved | within_layer | deck | clear | marginal
    base_fl: int | None = None
    margin_ft: int | None = None
    drop_ft: int | None = None


_BELOW_BASE_KEYS = {
    "unresolved": "convective_character.depth_unresolved",
    "within_layer": "convective_character.cruise_within_layer",
    "deck": "convective_character.deck_below_cells",
    "clear": "convective_character.below_base_clear",
    "marginal": "convective_character.below_base_marginal",
}


def _below_base_geometry(
    points: list[ConvCharPoint], cruise_ft: float, base_clearance_ft: float
) -> _BelowBase:
    """Classify cruise vs the realized cells' model-native bases (#298).

    Annotate-only: never changes the band/colour. Precedence is safety-first —
    *unresolved* depth and the *non-VMC* / *within-layer* geometries dominate the
    clear/marginal "more avoidable" notes, so a softer phrase can never mask a
    worse geometry on the same route.
    """
    realized = [p for p in points if p.is_convective and p.realized]
    if not realized:
        return _BelowBase("none")

    resolved = [p for p in realized if p.convective_base_ft is not None]

    # Most-constraining geometry first (resolved cells only), so a softer phrase
    # can never mask a worse one on the same route:
    # 1. Cruise at/above a cell base → inside the convective layer, least avoidable.
    if any(cruise_ft >= p.convective_base_ft for p in resolved):
        return _BelowBase("within_layer")
    # 2. Below the bases, but a BKN/OVC deck sits between cruise and the cells → IMC.
    if any(not p.vmc_below_base for p in resolved):
        return _BelowBase("deck")
    # 3. Any realized cell whose base/depth is unresolved (nwp_precip ghost column,
    #    nwp_lcl_top without LCL, cape fallback, non-GRIB) — can't promise see-and-
    #    avoid route-wide on a clearance we can't measure for every cell.
    if len(resolved) < len(realized):
        return _BelowBase("unresolved")

    # All cells resolved, below the bases, VMC. Report the tightest margin + hint.
    min_margin = min(p.convective_base_ft - cruise_ft for p in resolved)
    min_base = min(p.convective_base_ft for p in resolved)
    base_fl = int(round(min_base / 100.0))
    margin_ft = int(round(min_margin / 100.0) * 100)
    if min_margin >= base_clearance_ft:
        return _BelowBase("clear", base_fl=base_fl, margin_ft=margin_ft)
    drop_ft = int(math.ceil((base_clearance_ft - min_margin) / 500.0) * 500)
    return _BelowBase("marginal", base_fl=base_fl, margin_ft=margin_ft, drop_ft=drop_ft)


def _format_below_base(res: _BelowBase, loc: str) -> str | None:
    """Render the below-base geometry to a localized clearance phrase."""
    if res.kind == "none":
        return None
    tmpl = adv_t(_BELOW_BASE_KEYS[res.kind], loc)
    return tmpl.format(
        fl=res.base_fl,
        margin=f"{res.margin_ft:,}" if res.margin_ft is not None else "",
        drop=f"{res.drop_ft:,}" if res.drop_ft is not None else "",
    )


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
                AdvisoryParameterDef(
                    key="base_clearance_ft",
                    label="Below-base clearance (ft)",
                    description="VMC buffer below convective bases for see-and-avoid (mirrors the overfly clearance)",
                    type="number",
                    unit="ft",
                    default=2000,
                    min=0,
                    max=5000,
                    step=500,
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
        base_clearance_ft = params.get("base_clearance_ft", CHAR_BASE_CLEARANCE_FT)
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
                base_ft: float | None = None
                top_ft: float | None = None
                vmc_below = True
                if is_conv:
                    # Prefer the model's own GRIB-native convective precip (`cp`)
                    # over Open-Meteo `showers`: Open-Meteo does not populate
                    # `showers` for ECMWF IFS (it is structurally 0.0), so the
                    # native field is the only realized-firing precip signal for
                    # ECMWF over marine / elevated convection. A native value of
                    # 0.0 is a real "not firing" reading and is used as-is; only
                    # absent native diagnostics (non-GRIB models — AROME/UKMO/MF)
                    # fall back to the Open-Meteo `showers` cross-section field.
                    diag = sounding.nwp_cloud_diagnostics
                    native_cp = (
                        diag.convective_precip_mm_h if diag is not None else None
                    )
                    if native_cp is not None:
                        # `native_cp` is mm/h; `showers_mm` (the threshold) and
                        # showers_at_point() are mm-over-step. These are equal
                        # only because the cross-section is interpolated to
                        # 1-hour steps (see showers_at_point()'s `at_time()`
                        # lookup against hourly point_forecasts), so a 1 mm/h
                        # rate == 1 mm over the step. If that step ever stops
                        # being 1 hour, or `showers_mm` is recalibrated, revisit
                        # this mm/h-vs-mm equivalence.
                        showers = native_cp
                    else:
                        showers = showers_at_point(
                            ctx.cross_sections, model, rpa.point_index, rpa.forecast_hour
                        )
                    nwp = sounding.convective_nwp
                    cover = nwp.cover_pct if nwp is not None else None
                    base_ft = nwp.base_ft if nwp is not None else None
                    top_ft = nwp.top_ft if nwp is not None else None
                    has_geom = base_ft is not None and top_ft is not None
                    realized = (
                        (showers is not None and showers >= showers_mm)
                        or (cover is not None and cover >= CHAR_COVER_REALIZED_PCT)
                        or has_geom
                    )
                    embedded = _point_embedded(sounding, cruise_ft)
                    # Below-base avoidability geometry (#298): is the layer from
                    # cruise up to the cell base genuinely VMC (no BKN/OVC deck to
                    # descend into)? Computed here where the sounding is in scope.
                    vmc_below = _vmc_below_base(sounding, cruise_ft, base_ft)
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
                        convective_base_ft=base_ft,
                        convective_top_ft=top_ft,
                        vmc_below_base=vmc_below,
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
            # Below-base clearance note (#298) — annotate-only, low bands only. The
            # band/colour is already set; this adds the avoidance geometry vs cruise
            # (and a per-altitude hint). Never on EMBEDDED/WIDESPREAD/ORGANIZED —
            # those own the "no see-and-avoid" case already.
            if character in (ConvectiveCharacter.ISOLATED, ConvectiveCharacter.SCATTERED):
                note = _format_below_base(
                    _below_base_geometry(points, cruise_ft, base_clearance_ft), loc
                )
                if note:
                    detail += f" — {note}"

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
