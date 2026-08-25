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
from collections.abc import Sequence
from typing import NamedTuple

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EMPTY_EXTENT,
    RouteExtent,
    format_extent,
    route_extent,
    showers_at_point,
)
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.sounding.clouds import cloud_cover_band_pct
from weatherbrief.analysis.sounding.convective import (
    character_extent,
    CHAR_COVER_REALIZED_PCT,
    CHAR_EMBED_MIN_NM,
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
    Mitigation,
    MitigationKind,
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

# Bulk cover (%) in the band containing cruise, above which a BKN/OVC layer is
# treated as a stratiform deck the cells hide in (rather than just the cell's own
# cumulus). Promoted to a catalog parameter in #568 (``embed_deck_cover_pct``);
# this stays the default.
CHAR_EMBED_DECK_COVER_PCT = 60.0

# Vertical slack (ft) on the "a deck contains cruise" test (#568). Model vertical
# resolution near FL180 is ~1,000-2,000 ft per level, so demanding exact
# containment of the cruise level inside the layer bounds is falsely precise.
CHAR_EMBED_CRUISE_BUFFER_FT = 1000.0

# Terrain clearance (ft AGL) below which the altitude mitigation will not offer a
# cruise level (#568). Deliberately this advisory's OWN parameter rather than a
# read of ``vfr_feasibility``'s same-named one: §22 governs one *formula* being
# computed two ways, and this is not that — character does not re-derive
# vfr_feasibility's grade, the two advisories merely both need a terrain number.
# vfr_feasibility's means "the scud-running margin beneath a deck"; this one means
# "how low may a cruise level be offered", an en-route MSA-ish concern. A pilot
# may reasonably want different values, and coupling them would foreclose that.
CHAR_MITIGATION_MIN_BASE_AGL_FT = 3000.0

# Default below-base clearance buffer (#298): mirror of the severity-side overfly
# filter (`top_clearance_ft`, default 2000). A buffer this far *under* the cell
# bases, in VMC, is what makes isolated/scattered cells circumnavigable VFR.
CHAR_BASE_CLEARANCE_FT = 2000.0

# The advisory id whose parameters govern character classification, and the
# single source of truth for their defaults. A consumer (ifr_feasibility) grades
# with the *owning* advisory's tuning rather than a duplicate set — the §22 rule
# that stopped the convective colour from being computed two different ways.
CHARACTER_ADVISORY_ID = "convective_character"

CHARACTER_PARAM_DEFAULTS: dict[str, float] = {
    "min_risk": 3,
    "showers_mm": 0.1,
    "isolated_max_pct": 15,
    "scattered_max_pct": 40,
    "organized_shear_kt": 35,
    "base_clearance_ft": CHAR_BASE_CLEARANCE_FT,
    "embed_min_nm": CHAR_EMBED_MIN_NM,
    "embed_cruise_buffer_ft": CHAR_EMBED_CRUISE_BUFFER_FT,
    "embed_deck_cover_pct": CHAR_EMBED_DECK_COVER_PCT,
    "mitigation_min_base_agl_ft": CHAR_MITIGATION_MIN_BASE_AGL_FT,
}


def resolve_character_params(ctx: RouteContext) -> dict[str, float]:
    """The character advisory's *effective* parameters for this evaluation.

    Mirrors ``convective_grading.resolve_convective_params``: overrides off
    ``ctx.advisory_params`` layered over the catalog defaults, so a consumer
    never grades character with its own thresholds. Catalog defaults apply when
    the advisory is disabled — a composite that reads character must still be
    able to read it.
    """
    overrides = ctx.advisory_params.get(CHARACTER_ADVISORY_ID, {})
    return {**CHARACTER_PARAM_DEFAULTS, **overrides}


def _bulk_cover_at(sounding: SoundingAnalysis, altitude_ft: float) -> float | None:
    """Bulk NWP cloud cover (%) for the ICAO band *containing* ``altitude_ft``.

    Thin adapter over ``clouds.cloud_cover_band_pct`` — the band split lives
    there, with the boundary constants, so it cannot drift. ``None`` when the
    model publishes no bulk cover for that band (ECMWF via Open-Meteo), which
    :func:`_point_embedded` reads as "no corroboration available", never as
    "clear".
    """
    return cloud_cover_band_pct(
        altitude_ft,
        sounding.cloud_cover_low_pct,
        sounding.cloud_cover_mid_pct,
        sounding.cloud_cover_high_pct,
    )


def _point_embedded(
    sounding: SoundingAnalysis,
    cruise_ft: float,
    *,
    buffer_ft: float = CHAR_EMBED_CRUISE_BUFFER_FT,
    deck_cover_pct: float = CHAR_EMBED_DECK_COVER_PCT,
) -> bool:
    """True when a BKN/OVC deck **contains cruise** here, so cells cannot be seen.

    Two things this deliberately gets right that the pre-#568 version did not:

    1. **The deck's top is tested, not only its base.** The old test accepted any
       BKN/OVC layer *based* below cruise, so a 697-1,942 ft stratocumulus sheet
       counted as hiding cells from an aircraft at FL180 — 16,000 ft above it in
       clear air with a perfect view of any buildup. On the pack that motivated
       the issue, 36 of ICON's 39 "embedded" points were decks entirely below
       cruise. Containment is tested with ``buffer_ft`` of slack because model
       vertical resolution near FL180 is ~1,000-2,000 ft per level, so exact
       containment of a cruise level inside layer bounds is falsely precise.
       ``_vmc_below_base`` eleven lines down has always tested genuine overlap;
       the two are now consistent.
    2. **Corroborating cover comes from the band containing cruise.** The old
       ``max(low, mid)`` meant a 100 %-covered *low* deck corroborated a "deck"
       at FL180, which is in the mid band.

    When the model publishes no bulk cover for that band (ECMWF via Open-Meteo),
    fall back to requiring OVC as the stronger standalone evidence — unchanged.
    """
    containing = [
        cl
        for cl in sounding.cloud_layers
        if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
        and cl.base_ft - buffer_ft <= cruise_ft <= cl.top_ft + buffer_ft
    ]
    if not containing:
        return False
    cover = _bulk_cover_at(sounding, cruise_ft)
    if cover is not None:
        return cover >= deck_cover_pct
    return any(cl.coverage == CloudCoverage.OVC for cl in containing)


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


def _realized_extent(
    points: Sequence[ConvCharPoint],
    total_distance_nm: float,
    speed_kt: float | None = None,
) -> RouteExtent:
    """Geometry-accurate extent of the realized convective cells (#571).

    A thin call into ``character_extent`` — the classifier's own single reducer,
    which the coverage band and the EMBEDDED contiguity gate also go through — so
    the number this card prints is measured by the same code that graded it,
    including its treatment of hand-built test points with no ``distance_nm``.
    Re-implementing that fallback here was how the two could have drifted.
    """
    return character_extent(
        points,
        total_distance_nm,
        lambda p: p.is_convective and p.realized,
        speed_kt=speed_kt,
    )


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


class CharacterInputs(NamedTuple):
    """Everything one model's character classification is built from.

    ``method_id`` is the #568 addition: the convective track that actually
    graded this model's cells, so the character card can badge it the way the
    severity card already does. It is NOT derivable from
    ``driving_method_id`` — that reads ``AdvisoryHighlights``, which this
    evaluator does not produce — so the method travels out of the builder that
    already visits every sounding.
    """

    points: list[ConvCharPoint]
    shear_max: float | None
    synoptic_ascent: bool
    method_id: str | None


def _character_method_id(
    methods: list[str | None], any_fallback: bool
) -> str | None:
    """The convective track to badge on this model's character result (#568).

    ``methods`` are the ``convective_method_effective`` values of the points
    that count as cells (or, when no point does, of every point with a
    sounding). A fallback anywhere wins outright: "some of this model's cells
    were graded on thermodynamics because the model had no native forecast
    there" is exactly the fact the badge exists to carry, and it must not be
    averaged away by the points that did have one. Otherwise a single distinct
    method is the answer; a mix without a fallback (which resolution cannot
    currently produce — an explicit thermo request is route-wide) badges
    nothing rather than picking arbitrarily.
    """
    if any_fallback:
        return "thermo"
    distinct = {m for m in methods if m is not None}
    return distinct.pop() if len(distinct) == 1 else None


def build_character_points(
    ctx: RouteContext, model: str, params: dict[str, float],
    cruise_ft: float | None = None,
) -> CharacterInputs:
    """Build one model's character points — extracted so consumers can reuse it.

    Returns everything ``classify_convective_character`` needs, plus the
    effective convective method for the badge (#568).
    ``ConvectiveCharacterEvaluator`` calls it for its own grade;
    ``ifr_feasibility`` reaches it through ``classify_route_character`` below
    (§22).

    ``cruise_ft`` overrides ``ctx.cruise_altitude_ft`` — the altitude mitigation
    (#568 Fix 4) re-derives the whole band at candidate levels, so the two
    cruise-dependent per-point signals (``embedded`` and ``vmc_below_base``) have
    to be recomputed against the candidate rather than the planned cruise.
    Recomputing the whole band, not just ``_point_embedded``, is what stops the
    mitigation offering an altitude that merely moves you into a different deck.
    """
    min_risk_idx = int(params.get("min_risk", CHARACTER_PARAM_DEFAULTS["min_risk"]))
    showers_mm = params.get("showers_mm", CHARACTER_PARAM_DEFAULTS["showers_mm"])
    embed_buffer_ft = params.get(
        "embed_cruise_buffer_ft", CHARACTER_PARAM_DEFAULTS["embed_cruise_buffer_ft"]
    )
    embed_deck_cover_pct = params.get(
        "embed_deck_cover_pct", CHARACTER_PARAM_DEFAULTS["embed_deck_cover_pct"]
    )
    if cruise_ft is None:
        cruise_ft = ctx.cruise_altitude_ft

    points: list[ConvCharPoint] = []
    shear_max: float | None = None
    synoptic_ascent = False
    # Effective-method provenance for the badge (#568). Collected over the
    # points that count as cells — the ones the band is built from — with the
    # all-points list as the fallback for a model with no cells at all.
    cell_methods: list[str | None] = []
    all_methods: list[str | None] = []
    cell_fallback = False
    any_fallback = False

    for rpa in ctx.analyses:
        sounding = rpa.sounding.get(model)
        if sounding is None:
            # Keep the point, marked unassessed. Dropping it left ``cell_edges``
            # tiling the whole route over only the covered points, so the last
            # covered point's cell swallowed every uncovered mile (#571 review).
            points.append(ConvCharPoint(
                is_convective=False, realized=False, embedded=False,
                k_index=None, total_totals=None,
                distance_nm=rpa.distance_from_origin_nm, assessed=False,
            ))
            continue

        conv = sounding.convective
        is_conv = (
            conv is not None
            and _RISK_ORDER.index(conv.risk_level) >= min_risk_idx
        )

        all_methods.append(sounding.convective_method_effective)
        any_fallback = any_fallback or sounding.convective_nwp_fallback
        if is_conv:
            cell_methods.append(sounding.convective_method_effective)
            cell_fallback = cell_fallback or sounding.convective_nwp_fallback

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
            # Explicit-convection mode (#462): a fired ICON-D2 cell is
            # realized by construction (the decision table only fires
            # on a simulated echo), but its precip/cover/geometry
            # channels are structurally None — without this branch a
            # firing D2 cell would read "not realized".
            explicit_fired = (
                nwp is not None
                and nwp.method == "nwp_explicit"
                and _RISK_ORDER.index(nwp.risk_level)
                >= _RISK_ORDER.index(ConvectiveRisk.MODERATE)
            )
            realized = (
                (showers is not None and showers >= showers_mm)
                or (cover is not None and cover >= CHAR_COVER_REALIZED_PCT)
                or has_geom
                or explicit_fired
            )
            embedded = _point_embedded(
                sounding,
                cruise_ft,
                buffer_ft=embed_buffer_ft,
                deck_cover_pct=embed_deck_cover_pct,
            )
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
                distance_nm=rpa.distance_from_origin_nm,
            )
        )

    method_id = (
        _character_method_id(cell_methods, cell_fallback)
        if cell_methods
        else _character_method_id(all_methods, any_fallback)
    )
    return CharacterInputs(points, shear_max, synoptic_ascent, method_id)


def classify_inputs(
    ctx: RouteContext, model: str, params: dict[str, float], inputs: CharacterInputs
) -> ConvectiveCharacter:
    """Classify already-built :class:`CharacterInputs` with this advisory's tuning.

    The one place the classifier's keyword arguments are assembled, so the
    evaluator, ``classify_route_character`` (§22 consumers) and the altitude
    mitigation's candidate re-derivation cannot drift apart on a threshold.
    """
    return classify_convective_character(
        inputs.points,
        shear_kt=inputs.shear_max,
        front_present=_front_present(ctx, model),
        synoptic_ascent=inputs.synoptic_ascent,
        isolated_max_pct=params.get(
            "isolated_max_pct", CHARACTER_PARAM_DEFAULTS["isolated_max_pct"]
        ),
        scattered_max_pct=params.get(
            "scattered_max_pct", CHARACTER_PARAM_DEFAULTS["scattered_max_pct"]
        ),
        organized_shear_kt=params.get(
            "organized_shear_kt", CHARACTER_PARAM_DEFAULTS["organized_shear_kt"]
        ),
        embed_min_nm=params.get(
            "embed_min_nm", CHARACTER_PARAM_DEFAULTS["embed_min_nm"]
        ),
        total_distance_nm=ctx.total_distance_nm,
    )


def classify_route_character(
    ctx: RouteContext, model: str, params: dict[str, float]
) -> ConvectiveCharacter | None:
    """One model's convective character, or ``None`` when it has no soundings.

    The single entry point for any consumer that needs the character band
    without the advisory's wording — today ``ifr_feasibility``, which escalates
    its convective axis one step on EMBEDDED (§22). Callers pass
    ``resolve_character_params(ctx)`` so the band they read is the band this
    advisory would show.
    """
    inputs = build_character_points(ctx, model, params)
    if not inputs.points:
        return None
    return classify_inputs(ctx, model, params, inputs)


# --- Altitude mitigation for embedded convection (#568 Fix 4) ---------------
# EMBEDDED means "cells hidden in a deck, no see-and-avoid". A different cruise
# level is often exactly what restores it, in BOTH directions:
#   * descend below the deck — see-and-avoid underneath the cells;
#   * climb above it — VFR on top, where buildups penetrating the layer are
#     visible and can be circumnavigated. Legitimate even when tops are far above
#     the candidate: seeing them is the point, not out-topping them.
# Advice only, per the ``Mitigation`` contract — it never changes the grade, and
# ``mitigated_status`` is the status of the addressed sub-issue (the character
# band you would actually get at that altitude, typically SCATTERED/ISOLATED →
# AMBER, rarely GREEN), never the advisory overall.
#
# Offered ONLY for EMBEDDED, never WIDESPREAD/ORGANIZED — altitude cannot fix
# horizontal extent. And the ISOLATED/SCATTERED-only below-base clearance note
# (#298) is deliberately left alone: EMBEDDED gains the lightbulb instead, so one
# card never carries two competing phrasings of the same idea.

MITIGATION_ADDRESSES = "embedded_deck"


def _candidate_altitudes(ctx: RouteContext, params: dict[str, float]) -> list[int]:
    """Cruise levels the mitigation may offer, nearest to planned cruise first.

    Bounded below by ``mitigation_min_base_agl_ft`` above the route's highest
    terrain and above by ``ctx.flight_ceiling_ft``; stepped on the shared
    ``MITIGATION_BIN_STEP_FT`` grid so a tip lands on the same altitudes the
    other advisories' mitigations use. Planned cruise is excluded — it is the
    altitude that produced the EMBEDDED verdict.
    """
    from weatherbrief.analysis.advisories.vertical_profile import MITIGATION_BIN_STEP_FT

    min_base_agl = params.get(
        "mitigation_min_base_agl_ft",
        CHARACTER_PARAM_DEFAULTS["mitigation_min_base_agl_ft"],
    )
    max_terrain = ctx.elevation.max_elevation_ft if ctx.elevation else 0.0
    floor = (max_terrain or 0.0) + min_base_agl
    ceiling = ctx.flight_ceiling_ft
    cruise = ctx.cruise_altitude_ft
    step = MITIGATION_BIN_STEP_FT
    lo = int(math.ceil(floor / step) * step)
    hi = int(math.floor(ceiling / step) * step)
    if hi < lo:
        return []
    cands = [a for a in range(lo, hi + step, step) if a != int(cruise)]
    # Nearest-to-cruise first, ties broken low→high so the ladder is stable.
    cands.sort(key=lambda a: (abs(a - cruise), a))
    return cands


def _bands_at_candidates(
    ctx: RouteContext,
    model: str,
    params: dict[str, float],
    candidates: list[int],
    *,
    stop_when_cleared: bool = False,
) -> dict[int, ConvectiveCharacter]:
    """This model's character band at each candidate altitude, nearest cruise first.

    The **full** band is re-derived at each level rather than just
    ``_point_embedded`` — that is what stops the mitigation from offering an
    altitude that merely moves you into a different deck, and it is what supplies
    the honest ``mitigated_status``.

    ``stop_when_cleared`` returns as soon as one candidate is no longer EMBEDDED.
    Safe only when a single model is embedded: ``candidates`` is ordered
    nearest-to-cruise, so the first cleared level is the one that would be picked
    anyway, and the truncated map cannot change a per-model answer. With two or
    more embedded models the aggregate has to intersect the full ladders.
    """
    bands: dict[int, ConvectiveCharacter] = {}
    for alt in candidates:
        inputs = build_character_points(ctx, model, params, cruise_ft=float(alt))
        if not inputs.points:
            continue
        bands[alt] = classify_inputs(ctx, model, params, inputs)
        if stop_when_cleared and bands[alt] is not ConvectiveCharacter.EMBEDDED:
            break
    return bands


def _first_cleared(
    candidates: list[int], bands: dict[int, ConvectiveCharacter]
) -> int | None:
    """Nearest-to-cruise candidate whose band is known and no longer EMBEDDED.

    An altitude missing from ``bands`` (no soundings there, or a scan truncated by
    ``stop_when_cleared``) is not an answer — never read as "cleared".
    """
    return next(
        (
            a
            for a in candidates
            if bands.get(a) not in (None, ConvectiveCharacter.EMBEDDED)
        ),
        None,
    )


def _altitude_mitigation(
    alt: int, cruise_ft: float, mitigated_status: AdvisoryStatus, loc: str
) -> Mitigation:
    """Build the climb/descend tip for a candidate that clears the deck."""
    key = (
        "convective_character.mitigation.climb"
        if alt > cruise_ft
        else "convective_character.mitigation.descend"
    )
    return Mitigation(
        kind=MitigationKind.ALTITUDE,
        addresses=MITIGATION_ADDRESSES,
        detail=adv_t(key, loc, alt=alt),
        mitigated_status=mitigated_status,
        altitude_ft=alt,
        # v1 is level-altitude only. The >= embed_min_nm contiguous test is
        # route-level and non-additive, so it cannot be expressed as a per-point
        # cost in ``vertical_profile.solve()`` — a varying profile would need the
        # solver to propose one from a per-point "in-deck" cost and the contiguous
        # gate to be re-evaluated against it. Until then, no ``profile``.
        profile=None,
    )


def _attach_mitigations(
    ctx: RouteContext,
    params: dict[str, float],
    per_model: list[ModelAdvisoryResult],
    embedded_models: list[str],
    loc: str,
) -> RouteAdvisoryResult:
    """Add the EMBEDDED altitude tips to the per-model results and the aggregate.

    Run after the per-model loop, not inside it, so the number of embedded models
    is known: with one, each ladder scan can stop at the first cleared level; with
    two or more the aggregate needs the full ladders to intersect them.
    """
    cruise_ft = ctx.cruise_altitude_ft
    if not embedded_models:
        return RouteAdvisoryResult.from_per_model(
            "convective_character", per_model, params
        )

    candidates = _candidate_altitudes(ctx, params)
    single = len(embedded_models) == 1
    bands_by_model = {
        m: _bands_at_candidates(
            ctx, m, params, candidates, stop_when_cleared=single
        )
        for m in embedded_models
    }

    resolved: list[ModelAdvisoryResult] = []
    for res in per_model:
        model_bands = bands_by_model.get(res.model)
        cleared = (
            _first_cleared(candidates, model_bands) if model_bands is not None else None
        )
        if cleared is None:
            resolved.append(res)
            continue
        tip = _altitude_mitigation(
            cleared,
            cruise_ft,
            _CHAR_STATUS.get(model_bands[cleared], AdvisoryStatus.GREEN),
            loc,
        )
        resolved.append(res.model_copy(update={"mitigations": [tip]}))

    result = RouteAdvisoryResult.from_per_model(
        "convective_character", resolved, params
    )

    # The default representative-model policy would hand the aggregate whichever
    # EMBEDDED model happened to be first, and its altitude may leave another
    # EMBEDDED model still embedded — advice that helps one model and not another.
    # Promote only an altitude that clears EVERY model currently grading EMBEDDED,
    # and report the WORST band any of them would still have there.
    common = next(
        (
            a
            for a in candidates
            if all(
                b.get(a) not in (None, ConvectiveCharacter.EMBEDDED)
                for b in bands_by_model.values()
            )
        ),
        None,
    )
    aggregate: list[Mitigation] = []
    if common is not None:
        aggregate.append(
            _altitude_mitigation(
                common,
                cruise_ft,
                AdvisoryStatus.worst([
                    _CHAR_STATUS.get(b[common], AdvisoryStatus.GREEN)
                    for b in bands_by_model.values()
                ]),
                loc,
            )
        )
    return result.model_copy(update={"aggregate_mitigations": aggregate})


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
            timing_class="scan",
            altitude_dependent=True,
            parameters=[
                AdvisoryParameterDef(
                    key="min_risk",
                    label="Min risk level",
                    description="Minimum severity that counts as a cell (3=MODERATE, 4=HIGH)",
                    type="number",
                    default=CHARACTER_PARAM_DEFAULTS["min_risk"],
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
                    default=CHARACTER_PARAM_DEFAULTS["showers_mm"],
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
                    default=CHARACTER_PARAM_DEFAULTS["isolated_max_pct"],
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
                    default=CHARACTER_PARAM_DEFAULTS["scattered_max_pct"],
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
                    default=CHARACTER_PARAM_DEFAULTS["organized_shear_kt"],
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
                    default=CHARACTER_PARAM_DEFAULTS["base_clearance_ft"],
                    min=0,
                    max=5000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="embed_min_nm",
                    label="Embedded min extent (nm)",
                    description=(
                        "Longest unbroken stretch of cells hidden in a deck at/above "
                        "which the route counts as embedded"
                    ),
                    type="number",
                    unit="nm",
                    default=CHARACTER_PARAM_DEFAULTS["embed_min_nm"],
                    min=0,
                    max=200,
                    step=10,
                ),
                AdvisoryParameterDef(
                    key="embed_cruise_buffer_ft",
                    label="Embedded cruise buffer (ft)",
                    description=(
                        "Slack on \"the deck contains cruise\" — model levels are "
                        "1,000-2,000 ft apart near the flight levels"
                    ),
                    type="altitude",
                    unit="ft",
                    default=CHARACTER_PARAM_DEFAULTS["embed_cruise_buffer_ft"],
                    min=0,
                    max=3000,
                    step=250,
                ),
                AdvisoryParameterDef(
                    key="embed_deck_cover_pct",
                    label="Embedded deck cover %",
                    description=(
                        "Bulk cloud cover in the band at cruise above which the layer "
                        "is a stratiform deck, not the cells' own cumulus"
                    ),
                    type="percent",
                    unit="%",
                    default=CHARACTER_PARAM_DEFAULTS["embed_deck_cover_pct"],
                    min=30,
                    max=100,
                    step=5,
                ),
                AdvisoryParameterDef(
                    key="mitigation_min_base_agl_ft",
                    label="Lowest cruise offered (ft AGL)",
                    description=(
                        "Terrain clearance below which the climb/descend tip will not "
                        "suggest a cruise level"
                    ),
                    type="altitude",
                    unit="ft",
                    default=CHARACTER_PARAM_DEFAULTS["mitigation_min_base_agl_ft"],
                    min=1000,
                    max=6000,
                    step=500,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        # min_risk / showers_mm / the embedded gate's tuning are consumed inside
        # build_character_points and classify_inputs.
        base_clearance_ft = params.get(
            "base_clearance_ft", CHARACTER_PARAM_DEFAULTS["base_clearance_ft"]
        )
        cruise_ft = ctx.cruise_altitude_ft
        loc = ctx.locale

        per_model: list[ModelAdvisoryResult] = []
        # Models the altitude mitigation applies to (#568 Fix 4) — EMBEDDED only,
        # collected here and scanned after the loop, where the count is known.
        embedded_models: list[str] = []

        for model in ctx.models:
            # Same builder the composites reach through classify_route_character
            # (§22) — the band this card shows is the band they act on.
            inputs = build_character_points(ctx, model, params)
            points = inputs.points

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

            character = classify_inputs(ctx, model, params, inputs)

            status = _CHAR_STATUS.get(character, AdvisoryStatus.GREEN)
            total = sum(1 for p in points if p.assessed)
            realized_count = sum(1 for p in points if p.is_convective and p.realized)
            # Extent of the realized cells, over the same cell edges the
            # EMBEDDED contiguity gate measures its run on (#571) — the card's
            # "(Xnm/Ynm)" and the gate can no longer describe different geometry.
            extent = _realized_extent(
                points, ctx.total_distance_nm,
                speed_kt=ctx.cruise_groundspeed_kt,
            )
            detail = adv_t(f"convective_character.{character.value}", loc)
            if character not in (ConvectiveCharacter.NONE, ConvectiveCharacter.UNKNOWN):
                detail += f" ({format_extent(extent)})"
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

            if character is ConvectiveCharacter.EMBEDDED:
                embedded_models.append(model)

            per_model.append(
                ModelAdvisoryResult.build(
                    model=model,
                    status=status,
                    detail=detail,
                    affected=realized_count,
                    extent=extent,
                    total=total,
                    total_distance_nm=ctx.total_distance_nm,
                    # #568: the character card is the one that renders the
                    # EMBEDDED/WIDESPREAD red, and until now it badged nothing —
                    # so a model graded on thermodynamics for lack of a native
                    # track sat beside two graded on their own schemes with no
                    # visible difference, and its outlier verdict read as
                    # meteorological disagreement. The severity card has badged
                    # this since #408; this is the same fact on the other axis.
                    primary_method_id=inputs.method_id,
                )
            )

        return _attach_mitigations(ctx, params, per_model, embedded_models, loc)
