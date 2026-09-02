"""Shared per-model convective grading — the single source of the convective colour.

Extracted from ``convective.py`` so that every consumer of "how bad is the
convection on this route?" answers it with the *same* formula rather than its
own re-derivation. Before this, ``ifr_feasibility`` carried a second, older
derivation (its own ``convective_min_risk`` floor, its own 10 % red threshold, no
tops-below-cruise filter, and no awareness of the #442 DD-trigger amber), so the
two advisories could — and did — disagree in both directions on the same
sounding. See meteorology-decisions §22.

The grading logic here is verbatim the #442 behaviour documented in
meteorology-decisions §18:

- colour comes from the model's own NWP convective tier (the active track),
  **not** ``max(NWP, DD)``;
- a *green* NWP whose DD-vs-scheme cross-check reads ``dd_not_corroborated`` is
  raised to AMBER only (``reason="dd_trigger"``), tier capped at MODERATE, and
  excluded from the red-coverage count — DD alone can never make a red;
- any MODERATE+ point reaching cruise floors the model at AMBER;
- convection whose tops are below ``cruise - top_clearance_ft`` is ignored.

What stays OUT of this module: everything locale-dependent. The evaluators own
their own copy (headline wording, coverage suffixes, aggregate synthesis) — this
module returns the numbers and the colour, never a rendered sentence.
"""

from __future__ import annotations

from dataclasses import dataclass

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import (
    EMPTY_EXTENT,
    FlaggedCell,
    RouteExtent,
    EXTENT_MIN_NM,
    grade_extent,
    route_extent,
)
from weatherbrief.analysis.sounding.convective import convective_cross_check
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveRisk,
    HighlightSeverity,
)

# Ordered from least to most severe.
RISK_ORDER = [
    ConvectiveRisk.NONE,
    ConvectiveRisk.MARGINAL,
    ConvectiveRisk.LOW,
    ConvectiveRisk.MODERATE,
    ConvectiveRisk.HIGH,
    ConvectiveRisk.EXTREME,
]

# Threshold for "actual convective concern" — the headline extent is anchored
# here (MODERATE+), separate from the LOW ``min_risk`` floor that drives the
# colour (#300).
MOD_IDX = RISK_ORDER.index(ConvectiveRisk.MODERATE)

# The advisory id whose parameters govern this grading. Consumers resolve the
# user's tuning through ``resolve_convective_params`` so a pilot who raises
# ``extent_pct_red`` moves the convective colour everywhere it is used, not
# just on the convective card.
CONVECTIVE_ADVISORY_ID = "convective"

# Single source of truth for the convective parameter defaults. ``convective.py``
# builds its catalog entry from these, and ``resolve_convective_params`` falls
# back to them, so the catalog the settings page renders and the values a
# consuming composite grades with cannot drift apart (the drift that produced
# the 15/30-vs-20/50 icing bug in ifr_feasibility).
CONVECTIVE_PARAM_DEFAULTS: dict[str, float] = {
    "min_risk": 2,
    "extent_pct_amber": 20,
    "extent_pct_red": 50,
    "extent_min_nm": EXTENT_MIN_NM,
    "top_clearance_ft": 2000,
}


@dataclass(frozen=True)
class ConvectiveModelGrade:
    """Everything the convective colour for one model is made of.

    Index-aligned with ``ctx.analyses``: ``ribbon_points`` and ``region_cells``
    always carry exactly one entry per route point (UNAVAILABLE/None where the
    model has no sounding), so a composite can zip them against its own
    per-point pass without re-deriving which points were graded.
    """

    model: str
    status: AdvisoryStatus
    total: int              # points with a sounding (the grading denominator)
    affected: int           # >= min_risk (LOW floor) — drives the colour
    affected_mod: int       # >= MODERATE — anchors the headline extent (#300)
    dd_trigger_count: int   # DD-only amber points (#442) — never escalate RED
    below_cruise_count: int  # risky points skipped because tops below cruise
    worst_risk: ConvectiveRisk
    max_cover_pct: float | None
    max_precip_mm_h: float | None
    ribbon_points: list[tuple[float, HighlightSeverity]]
    region_cells: list[tuple[float, FlaggedCell | None]]
    peak_dist_nm: float | None
    cross_check: str | None
    # Geometry-accurate extents of the two populations above (#571). ``extent``
    # measures ``affected``, ``extent_mod`` measures ``affected_mod``; both carry
    # their own ``domain_nm`` so a consumer never has to scale a point ratio.
    extent: RouteExtent = EMPTY_EXTENT
    extent_mod: RouteExtent = EMPTY_EXTENT


def resolve_convective_params(ctx: RouteContext) -> dict[str, float]:
    """The convective advisory's *effective* parameters for this evaluation.

    Reads the user's overrides for the ``convective`` advisory off the context
    (threaded through by ``evaluate_all``) layered over
    ``CONVECTIVE_PARAM_DEFAULTS``. A consuming composite therefore grades
    convection with the same tuning the convective card shows.

    When the user has **disabled** the convective advisory there are no
    overrides to read and the catalog defaults apply: a composite that depends
    on convection must still grade it, and silently inheriting "disabled" as
    "no convection" would be the #391 false-GREEN failure mode.
    """
    overrides = ctx.advisory_params.get(CONVECTIVE_ADVISORY_ID, {})
    return {**CONVECTIVE_PARAM_DEFAULTS, **overrides}


def _peak_cross_check(
    status: AdvisoryStatus,
    peak_has_both: bool,
    peak_nwp_risk: ConvectiveRisk,
    peak_dd_risk: ConvectiveRisk,
    peak_fallback: bool = False,
) -> str | None:
    """DD-vs-NWP divergence note, anchored on the GRADE DRIVER (#442 follow-up).

    Principle: only report a divergence when the two convective signals disagree
    on the point that actually drives the advisory — not on some unrelated
    minority stretch (which read as contradicting the headline). We compare the
    NWP and DD tiers AT the peak (driving) point and only flag a **≥2-level**
    gap; same-or-one-off is normal method spread, not worth surfacing. Named
    after the cross-section layer toggles ("Thermo Convective" / "NWP
    Convective") so a pilot can pull up exactly these two overlays to compare.

    ``peak_fallback`` is the #568 case: there is no NWP tier to compare because
    this model has no native convective forecast at this range. ``convective_
    cross_check`` correctly returns ``None`` at its own layer (nothing to
    compare), so the note has to be emitted here — otherwise the card is silent
    about the one fact that explains why this model is the outlier, and its
    grade reads as meteorological disagreement rather than a missing track.
    """
    if status == AdvisoryStatus.GREEN:
        return None
    if peak_fallback:
        return (
            "No NWP Convective forecast from this model here "
            "— graded on Thermo Convective (thermodynamics) alone, "
            "which on its own can never grade red"
        )
    if not peak_has_both:
        return None
    nwp_idx = RISK_ORDER.index(peak_nwp_risk)
    dd_idx = RISK_ORDER.index(peak_dd_risk)
    if abs(nwp_idx - dd_idx) < 2:
        return None
    if nwp_idx > dd_idx:
        # The model's own forecast drives it; thermodynamics lag.
        return (
            f"NWP Convective (the model's own forecast) drives this "
            f"— Thermo Convective shows only "
            f"{peak_dd_risk.value.upper()} instability here"
        )
    # The thermodynamics drive it; the model itself is quiet.
    return (
        f"Thermo Convective shows {peak_dd_risk.value.upper()} "
        f"instability, but the model's own NWP Convective forecast "
        f"is quiet here"
    )


def grade_convective_model(
    ctx: RouteContext, model: str, params: dict[str, float]
) -> ConvectiveModelGrade:
    """Grade one model's route convection — the single convective formula.

    Callers: ``ConvectiveEvaluator`` (which adds the locale wording and the
    cross-model aggregate) and ``IFRFeasibilityEvaluator`` (which takes
    ``status`` as its convective axis). Any future consumer must come through
    here rather than re-reading ``sounding.convective`` with its own thresholds.
    """
    min_risk_idx = int(params.get("min_risk", CONVECTIVE_PARAM_DEFAULTS["min_risk"]))
    extent_pct_amber = params.get(
        "extent_pct_amber", CONVECTIVE_PARAM_DEFAULTS["extent_pct_amber"]
    )
    extent_min_nm = params.get(
        "extent_min_nm", CONVECTIVE_PARAM_DEFAULTS["extent_min_nm"]
    )
    extent_pct_red = params.get(
        "extent_pct_red", CONVECTIVE_PARAM_DEFAULTS["extent_pct_red"]
    )
    top_clearance_ft = params.get(
        "top_clearance_ft", CONVECTIVE_PARAM_DEFAULTS["top_clearance_ft"]
    )

    min_risk = RISK_ORDER[min(min_risk_idx, len(RISK_ORDER) - 1)]
    min_risk_index = RISK_ORDER.index(min_risk)
    cruise_ft = ctx.cruise_altitude_ft

    total = 0
    affected = 0
    affected_mod = 0
    dd_trigger_count = 0
    has_high = False
    worst_risk = ConvectiveRisk.NONE
    below_cruise_count = 0
    max_cover_pct: float | None = None
    # Peak native convective precip among the flagged points. Distinguishes
    # "the scheme is quiet" from "the scheme IS precipitating but its tier is
    # capped because depth is unknown" (the nwp_precip ladder tops out at
    # MODERATE) — the LOW-only headline the evaluator picks depends on it.
    max_precip_mm_h: float | None = None
    ribbon_points: list[tuple[float, HighlightSeverity]] = []
    region_cells: list[tuple[float, FlaggedCell | None]] = []
    # Along-route positions of the two graded populations, for the
    # geometry-accurate extents (#571 D1/D2). ``affected`` (the LOW-floor union)
    # drives the colour; ``affected_mod`` (MODERATE+) anchors the headline. Each
    # needs its own midpoint-cell reduction — deriving the MODERATE+ nm as a
    # share of the union's is exactly the "45% in the string, 68.8% in the JSON"
    # split this removes.
    # Index-aligned with ``ribbon_points`` (one entry per route point), the same
    # shape every other evaluator in #571 uses. Deliberately NOT sets of raw
    # distance floats: two route points can share a distance (a repeated
    # waypoint), and membership-testing by value would then mark a clean point
    # affected because its twin was — silently overstating the extent instead of
    # measuring the point (#571 review).
    affected_flags: list[bool] = []
    mod_flags: list[bool] = []
    # DD-trigger points, located rather than counted (#571). The #442 cap tests
    # the coverage the model's OWN scheme produced, and subtracting a count from
    # a count could only ever approximate that: the two populations are not
    # interchangeable once the extent is measured in miles.
    dd_flags: list[bool] = []
    # The grading denominator is points carrying a sounding (``total``), not the
    # whole route — a model with no data at a point must not dilute the coverage
    # of the points it does resolve (#391).
    graded_flags: list[bool] = []
    # Worst affected point for peak_dist_nm: max graded risk, ties → CAPE.
    peak_key: tuple[int, float] | None = None
    peak_dist: float | None = None
    peak_nwp_risk = ConvectiveRisk.NONE  # NWP tier at the driving point
    peak_dd_risk = ConvectiveRisk.NONE   # DD (thermo) tier at that point
    peak_has_both = False  # both signals present at the driving point
    peak_fallback = False  # driving point graded on thermo for lack of an NWP track

    for rpa in ctx.analyses:
        dist = rpa.distance_from_origin_nm or 0.0
        # One entry per point, appended before any `continue`, so these stay
        # index-aligned with ``ribbon_points`` on every path through the loop.
        affected_flags.append(False)
        mod_flags.append(False)
        dd_flags.append(False)
        graded_flags.append(False)
        sounding = rpa.sounding.get(model)
        if sounding is None:
            ribbon_points.append((dist, HighlightSeverity.UNAVAILABLE))
            region_cells.append((dist, None))
            continue
        total += 1
        graded_flags[-1] = True

        # Independent of the grade filters below: compare the chosen thermo
        # (CAPE-derived) risk against the model's own convective scheme. Use
        # convective_thermo explicitly (matches the digest and dd_nwp_agreement)
        # so this stays a DD-vs-NWP comparison even if convective ever becomes
        # the chosen (possibly NWP) method. Do NOT fall back to
        # sounding.convective: when the active track is the NWP one that would
        # pass the NWP assessment as both sides → circular self-comparison
        # (#283 review). If thermo is missing, convective_cross_check returns
        # None on its own.
        thermo_conv = sounding.convective_thermo
        xc = convective_cross_check(thermo_conv, sounding.convective_nwp)

        conv = sounding.convective
        if conv is None:
            # Sounding present but no convective assessment: not a hazard we can
            # locate — grade the ribbon GREEN (not UNAVAILABLE, which is
            # reserved for a missing sounding).
            ribbon_points.append((dist, HighlightSeverity.GREEN))
            region_cells.append((dist, None))
            continue

        # NWP-native grade (#442, meteorology-decisions §18). The colour comes
        # from the model's OWN convective scheme (the active track), NOT
        # max(NWP, DD). A quiet NWP is no longer floored up to a loaded DD tower
        # — that floor produced the loaded-gun false-alarm REDs. The DD tier
        # still speaks, but only as an AMBER cap + the cross-check note, never a
        # red. ``reason`` answers "why is this flagged?" — ``active_track`` (the
        # model's scheme saw it) or ``dd_trigger`` (the model was quiet and the
        # thermodynamics raised it to amber); ``method_id`` stays the active
        # track's, the layer a chip draws.
        graded_risk = conv.risk_level
        reason = "active_track"

        # #568 — the *absent* NWP track, the hole §18 left open. When the user
        # asked for the NWP track and this model has none at this point (its
        # GRIB run is out of forecast range, or a D2 detection channel was
        # incomplete), ``conv`` is the THERMO assessment: MetPy parcel CAPE,
        # graded here beside siblings that were graded on their own convective
        # schemes. §18's DD-amber cap never sees it, because the thermo tier is
        # already at/above ``min_risk`` and the green-NWP branch below is never
        # entered — so an uncorroborated DD tower goes uncapped to RED on the one
        # model that had no scheme to corroborate it. (Observed: LFMD→EGTF
        # 2026-08-27, ICON RED 83 % of route while GFS/ECMWF were AMBER on the
        # identical thermodynamic signal, every one of their flagged points a
        # capped ``dd_trigger``.)
        #
        # Treat it exactly as §18 treats an uncorroborated DD tower: cap the tier
        # at MODERATE and count it into ``dd_trigger_count`` so the red-coverage
        # exclusion applies. A fallback-graded model can reach AMBER, never RED
        # on DD alone. ``reason`` stays separable for telemetry.
        #
        # Keyed on ``convective_nwp_fallback``, NOT on ``convective_nwp is None``
        # (False under an explicit thermo request, where capping would silently
        # override the user's choice) and NOT on
        # ``convective_method_effective == "thermo"`` (ambiguous between the two).
        nwp_fallback = sounding.convective_nwp_fallback
        # Qualification is decided on the tier the model actually produced, and
        # only THEN is the cap applied. Capping first and re-testing against
        # ``min_risk`` silently drops the point when the pilot has raised the
        # floor above MODERATE (the catalog allows up to 4/HIGH): a thermo-HIGH
        # fallback point capped to MODERATE falls below a HIGH floor, and the
        # ``dd_trigger`` branch below cannot catch it either — ``xc`` is None for
        # an absent track, so there is no ``dd_not_corroborated`` to key on. The
        # point then vanishes from the ribbon, ``dd_trigger_count`` AND the
        # absence note, and a route that graded RED before this fix grades GREEN
        # after it. That is the same silent mishandling this fix exists to close,
        # not an acceptable side effect of it: the cap exists to stop DD alone
        # reaching RED, not to suppress a point the pilot's own floor admits.
        fallback_flagged = False
        if nwp_fallback:
            fallback_flagged = RISK_ORDER.index(graded_risk) >= min_risk_index
            if RISK_ORDER.index(graded_risk) > MOD_IDX:
                graded_risk = ConvectiveRisk.MODERATE
            reason = "dd_fallback"

        risk_idx = RISK_ORDER.index(graded_risk)
        if risk_idx < min_risk_index and not fallback_flagged:
            # NWP is GREEN here. DD-trigger AMBER cap: when the DD-vs-model
            # cross-check flags an uncorroborated DD MODERATE+ tower (the
            # thermodynamics are loaded but the model's own scheme is quiet),
            # raise this point to AMBER — never RED — so the divergence carries
            # a colour, not just a note. Bound to the SAME condition as ``xc``
            # (``dd_not_corroborated``) so colour, note, and reason can never
            # diverge. Capped at MODERATE so a DD HIGH never renders a red-tier
            # "peak HIGH" under amber.
            if (
                xc is not None
                and xc.direction == "dd_not_corroborated"
                and thermo_conv is not None
                and MOD_IDX >= min_risk_index
            ):
                graded_risk = ConvectiveRisk.MODERATE
                risk_idx = MOD_IDX
                reason = "dd_trigger"
                dd_trigger_count += 1
                dd_flags[-1] = True
            else:
                # Below the min risk floor → GREEN on the ribbon (checked,
                # nothing worth flagging here).
                ribbon_points.append((dist, HighlightSeverity.GREEN))
                region_cells.append((dist, None))
                continue

        # A flagged fallback point counts as a DD trigger (#568), tallied here —
        # the same place, and for the same reason, as the §18 branch above: it
        # excludes the point from the red-coverage test. ``reason`` can only
        # still read "dd_fallback" when the branch above did not relabel it.
        if reason == "dd_fallback":
            dd_trigger_count += 1
            dd_flags[-1] = True

        # Skip if convective tops are well below cruise altitude. When the DD
        # floor raised the grade and the active (quiet NWP) track has no
        # geometry (top_ft=None), fall back to the thermo EL so altitude
        # awareness is preserved — otherwise top_ft=None bypasses the filter and
        # fires the advisory for convection that tops out below cruise (#283
        # review I1). Use the deeper of the active and thermo tops: that covers
        # both a missing NWP top and a shallow NWP top below the DD EL,
        # otherwise a quiet/shallow NWP top would filter out a point graded by a
        # DD tower that does reach cruise.
        #
        # #592 — this is the FILTER input, and it is deliberately no longer the
        # same variable as the geometry drawn below. One value was doing two
        # jobs: "does this convection reach my cruise level?" is meteorological
        # grading, "how deep is the drawn box?" is rendering. Widening the
        # geometry resolution (below) to stop 38 % of flagged points drawing as
        # full-column ghosts must not move a single grade, so the gate here —
        # ``graded_risk != conv.risk_level``, i.e. only when the DD floor
        # modified the grade — stays exactly as it was. Opening THIS gate is a
        # real policy change ("use the deeper of the model's and the
        # thermodynamic top when testing whether convection reaches cruise");
        # it is defensible, it moves ~1.4 % of graded rows, and it belongs in
        # meteorology-decisions as its own dated decision with the replay
        # evidence — not smuggled in behind a rendering fix.
        filter_top_ft = conv.top_ft
        if thermo_conv is not None and graded_risk != conv.risk_level:
            thermo_top = thermo_conv.top_ft
            if filter_top_ft is None or (
                thermo_top is not None and thermo_top > filter_top_ft
            ):
                filter_top_ft = thermo_top
        if (
            filter_top_ft is not None
            and filter_top_ft + top_clearance_ft <= cruise_ft
        ):
            below_cruise_count += 1
            # Tops below cruise (with clearance) → not a hazard at cruise →
            # GREEN on the ribbon, no cutout.
            ribbon_points.append((dist, HighlightSeverity.GREEN))
            region_cells.append((dist, None))
            continue

        affected += 1
        affected_flags[-1] = True
        if risk_idx >= MOD_IDX:
            affected_mod += 1
            mod_flags[-1] = True
        if risk_idx > RISK_ORDER.index(worst_risk):
            worst_risk = graded_risk

        is_high = graded_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
        if is_high:
            has_high = True

        if conv.cover_pct is not None:
            max_cover_pct = max(max_cover_pct or 0, conv.cover_pct)

        if conv.convective_precip_mm_h is not None:
            max_precip_mm_h = max(
                max_precip_mm_h or 0.0, conv.convective_precip_mm_h
            )

        # Highlight geometry for this flagged point (#373), resolved
        # INDEPENDENTLY of the filter input above (#592). Each missing bound is
        # filled from the thermo track whenever the thermo track has it, with no
        # gate on whether the DD floor moved the grade — the old gate meant a
        # plain ``active_track`` point (the model says "convection here" without
        # publishing bounds) drew a terrain-to-top ghost while complete
        # base+top sat unread two fields away in the same sounding. Measured
        # over the eval corpus that was 1,666 of 2,413 ghosts (69 %).
        #
        # Fill the gap, never override: a bound the model published is the
        # model's own claim about its own storm and stands. Only the missing
        # side is borrowed, so this can widen a box from "unknown" to "known",
        # never contradict the model.
        severity = HighlightSeverity.RED if is_high else HighlightSeverity.AMBER
        ribbon_points.append((dist, severity))
        geometry_base_ft = conv.base_ft
        geometry_top_ft = conv.top_ft
        # Did any DRAWN bound come from a track other than the graded one? Under
        # ``dd_fallback`` (and an explicit thermo request) ``conv`` *is* the
        # thermo assessment, so there is no second source and nothing is ever
        # borrowed — the flag stays False and the box reads as the plain tower
        # it is.
        borrowed_from_thermo = False
        if thermo_conv is not None:
            if geometry_base_ft is None and thermo_conv.base_ft is not None:
                geometry_base_ft = thermo_conv.base_ft
                borrowed_from_thermo = True
            if geometry_top_ft is None and thermo_conv.top_ft is not None:
                geometry_top_ft = thermo_conv.top_ft
                borrowed_from_thermo = True
        # A bounded cutout requires BOTH base and top resolved. A known top with
        # an unknown base must NOT render as a solid box down to terrain — the
        # client draws base=None to the ground, which would imply a base the
        # model doesn't have and erase the tower/ghost distinction. So fall back
        # to the depth-unknown marker whenever either bound is still missing.
        # The GRADED convective track — "nwp" / "thermo" under the CAPE fallback
        # (#408). Deliberately NOT compounded when the thermo floor raised the
        # grade: the floor changes the severity, not the track. Where the drawn
        # bounds came from somewhere else, ``kind`` says so (#592) — the two are
        # read together, and ``method_id`` alone is not the geometry's source.
        conv_method = sounding.convective_method_effective
        if geometry_top_ft is not None and geometry_base_ft is not None:
            # ``tower_estimated`` — a box on an NWP-graded point at least one of
            # whose bounds was borrowed from the thermodynamics. Mixing methods
            # inside one drawn shape without saying so is the same honesty
            # problem as an unmarked ``dd_trigger``, so the kind carries it:
            # the depth is an estimate, and the layer that holds the evidence
            # for it is Thermo Convective, not the graded track (#592, #593).
            region_cells.append((dist, FlaggedCell(
                kind="tower_estimated" if borrowed_from_thermo else "tower",
                severity=severity,
                base_ft=int(geometry_base_ft),
                top_ft=int(geometry_top_ft),
                reason_code=reason,
                metric_id="convective_risk",
                method_id=conv_method,
            )))
        else:
            # Depth-unknown (nwp_precip / cover-only with no thermo bounds to
            # borrow, or the ``dd_fallback`` track's own missing EL). Still a
            # region — the point IS flagged and the pilot must see where — but
            # base/top stay None and the renderers draw a narrow depth-unknown
            # marker for it, never a terrain-to-top rectangle: a full-height box
            # is the strongest possible claim about vertical extent, made
            # exactly where there is the least information.
            region_cells.append((dist, FlaggedCell(
                kind="tower_unresolved",
                severity=severity,
                base_ft=None,
                top_ft=None,
                reason_code=reason,
                metric_id="convective_risk",
                method_id=conv_method,
            )))

        # Peak = worst graded risk, ties broken by highest CAPE — matches the
        # MCP deep-link's highest-CAPE peak.
        cape = conv.cape_jkg
        if cape is None and thermo_conv is not None:
            cape = thermo_conv.cape_jkg
        key = (risk_idx, cape if cape is not None else 0.0)
        if peak_key is None or key > peak_key:
            peak_key = key
            peak_dist = dist
            # Capture the two raw method tiers AT the grade-driving point, so
            # the cross-check reports divergence on what actually drives the
            # advisory (not some unrelated minority stretch). #442 f/u. Compare
            # the two cross-section layers directly — the model's own scheme
            # (convective_nwp) vs the thermodynamics (convective_thermo) — not
            # the resolved active track.
            peak_has_both = (
                sounding.convective_nwp is not None and thermo_conv is not None
            )
            peak_nwp_risk = (
                sounding.convective_nwp.risk_level
                if sounding.convective_nwp is not None
                else ConvectiveRisk.NONE
            )
            peak_dd_risk = (
                thermo_conv.risk_level if thermo_conv is not None
                else ConvectiveRisk.NONE
            )
            # #568: the driving point had no model-native track at all. There is
            # nothing to compare, but the *absence* is itself what the pilot
            # needs told — see ``_peak_cross_check``.
            peak_fallback = nwp_fallback

    # Both extents reduce over the SAME cell edges as the ribbon this grade
    # carries, so the card's sentence, its highlight, the gate that set the
    # colour and the published ``affected_nm`` are one measurement (#571).
    # Taken from ``ctx.analyses`` directly, not from ``ribbon_points``: the flag
    # lists are appended once per analysis at the top of the loop, so this is
    # aligned with them by construction. Reading the distances back out of
    # ``ribbon_points`` would instead depend on its five branch-local appends
    # staying exactly one-per-point — true today, but an assumption rather than
    # a guarantee.
    all_dists = [rpa.distance_from_origin_nm or 0.0 for rpa in ctx.analyses]
    speed_kt = ctx.cruise_groundspeed_kt
    extent = route_extent(
        all_dists, ctx.total_distance_nm, affected_flags, graded_flags,
        speed_kt=speed_kt,
    )
    extent_mod = route_extent(
        all_dists, ctx.total_distance_nm, mod_flags, graded_flags,
        speed_kt=speed_kt,
    )

    # --- colour ---
    if total == 0:
        status = AdvisoryStatus.UNAVAILABLE
    elif affected == 0:
        status = AdvisoryStatus.GREEN
    elif has_high:
        # HIGH/EXTREME anywhere → RED.
        status = AdvisoryStatus.RED
    else:
        status = grade_extent(
            extent, amber_pct=extent_pct_amber, red_pct=extent_pct_red,
            min_nm=extent_min_nm,
        )
        if worst_risk == ConvectiveRisk.LOW and status == AdvisoryStatus.RED:
            status = AdvisoryStatus.AMBER
        # #442: DD-trigger points raise a green NWP to AMBER only — they must
        # never escalate the advisory to RED via coverage. RED comes only from
        # the model's own NWP track (its HIGH, or its own MODERATE+ extent
        # crossing the red threshold). If the red is crossed only because of
        # DD-trigger extent, cap AMBER.
        if status == AdvisoryStatus.RED and dd_trigger_count > 0:
            native_extent = route_extent(
                all_dists,
                ctx.total_distance_nm,
                [a and not d for a, d in zip(affected_flags, dd_flags)],
                graded_flags,
            )
            if grade_extent(
                native_extent,
                amber_pct=extent_pct_amber, red_pct=extent_pct_red,
                min_nm=extent_min_nm,
            ) != AdvisoryStatus.RED:
                status = AdvisoryStatus.AMBER
        # #442: any MODERATE+ convection that reaches cruise is at least a
        # WATCH. The coverage thresholds were calibrated with the old DD floor
        # inflating the LOW-floor extent; without it an isolated-but-real
        # MODERATE tower (or a dd_trigger amber) can fall below
        # extent_pct_amber and read GREEN despite the "MODERATE+ peak"
        # headline — colour contradicting text, and the DD divergence note going
        # unsurfaced. Floor a MODERATE+ point at AMBER.
        if affected_mod > 0 and status == AdvisoryStatus.GREEN:
            status = AdvisoryStatus.AMBER

    return ConvectiveModelGrade(
        model=model,
        status=status,
        total=total,
        affected=affected,
        affected_mod=affected_mod,
        dd_trigger_count=dd_trigger_count,
        below_cruise_count=below_cruise_count,
        worst_risk=worst_risk,
        max_cover_pct=max_cover_pct,
        max_precip_mm_h=max_precip_mm_h,
        ribbon_points=ribbon_points,
        region_cells=region_cells,
        peak_dist_nm=peak_dist,
        cross_check=_peak_cross_check(
            status, peak_has_both, peak_nwp_risk, peak_dd_risk, peak_fallback
        ),
        extent=extent,
        extent_mod=extent_mod,
    )
