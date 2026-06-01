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

Grading (design doc §2–3, qualitative per §8):
  * a **sharp** crossing (|∇θe| > 12, SIGMET-worthy) → RED
  * a **classical / significant** crossing → AMBER
  * the nearest off-track front **closing** within ``closing_within_km`` → AMBER
  * otherwise → GREEN

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
    n_crossings: int,
    total_km: float,
    loc: str | None,
) -> str:
    """Build the localized detail for the worst on-track crossing."""
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
    # Warm/cold advection tail (§2.5) — the most flight-relevant nuance.
    if worst.advection > 1.0:
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
                "Experimental. Grades free-atmosphere fronts derived from Hewson "
                "θe diagnostics along the route: a sharp crossing flags RED, a "
                "classical/significant crossing or an off-track front closing on "
                "the route flags AMBER. Available only when 'Experimental Auto "
                "Front Detection' is enabled. Advisory-only — not official SIGWX; "
                "does not see low IMC / fog (free-atmosphere systems only)."
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
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        loc = ctx.locale
        manifest = ctx.route_fronts
        closing_within_km = params.get("closing_within_km", 300)

        # Gating: no artifact → experimental feature was off → UNAVAILABLE.
        # Build the result directly (not via from_per_model): the aggregation
        # helpers collapse an all-UNAVAILABLE set to GREEN, but here we want the
        # advisory to genuinely read UNAVAILABLE so it stays hidden.
        if manifest is None or not manifest.per_model:
            return _unavailable(params, loc)

        primary = manifest.primary_level_hPa
        total_km = ctx.total_distance_nm * _KM_PER_NM

        # Honor the user's advisory model selection; fall back to whatever the
        # artifact carries (front models are always a subset of ecmwf/gfs/icon).
        models = [m for m in ctx.models if m in manifest.per_model] or list(manifest.per_model.keys())

        per_model: list[ModelAdvisoryResult] = []
        for model in models:
            analyses = manifest.per_model.get(model)
            if not analyses:
                continue
            # Match the analysis at the primary (nearest-cruise) level by its
            # level_hPa field, not by position (manifest contract).
            analysis = next((a for a in analyses if a.level_hPa == primary), analyses[0])

            crossings = list(analysis.crossings)
            nearest = analysis.nearest

            if crossings:
                worst = max(crossings, key=lambda c: _INTENSITY_RANK.get(c.intensity, 0))
                status = (
                    AdvisoryStatus.RED if worst.intensity == "sharp"
                    else AdvisoryStatus.AMBER
                )
                detail = _describe(worst, len(crossings), total_km, loc)
            elif (
                nearest is not None
                and not nearest.on_track
                and nearest.trend == "closing"
                and nearest.distance_km <= closing_within_km
            ):
                status = AdvisoryStatus.AMBER
                detail = adv_t("fronts.closing", loc, dist=int(round(nearest.distance_km)))
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
