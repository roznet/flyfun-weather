"""Front advisory — air-mass boundaries the route crosses (experimental, #196).

Grades the per-briefing Hewson front-detection artifact (``route_fronts.json``,
produced by Part 1 / #195) into a route advisory. It reads
:attr:`RouteContext.route_fronts`, which is populated *only* when the
experimental ``auto_front_detection`` preference was on at generation time — so
the evaluator returns ``UNAVAILABLE`` (and the advisory disappears) whenever the
feature is off. ``default_enabled=False`` keeps it out of the default catalog
run; ``tasks/advise.py`` (``_front_context``) enables it *by default* whenever
the artifact is present, but honors an explicit per-profile ``fronts: false``
opt-out — two independent controls (issue #196, model B): ``auto_front_detection``
is the master (data + overlays) and this advisory toggle independently gates the
grade, so a pilot can keep the overlays without letting the experimental signal
move the overall assessment.

Grading is gated by the weather *on* the boundary, not the θe gradient alone —
so a dry orographic ribbon doesn't false-RED and a front you overfly whose cloud
lofts through your level doesn't false-GREEN. Each crossing (at every stored
level) is graded:
  * **flickering** (low persistence across the time window) → demote: likely an
    orographic / grid artifact, not a front you'll meet.
  * **dry** boundary (clear air on it) → GREEN: a wind shift only.
  * weather **below** the flight (top doesn't reach cruise − buffer) → GREEN:
    overflown cleanly.
  * **convective** boundary reaching the flight band → RED: towers through / above
    your level (deviation / turbulence).
  * **wet** boundary reaching the flight: sharp → RED, else AMBER.
  * nearest off-track front **closing** within ``closing_within_km`` → AMBER.
The per-model status is the worst graded crossing. (No co-location enrichment →
falls back to the bare intensity grade so fronts are never silently hidden.)

Free-atmosphere only: 850 hPa θe does not see low IMC / fog (§10a.2). Positions
are described qualitatively (early / mid / late route), never as exact
coordinates or times (§8 accuracy budget).
"""

from __future__ import annotations

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    FrontCrossingModel,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

# Advisory id — also referenced by ``tasks/advise.py`` to auto-enable when the
# front artifact is present.
FRONTS_ADVISORY_ID = "fronts"

_KM_PER_NM = 1.852

# Front kind → localized noun key.
_KIND_KEY = {
    "cold": "fronts.kind.cold",
    "warm": "fronts.kind.warm",
    "quasi-stationary": "fronts.kind.quasi",
}

# Intensity → severity rank (higher = worse) so we can pick the "worst" crossing.
_INTENSITY_RANK = {"significant": 1, "classical": 2, "sharp": 3}
_STATUS_RANK = {
    AdvisoryStatus.GREEN: 0,
    AdvisoryStatus.AMBER: 1,
    AdvisoryStatus.RED: 2,
}


def _grade_crossing(
    c: FrontCrossingModel,
    level_hpa: int,
    primary_level: int,
    cruise_ft: int,
    persistence_min: float,
    buffer_ft: float,
    convective_deep_ft: float,
) -> tuple[AdvisoryStatus, str]:
    """Grade one crossing → (status, reason).

    Two altitude facts matter, not just the gradient:
      * **does the weather reach the flight?** — judged by the cloud/convective
        top vs the flight band, since the θe boundary's level is not the
        weather's level;
      * **is the sharp part at the flight level?** — a sharp boundary *below*
        cruise (``level_hpa > primary_level``) is one you overfly: its core
        never touches you, so it caps at AMBER. Only convective towers, which
        grow up through your level, can still RED from below — and then only
        when deep (tops ≥ cruise + ``convective_deep_ft``); modest build-ups are
        AMBER. (Calibrated on the 2026-05-31 Dijon flight: a sharp 925 hPa cold
        front overflown at FL100 is AMBER, not RED.)

    A third fact gates RED: **vertical coherence** — a RED needs a boundary seen
    on ≥2 pressure levels (``c.vertical_levels``). A single-level detection is
    shallow/suspect and caps at AMBER. Convective towers at/above the flight's
    free-atmosphere (primary) level are exempt (graded by depth, not θe-level
    count); an *overflown* convective crossing seen on a single below-cruise level
    (e.g. a 925 hPa boundary over high terrain) is held to the coherence rule too,
    so a lone terrain-driven crossing cannot RED on its parcel EL alone (#216).
    """
    # Flickering across the window → orographic / grid artifact, not a real front.
    if c.persistence is not None and c.persistence < persistence_min:
        return AdvisoryStatus.GREEN, "flicker"
    # Vertical coherence: a RED demands a front that slopes through ≥2 levels.
    # A single-level (shallow) detection is held at AMBER — it may be real but is
    # not the deep, coherent boundary a RED implies. Unknown (None, older
    # artifact) is treated as coherent so we never suppress on missing data.
    # Convective towers at/above the primary level are exempt (flagged by depth);
    # overflown below-cruise convection is held to this rule too (#216, below).
    coherent = c.vertical_levels is None or c.vertical_levels >= 2
    co = c.co_location
    if co is None:
        # No enrichment (older artifact / no analyses) → bare intensity grade,
        # rather than silently hiding the front.
        if c.intensity == "sharp" and coherent:
            return AdvisoryStatus.RED, "sharp"
        return AdvisoryStatus.AMBER, "classical"
    if co == "dry":
        return AdvisoryStatus.GREEN, "dry"            # boundary aloft, clear → wind shift only
    # Unknown top (None) → assume it reaches: a "wet" boundary with no spanning
    # cloud layer (surface-precip-only) leaves weather_top_ft None, and we'd
    # rather flag it than silently overfly. Conservative by design — see _colocate.
    reaches = c.weather_top_ft is None or c.weather_top_ft >= cruise_ft - buffer_ft
    if not reaches:
        return AdvisoryStatus.GREEN, "below"          # weather stays below cruise — overflown
    overflown = level_hpa > primary_level             # boundary lower than cruise → you fly over its core
    if co == "convective":
        deep = c.weather_top_ft is not None and c.weather_top_ft >= cruise_ft + convective_deep_ft
        # Overflown convection needs vertical coherence to RED: a single
        # below-cruise θe crossing (a 925 hPa boundary over high terrain) is
        # shallow/suspect and caps at AMBER even when its parcel EL is deep. A
        # free-atmosphere detection (at/above the primary level) or a boundary
        # spanning ≥2 levels can still RED on depth. (#216)
        if deep and (not overflown or coherent):
            return AdvisoryStatus.RED, "convective"
        return AdvisoryStatus.AMBER, "convective"
    if co == "wet":
        if c.intensity == "sharp" and not overflown and coherent:
            return AdvisoryStatus.RED, "wet_sharp"    # sharp, coherent front at/above the flight level
        return AdvisoryStatus.AMBER, "wet"
    return AdvisoryStatus.AMBER, "partly"             # few/sct cloud band


def _where_key(distance_km: float, total_km: float) -> str:
    """Map an along-route distance to an early / mid / late descriptor."""
    frac = (distance_km / total_km) if total_km > 0 else 0.5
    if frac < 1 / 3:
        return "fronts.where.early"
    if frac < 2 / 3:
        return "fronts.where.mid"
    return "fronts.where.late"


def _describe(
    worst: FrontCrossingModel,
    reason: str,
    n_crossings: int,
    total_km: float,
    loc: str | None,
) -> str:
    """Build the localized detail for the worst graded crossing."""
    prefix = adv_t("fronts.sharp", loc) if worst.intensity == "sharp" else ""
    kind = adv_t(_KIND_KEY.get(worst.kind, "fronts.kind.quasi"), loc)
    where = adv_t(_where_key(worst.distance_km, total_km), loc)
    if n_crossings > 1:
        detail = adv_t(
            "fronts.crossing_multi", loc,
            prefix=prefix, kind=kind, where=where, count=n_crossings,
        )
    else:
        detail = adv_t("fronts.crossing", loc, prefix=prefix, kind=kind, where=where)
    # Tail: convective tops (most flight-relevant for an overflown front) take
    # priority, else the warm/cold advection tendency (§2.5).
    if reason == "convective" and worst.weather_top_ft:
        detail += adv_t("fronts.tail.convective", loc, top=int(round(worst.weather_top_ft / 100)))
    elif worst.advection > 1.0:
        detail += adv_t("fronts.tail.deteriorating", loc)
    elif worst.advection < -1.0:
        detail += adv_t("fronts.tail.improving", loc)
    return detail


@register
class FrontsEvaluator:
    """Grades Hewson front crossings + nearest closing front into an advisory."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id=FRONTS_ADVISORY_ID,
            name="Fronts (experimental)",
            short_description="Air-mass boundaries the route crosses",
            description=(
                "This advisory looks for air-mass boundaries — which can be the "
                "signal of a front — and grades them along the route or off-track. "
                "They are graded by their intensity, the sharpness of the air-mass "
                "change, and whether they are linked to bad weather: dry air is "
                "benign, while wet or convective weather raises the grade."
            ),
            category="fronts",
            default_enabled=False,
            parameters=[
                AdvisoryParameterDef(
                    key="closing_within_km",
                    label="Closing front distance",
                    description=(
                        "Flag AMBER when the nearest off-track front is closing on "
                        "the route within this distance"
                    ),
                    type="number",
                    unit="km",
                    default=300,
                    min=50,
                    max=600,
                    step=50,
                ),
                AdvisoryParameterDef(
                    key="persistence_min",
                    label="Min front persistence",
                    description=(
                        "Demote a crossing to GREEN when the gradient holds in fewer "
                        "than this fraction of the time-window frames (flickering = "
                        "likely orographic / grid artifact, not a real front)"
                    ),
                    type="number",
                    unit="",
                    default=0.5,
                    min=0.0,
                    max=1.0,
                    step=0.1,
                ),
                AdvisoryParameterDef(
                    key="altitude_buffer_ft",
                    label="Flight-band buffer",
                    description=(
                        "A front's weather is 'relevant' when its cloud/convective "
                        "top reaches cruise minus this buffer (so weather entirely "
                        "below the aircraft is overflown, not graded)"
                    ),
                    type="number",
                    unit="ft",
                    default=2000,
                    min=0,
                    max=10000,
                    step=500,
                ),
                AdvisoryParameterDef(
                    key="convective_deep_ft",
                    label="Deep-convection height",
                    description=(
                        "A convective front reaching the flight is RED only when its "
                        "tops are at least this far above cruise (deep towers); "
                        "shallower build-ups are AMBER"
                    ),
                    type="number",
                    unit="ft",
                    default=15000,
                    min=5000,
                    max=30000,
                    step=1000,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        loc = ctx.locale
        manifest = ctx.route_fronts
        closing_within_km = params.get("closing_within_km", 300)
        persistence_min = params.get("persistence_min", 0.5)
        buffer_ft = params.get("altitude_buffer_ft", 2000)
        convective_deep_ft = params.get("convective_deep_ft", 15000)
        cruise_ft = ctx.cruise_altitude_ft

        # Gating: no artifact → experimental feature was off → UNAVAILABLE.
        # Build the result directly (not via from_per_model): the aggregation
        # helpers collapse an all-UNAVAILABLE set to GREEN, but here we want the
        # advisory to genuinely read UNAVAILABLE so it stays hidden.
        if manifest is None or not manifest.per_model:
            return _unavailable(params, loc)

        total_km = ctx.total_distance_nm * _KM_PER_NM
        primary = manifest.primary_level_hPa

        # Honor the user's advisory model selection; fall back to whatever the
        # artifact carries (front models are always a subset of ecmwf/gfs/icon).
        models = [m for m in ctx.models if m in manifest.per_model] or list(manifest.per_model.keys())

        per_model: list[ModelAdvisoryResult] = []
        for model in models:
            analyses = manifest.per_model.get(model)
            if not analyses:
                continue

            # Grade against *this* model's nearest-cruise level, not a single
            # manifest-wide value — a partial snapshot may expose different
            # levels per model, so a shared primary would mis-grade overflown
            # vs. crossed (#203). Falls back to the representative level on
            # pre-#203 packs that lack the per-model map.
            model_primary = manifest.per_model_primary_hPa.get(model, primary)

            # Grade every crossing at every stored level — the boundary may sit
            # below cruise yet loft weather through it (the Dijon case) — and keep
            # the worst. Nearest off-track closing front handled separately.
            graded = [
                (*_grade_crossing(
                    c, a.level_hPa, model_primary, cruise_ft,
                    persistence_min, buffer_ft, convective_deep_ft,
                ), c)
                for a in analyses for c in a.crossings
            ]
            closing = min(
                (a.nearest for a in analyses
                 if a.nearest is not None and not a.nearest.on_track
                 and a.nearest.trend == "closing" and a.nearest.distance_km <= closing_within_km),
                key=lambda nf: nf.distance_km, default=None,
            )

            if graded:
                status, reason, worst = max(
                    graded, key=lambda g: (_STATUS_RANK[g[0]], _INTENSITY_RANK.get(g[2].intensity, 0)),
                )
                if status != AdvisoryStatus.GREEN:
                    # Distinct relevant crossings (dedup the same front seen at
                    # multiple levels into ~50 km bins).
                    n = len({round(c.distance_km / 50.0) for st, _r, c in graded if st != AdvisoryStatus.GREEN})
                    detail = _describe(worst, reason, n, total_km, loc)
                elif closing is not None:
                    status = AdvisoryStatus.AMBER
                    detail = adv_t("fronts.closing", loc, dist=int(round(closing.distance_km)))
                else:
                    # Crossings existed but all demoted (dry / below / flicker).
                    detail = adv_t("fronts.benign", loc)
            elif closing is not None:
                status = AdvisoryStatus.AMBER
                detail = adv_t("fronts.closing", loc, dist=int(round(closing.distance_km)))
            else:
                status = AdvisoryStatus.GREEN
                detail = adv_t("fronts.none", loc)

            per_model.append(ModelAdvisoryResult(
                model=model, status=status, detail=detail,
            ))

        if not per_model:
            return _unavailable(params, loc)

        return RouteAdvisoryResult.from_per_model(FRONTS_ADVISORY_ID, per_model, params)


def _unavailable(params: dict[str, float], loc: str | None) -> RouteAdvisoryResult:
    """An explicitly-UNAVAILABLE result (the aggregation helpers would otherwise
    collapse an all-UNAVAILABLE per-model set to GREEN)."""
    return RouteAdvisoryResult(
        advisory_id=FRONTS_ADVISORY_ID,
        aggregate_status=AdvisoryStatus.UNAVAILABLE,
        aggregate_detail=adv_t("no_data", loc),
        per_model=[ModelAdvisoryResult(
            model="all", status=AdvisoryStatus.UNAVAILABLE, detail=adv_t("no_data", loc),
        )],
        parameters_used=params,
    )
