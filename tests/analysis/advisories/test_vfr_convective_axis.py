"""VFR feasibility grades on convection too (§22, the other composite).

`ifr_feasibility` was taught to consume the one convective grade in §22. The VFR
composite was not, and it had no convective input **at all** — its axes were
airport category, en-route cloud clearance, corridor decks and precipitation.
Reported from LFMD→EGTF 2026-08-27: HIGH convection over 47% of route published
**VFR Feasibility GREEN** directly beside **IFR Feasibility RED**, on the same
soundings and the same run. The card whose name promises the VFR answer was the
one card that never looked at the convection.

The invariant here: VFR's convective axis is the convective advisory's status for
that model, softened by — and only by — the character band, which is the axis
built to answer "can a VFR pilot operate *around* this?". The softening is
one-directional; nothing in this path may grade convection *worse* than the
convective card, and nothing may make it GREEN when that card is not.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.convective_character import (
    AVOIDABLE_BANDS as AVOIDABLE_BANDS_IMPORT,
    CHARACTER_STATUS,
    classify_route_character,
    resolve_character_params,
)
from weatherbrief.analysis.advisories.vfr_feasibility import (
    CONV_MITIGATION_ADDRESSES,
    VFRFeasibilityEvaluator,
    _least_severe,
)
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    EnhancedCloudLayer,
    ConvectiveAssessment,
    ConvectiveCharacter,
    ConvectiveRisk,
    ElevationPoint,
    ElevationProfile,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses

_MODELS = ["gfs"]


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _ctx(
    soundings: list[SoundingAnalysis],
    *,
    cruise_ft: int = 8000,
    elevation: ElevationProfile | None = None,
) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]
    resolved = _resolve_analyses(
        analyses, icing_method=None, cloud_source=None, convective_method="nwp",
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=elevation, models=_MODELS,
        cruise_altitude_ft=cruise_ft, flight_ceiling_ft=cruise_ft + 4000,
        total_distance_nm=len(soundings) * 20.0,
    )


def _terrain(profile: list[tuple[float, float]], total_nm: float) -> ElevationProfile:
    """Terrain from ``(distance_nm, elevation_ft)`` samples."""
    points = [
        ElevationPoint(distance_nm=d, elevation_ft=e, lat=48.0, lon=2.0)
        for d, e in profile
    ]
    return ElevationProfile(
        route_name="test", points=points,
        max_elevation_ft=max(e for _, e in profile),
        total_distance_nm=total_nm,
    )


def _nwp_cell(risk: ConvectiveRisk, *, base_ft: int = 3000, top_ft: int = 30000):
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=risk, cape_jkg=2500, base_ft=base_ft, top_ft=top_ft,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=risk, cape_jkg=2500, base_ft=base_ft, top_ft=top_ft,
        ),
    )


def _quiet():
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=None, base_ft=None, top_ft=None,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=10, base_ft=None, top_ft=None,
        ),
    )


def _status_of(evaluator, ctx: RouteContext) -> AdvisoryStatus:
    res = evaluator.evaluate(ctx, _defaults(evaluator))
    return next(m for m in res.per_model if m.model == "gfs").status


def _vfr(ctx: RouteContext):
    res = VFRFeasibilityEvaluator.evaluate(ctx, _defaults(VFRFeasibilityEvaluator))
    return next(m for m in res.per_model if m.model == "gfs")


_SCENARIOS = {
    "nwp_high_everywhere": [_nwp_cell(ConvectiveRisk.HIGH)] * 6,
    "nwp_high_sparse": [_nwp_cell(ConvectiveRisk.HIGH)] + [_quiet()] * 5,
    "nwp_moderate": [_nwp_cell(ConvectiveRisk.MODERATE)] + [_quiet()] * 5,
    "quiet": [_quiet()] * 6,
}


class TestVFRConsumesTheConvectiveGrade:

    def test_convection_reaches_the_vfr_card_at_all(self):
        """The reported regression: HIGH convection must not read GREEN here."""
        ctx = _ctx(_SCENARIOS["nwp_high_everywhere"])
        assert _status_of(ConvectiveEvaluator, ctx) == AdvisoryStatus.RED
        assert _vfr(ctx).status != AdvisoryStatus.GREEN

    @pytest.mark.parametrize("name", sorted(_SCENARIOS))
    def test_vfr_is_never_calmer_than_the_softened_convective_axis(self, name):
        """VFR >= the convective colour after the sanctioned character softening.

        Stated as a bound rather than an equality because the composite's other
        axes (airport, cloud, corridor, precip) may legitimately push it worse.
        What must never happen is VFR reading calmer than the convection it is
        supposed to have consumed.
        """
        ctx = _ctx(_SCENARIOS[name])
        conv = _status_of(ConvectiveEvaluator, ctx)
        if conv in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE):
            return
        character = classify_route_character(ctx, "gfs", resolve_character_params(ctx))
        floor = _least_severe(
            conv,
            CHARACTER_STATUS.get(character, AdvisoryStatus.RED)
            if character is not None else AdvisoryStatus.RED,
        )
        assert AdvisoryStatus.worst([_vfr(ctx).status, floor]) == _vfr(ctx).status

    def test_quiet_air_adds_no_convective_colour(self):
        """The axis may add a colour; it must not invent one."""
        ctx = _ctx(_SCENARIOS["quiet"])
        assert _status_of(ConvectiveEvaluator, ctx) in (
            AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE,
        )
        assert "convection" not in _vfr(ctx).detail.lower()

    def test_the_sentence_names_the_avoidability(self):
        """A pilot reading the card learns the band, not just the tier."""
        ctx = _ctx(_SCENARIOS["nwp_high_everywhere"])
        detail = _vfr(ctx).detail.lower()
        assert "convection" in detail
        assert "circumnavigable" in detail


class TestTheCapOnlySoftens:
    """`_least_severe` is the cap's one direction, isolated from the fixtures."""

    def test_takes_the_calmer_of_the_two(self):
        assert _least_severe(AdvisoryStatus.RED, AdvisoryStatus.AMBER) == AdvisoryStatus.AMBER
        assert _least_severe(AdvisoryStatus.AMBER, AdvisoryStatus.RED) == AdvisoryStatus.AMBER

    def test_a_worse_band_can_never_escalate_the_activity_grade(self):
        """An ISOLATED band must not lift AMBER activity to RED, and a RED band
        must not lift a GREEN axis — the composite adds colour through `worst`
        over all axes, never through this cap."""
        assert _least_severe(AdvisoryStatus.AMBER, AdvisoryStatus.RED) == AdvisoryStatus.AMBER
        assert _least_severe(AdvisoryStatus.GREEN, AdvisoryStatus.RED) == AdvisoryStatus.GREEN

    def test_equal_statuses_are_idempotent(self):
        for s in (AdvisoryStatus.GREEN, AdvisoryStatus.AMBER, AdvisoryStatus.RED):
            assert _least_severe(s, s) == s

    def test_every_character_band_maps_to_a_status(self):
        """A band with no mapping would silently read GREEN through `.get`."""
        for band in ConvectiveCharacter:
            assert band in CHARACTER_STATUS


class TestBelowBaseEscape:
    """`below_base_escapes` answers per convective cluster, not per route (#593).

    The route-wide test it replaces could not fire on a real flight: to be
    "below the cells" you had to be below *every* resolved base on the whole
    route. Pilot-reported ground truth on LFMD→EGTF 2026-08-27 is the shape —
    a mid-route system the pilot flew underneath around FL150, and a *different*
    system hours later at the arrival end with towers based near 1,500 ft. The
    arrival cells condemned every candidate level for the whole flight, so no
    escape was ever offered for the mid-route system that demonstrably had one.
    """

    def _escapes(self, ctx):
        from weatherbrief.analysis.advisories.convective_character import (
            below_base_escapes, resolve_character_params,
        )
        return below_base_escapes(ctx, "gfs", resolve_character_params(ctx))

    def _route(self, *, cruise_ft: int, base_ft: int):
        """One cell at the origin, quiet elsewhere. `_ctx` sets the ceiling to
        cruise + 4,000 ft and leaves elevation None, so the candidate ladder
        floors at `mitigation_min_base_agl_ft` (3,000 ft) — no terrain to
        confound the geometry under test."""
        soundings = [
            _nwp_cell(ConvectiveRisk.MODERATE, base_ft=base_ft, top_ft=30000),
        ] + [_quiet()] * 5
        return _ctx(soundings, cruise_ft=cruise_ft)

    def _two_clusters(self, *, near_base_ft: int, far_base_ft: int, **kwargs):
        """Two two-point convective clusters, separated by quiet air.

        12 points at 20 nm keep the realized coverage inside the SCATTERED band
        (~33 %), so the route-wide band stays one a VFR pilot can operate around
        and the *geometry* is the only thing under test.
        """
        soundings = [_quiet()] * 12
        for i in (1, 2):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=near_base_ft, top_ft=30000
            )
        for i in (7, 8):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=far_base_ft, top_ft=30000
            )
        return _ctx(soundings, **kwargs)

    def test_finds_a_level_beneath_the_cells(self):
        # Cells based at 12,000 ft, filed cruise 16,000 ft — inside the layer.
        ctx = self._route(cruise_ft=16000, base_ft=12000)
        escapes = self._escapes(ctx)
        assert len(escapes) == 1
        escape = escapes[0]
        # Must clear the 2,000 ft default below-base buffer, and be a descent.
        assert escape.altitude_ft <= 12000 - 2000
        assert escape.altitude_ft < ctx.cruise_altitude_ft
        assert escape.band in AVOIDABLE_BANDS_IMPORT

    def test_publishes_the_base_it_reasoned_from(self):
        """The tip names the base as well as the altitude.

        Modelled convective bases read low (ICON grossly so on the reported
        flight), which makes the offered level conservative — the pilot needs the
        number it came from to check it against what they can actually see.
        """
        ctx = self._route(cruise_ft=16000, base_ft=12000)
        escape = self._escapes(ctx)[0]
        assert escape.base_fl == pytest.approx(120, abs=1)
        assert escape.margin_ft >= 2000

    def test_no_escape_when_the_cells_are_based_below_every_candidate(self):
        """A cell based low enough to condemn the ladder still has no answer."""
        ctx = self._route(cruise_ft=16000, base_ft=1500)
        assert self._escapes(ctx) == []

    def test_a_condemned_cluster_does_not_suppress_a_flyable_one(self):
        """The #593 bug itself: the arrival system used to answer for the route.

        Mid-route cells based at 12,000 ft are flyable underneath; arrival-end
        cells based at 1,500 ft are not. Route-wide, the second set makes every
        candidate `within_layer` and nothing is offered. Per cluster, the first
        set keeps its escape.
        """
        ctx = self._two_clusters(
            near_base_ft=12000, far_base_ft=1500, cruise_ft=16000
        )
        escapes = self._escapes(ctx)
        assert len(escapes) == 1
        # ...and it is the mid-route cluster (points 1-2 → 10-50 nm), named with
        # the miles it applies to rather than implying the whole flight.
        assert escapes[0].dist_from_nm == pytest.approx(10.0)
        assert escapes[0].dist_to_nm == pytest.approx(50.0)
        assert escapes[0].dist_to_nm < ctx.total_distance_nm

    def test_both_clusters_answered_when_both_have_an_escape(self):
        """Per-cluster does not mean one-only: each flyable cluster is reported.

        The two clusters here are 100 nm apart with quiet air between them, so
        the post-sweep merge (which folds *neighbouring* clusters offered the
        same level) must not collapse them into one span that claims miles
        neither was tested over... it may, since the gap carries no cells; what
        it must not do is drop one of them.
        """
        ctx = self._two_clusters(
            near_base_ft=12000, far_base_ft=13000, cruise_ft=16000
        )
        escapes = self._escapes(ctx)
        assert escapes
        covered_to = max(e.dist_to_nm for e in escapes)
        covered_from = min(e.dist_from_nm for e in escapes)
        assert covered_from == pytest.approx(10.0)
        assert covered_to == pytest.approx(170.0)

    def test_a_cluster_that_never_cleared_is_not_merged_over(self):
        """Review round 1, Critical: adjacency in the *results* is not adjacency.

        Three clusters, A-B-C. A and C clear at the same level; B (low-based,
        the arrival-end shape) never clears at any level. B is simply absent
        from the results, so A and C land next to each other in the list — and
        a merge keyed off list adjacency folds them into one span that runs
        straight through B, asserting "you would be below the cells there" over
        cells that were explicitly never confirmed clear. That is #593's own
        false assurance, returning for three clusters.
        """
        soundings = [_quiet()] * 20
        for i in (1, 2, 13, 14):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=12000, top_ft=30000
            )
        for i in (7, 8):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=1500, top_ft=30000
            )
        escapes = self._escapes(_ctx(soundings, cruise_ft=16000))
        assert len(escapes) == 2, "the unresolved middle cluster was merged over"
        assert (escapes[0].dist_from_nm, escapes[0].dist_to_nm) == (10.0, 50.0)
        assert (escapes[1].dist_from_nm, escapes[1].dist_to_nm) == (250.0, 290.0)
        # Nothing offered may span the middle cluster's cells (130-170 nm).
        assert not any(
            e.dist_from_nm < 170.0 < e.dist_to_nm or e.dist_from_nm < 130.0 < e.dist_to_nm
            for e in escapes
        )

    def test_a_widespread_band_is_not_an_escape(self):
        """Both halves are required: under the bases is not enough.

        Cells over 100 % of a route are WIDESPREAD however low you fly — no
        altitude fixes horizontal extent — so even though every candidate level
        is comfortably below the 14,000 ft bases, nothing may be offered.
        """
        ctx = _ctx(
            [_nwp_cell(ConvectiveRisk.MODERATE, base_ft=14000, top_ft=32000)] * 8,
            cruise_ft=16000,
        )
        assert classify_route_character(
            ctx, "gfs", resolve_character_params(ctx)
        ) not in AVOIDABLE_BANDS_IMPORT
        assert self._escapes(ctx) == []

    def test_an_embedded_route_refuses_every_cluster(self):
        """The band half is route-wide, and vetoes even a cluster that is clear.

        A deep OVC deck over the *second* cluster hides its cells at every
        candidate level, so the route re-derives EMBEDDED there — "you cannot
        see them" — and nothing is offered, although the first cluster's own
        geometry reads `clear` at 10,000 ft. Deliberate, and the one coupling
        the per-cluster split does NOT remove: the band is a property of the
        whole flight and is what the character card publishes, and re-deriving
        it over a cluster alone is meaningless (a cluster is contiguous cells,
        so its internal coverage is 100 % and every one would read WIDESPREAD).
        """
        from weatherbrief.analysis.advisories.convective_character import (
            _below_base_geometry, build_character_points, classify_inputs,
            resolve_character_params,
        )
        # Deep enough that no candidate below the 16,000 ft cruise sits above it
        # — otherwise the second cluster has a legitimate escape on top of the
        # deck and under its own 20,000 ft bases, which is a different answer.
        deck = EnhancedCloudLayer(
            base_ft=3000, top_ft=15000, coverage=CloudCoverage.OVC,
        )
        soundings = [_quiet()] * 16
        for i in (1, 2):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=12000, top_ft=30000
            )
        for i in (9, 10, 11):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=20000, top_ft=32000
            ).model_copy(update={
                "cloud_layers": [deck],
                "cloud_cover_low_pct": 95.0,
                "cloud_cover_mid_pct": 95.0,
                "cloud_cover_high_pct": 95.0,
            })
        ctx = _ctx(soundings, cruise_ft=16000)
        params = resolve_character_params(ctx)

        # The first cluster genuinely clears at 10,000 ft — so what refuses the
        # escape below is the band, not the geometry.
        at_10k = build_character_points(ctx, "gfs", params, cruise_ft=10000.0)
        assert _below_base_geometry(
            [at_10k.points[i] for i in (1, 2)], 10000.0, 2000.0
        ).kind == "clear"
        # ...and the route re-derived at that same level is EMBEDDED. (At the
        # filed 16,000 ft cruise it is not — the deck tops out at 13,000 — which
        # is exactly why the sweep re-derives the band per candidate rather than
        # reusing the card's.)
        assert classify_inputs(ctx, "gfs", params, at_10k) is ConvectiveCharacter.EMBEDDED

        assert self._escapes(ctx) == []

    def test_the_terrain_floor_is_the_clusters_own(self):
        """Open question 1: yes, per cluster.

        The ground-truth flight floored at 8,000 ft because of the Alps at
        *departure* — irrelevant to a cluster over flat northern France 300 nm
        later. Here 12,000 ft peaks sit under the first cluster and 500 ft
        terrain under the second. A route-wide floor (12,000 + 3,000 AGL =
        15,000 ft) leaves no candidate below the 16,000 ft cruise that clears
        12,000 ft bases, so nothing would be offered at all.
        """
        ctx = self._two_clusters(
            near_base_ft=12000, far_base_ft=12000, cruise_ft=16000,
            elevation=_terrain(
                [(0.0, 500.0), (20.0, 12000.0), (40.0, 12000.0), (60.0, 500.0),
                 (240.0, 500.0)],
                240.0,
            ),
        )
        escapes = self._escapes(ctx)
        assert len(escapes) == 1
        assert escapes[0].dist_from_nm == pytest.approx(130.0)
        assert escapes[0].altitude_ft <= 10000

    def test_the_merge_does_not_offer_a_level_below_the_terrain_between(self):
        """Folding two clusters together must re-check the col between them.

        Both clusters are over flat ground and resolve to the same level; the
        20 nm between them is a 12,000 ft ridge. Merging without re-checking
        would advertise a single span at a level that flies into it.
        """
        soundings = [_quiet()] * 12
        for i in (1, 2, 4, 5):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=12000, top_ft=30000
            )
        ctx = _ctx(
            soundings, cruise_ft=16000,
            elevation=_terrain(
                [(0.0, 500.0), (50.0, 500.0), (60.0, 12000.0), (70.0, 500.0),
                 (240.0, 500.0)],
                240.0,
            ),
        )
        escapes = self._escapes(ctx)
        assert len(escapes) == 2, "the ridge between them was merged away"
        assert all(e.dist_to_nm - e.dist_from_nm < 60.0 for e in escapes)


class TestTheEscapeIsAdviceOnly:
    """Acceptance criterion: no mitigation moves any grade.

    The `Mitigation` contract is that it never changes the advisory's status —
    the *altitude table* is what shows the flight becoming feasible lower down.
    Asserted by grading the same route with the escape sweep neutered and
    comparing every per-model status, so a future change that lets the tip leak
    into the grade fails here rather than in a briefing.
    """

    def _route(self):
        soundings = [_quiet()] * 12
        for i in (1, 2):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=12000, top_ft=30000
            )
        for i in (7, 8):
            soundings[i] = _nwp_cell(
                ConvectiveRisk.MODERATE, base_ft=1500, top_ft=30000
            )
        return _ctx(soundings, cruise_ft=16000)

    def test_the_tip_is_actually_offered_on_this_route(self):
        """Guards the comparison below from passing vacuously."""
        result = _vfr(self._route())
        assert any(
            m.addresses == CONV_MITIGATION_ADDRESSES
            for m in (result.mitigations or [])
        )

    def test_grades_are_identical_with_and_without_the_mitigation(self, monkeypatch):
        ctx = self._route()
        with_tip = VFRFeasibilityEvaluator.evaluate(
            ctx, _defaults(VFRFeasibilityEvaluator)
        )
        monkeypatch.setattr(
            "weatherbrief.analysis.advisories.vfr_feasibility.below_base_escapes",
            lambda *a, **k: [],
        )
        without = VFRFeasibilityEvaluator.evaluate(
            ctx, _defaults(VFRFeasibilityEvaluator)
        )
        assert with_tip.aggregate_status == without.aggregate_status
        assert [(m.model, m.status) for m in with_tip.per_model] == [
            (m.model, m.status) for m in without.per_model
        ]


class TestTheClusterPrimitives:
    """The two library pieces the per-cluster escape is built on (#593).

    Tested directly as well as through the sweep: a mis-measured cluster extent
    or terrain span produces a *plausible* wrong altitude, which the end-to-end
    assertions above would not necessarily catch.
    """

    def _points(self, flags: list[bool], spacing_nm: float = 20.0):
        from weatherbrief.analysis.sounding.convective import ConvCharPoint
        return [
            ConvCharPoint(
                is_convective=f, realized=f, embedded=False,
                k_index=None, total_totals=None, distance_nm=i * spacing_nm,
            )
            for i, f in enumerate(flags)
        ]

    def test_runs_split_on_a_single_quiet_point(self):
        """Strictly contiguous — no gap tolerance, by design."""
        from weatherbrief.analysis.sounding.convective import convective_clusters
        clusters = convective_clusters(
            self._points([False, True, True, False, True, False, False]), 140.0
        )
        assert [c.indices for c in clusters] == [(1, 2), (4,)]

    def test_extents_use_the_shared_midpoint_cell_geometry(self):
        """Each cluster owns its points' cells, halfway to the neighbours.

        Same convention the coverage band and the EMBEDDED gate measure on, so a
        tip's miles cannot describe different geometry from the grade's.
        """
        from weatherbrief.analysis.sounding.convective import convective_clusters
        clusters = convective_clusters(
            self._points([False, True, True, False, True, False, False]), 140.0
        )
        assert (clusters[0].dist_from_nm, clusters[0].dist_to_nm) == (10.0, 50.0)
        assert (clusters[1].dist_from_nm, clusters[1].dist_to_nm) == (70.0, 90.0)

    def test_a_run_touching_either_end_is_closed_by_the_route(self):
        from weatherbrief.analysis.sounding.convective import convective_clusters
        clusters = convective_clusters(self._points([True, False, True]), 40.0)
        assert (clusters[0].dist_from_nm, clusters[0].dist_to_nm) == (0.0, 10.0)
        assert (clusters[1].dist_from_nm, clusters[1].dist_to_nm) == (30.0, 40.0)

    def test_no_realized_cells_is_no_clusters(self):
        from weatherbrief.analysis.sounding.convective import convective_clusters
        assert convective_clusters(self._points([False] * 4), 80.0) == []
        assert convective_clusters([], 80.0) == []

    def test_terrain_span_ignores_peaks_outside_it(self):
        """The whole point of the helper: the Alps at departure are not the floor
        for a descent 300 nm later."""
        from weatherbrief.analysis.advisories._helpers import max_terrain_between
        profile = _terrain(
            [(0.0, 500.0), (20.0, 12000.0), (40.0, 500.0), (200.0, 800.0)], 200.0
        )
        assert max_terrain_between(profile, 0.0, 40.0) == 12000.0
        assert max_terrain_between(profile, 60.0, 200.0) == pytest.approx(800.0, abs=50)

    def test_a_span_narrower_than_the_sampling_still_measures(self):
        """Both endpoints are interpolated, so a short span is not `None`."""
        from weatherbrief.analysis.advisories._helpers import max_terrain_between
        profile = _terrain([(0.0, 0.0), (100.0, 1000.0)], 100.0)
        assert max_terrain_between(profile, 50.0, 60.0) == pytest.approx(600.0)

    def test_no_profile_is_unknown_not_sea_level(self):
        from weatherbrief.analysis.advisories._helpers import max_terrain_between
        assert max_terrain_between(None, 0.0, 100.0) is None


class TestAnUncharacterisedBandNeverSoftens:
    """Review round 1, Critical: `NONE`/`UNKNOWN` must not zero a flagged axis.

    The first cut of the cap read `CHARACTER_STATUS`, which is the character
    *card's own grade* — where NONE → GREEN is correct ("nothing to
    characterise"). Used as a cap it turned "no answer" into "it's fine":
    `_least_severe(RED, GREEN)` is GREEN. That is the #391 false-clear, and the
    §22 divergence this whole axis exists to close, reintroduced through it.

    The live reproduction is a LOW-risk route: the character axis floors at
    `min_risk` MODERATE while grading floors at LOW, so an AMBER convective card
    yields character NONE — and the composite published GREEN with a detail line
    that read "not circumnavigable VFR" beside it.
    """

    def _low_risk_route(self):
        return _ctx([_nwp_cell(ConvectiveRisk.LOW)] * 8)

    def test_low_risk_route_keeps_the_convective_colour(self):
        ctx = self._low_risk_route()
        assert _status_of(ConvectiveEvaluator, ctx) == AdvisoryStatus.AMBER
        assert classify_route_character(
            ctx, "gfs", resolve_character_params(ctx)
        ) is ConvectiveCharacter.NONE
        # The bug published GREEN here.
        assert _vfr(ctx).status == AdvisoryStatus.AMBER

    def test_it_claims_neither_avoidability_nor_its_absence(self):
        """The sentence tracks what was established, not the colour.

        "circumnavigable" would be the false clear in prose; "not
        circumnavigable" (what the buggy ternary printed) asserts a judgement the
        character axis never made.
        """
        detail = _vfr(self._low_risk_route()).detail.lower()
        assert "avoidability not established" in detail
        assert "circumnavigable" not in detail

    def test_the_cap_still_fires_for_a_band_that_did_establish_it(self):
        """The guard must not have disabled the softening it guards."""
        ctx = _ctx([_nwp_cell(ConvectiveRisk.HIGH)] + [_quiet()] * 7)
        assert _status_of(ConvectiveEvaluator, ctx) == AdvisoryStatus.RED
        assert classify_route_character(
            ctx, "gfs", resolve_character_params(ctx)
        ) is ConvectiveCharacter.ISOLATED
        result = _vfr(ctx)
        assert result.status == AdvisoryStatus.AMBER
        assert "circumnavigable with see-and-avoid" in result.detail.lower()


class TestConvectionReachesTheHighlight:
    """Review round 1, Important: the graded axis must also be drawn.

    Convection can be the sole driver of the composite's colour — the reported
    LFMD→EGTF shape had airport, cloud, corridor and precip all clear. The
    highlight built only cloud/corridor geometry, so it returned an empty
    `regions` list and an all-GREEN ribbon; both renderers skip the scrim on
    empty regions, so the cross-section drew a spotless chart beside an AMBER
    badge.
    """

    def _conv_only_route(self):
        # Cells based well above cruise so the cloud axes stay quiet and
        # convection is the only thing flagging.
        return _ctx(
            [_nwp_cell(ConvectiveRisk.HIGH, base_ft=14000, top_ft=32000)] * 4
            + [_quiet()] * 4,
            cruise_ft=8000,
        )

    def test_regions_are_not_empty_when_convection_drives_the_grade(self):
        ctx = self._conv_only_route()
        result = _vfr(ctx)
        assert result.status != AdvisoryStatus.GREEN
        regions = (result.highlights.regions if result.highlights else [])
        assert regions, "convective grade with no geometry — the scrim would skip"
        assert any(r.metric_id == "convective_risk" for r in regions)

    def test_the_ribbon_is_not_all_green_under_a_flagged_grade(self):
        result = _vfr(self._conv_only_route())
        severities = {s.severity for s in result.highlights.ribbon}
        assert severities - {"green"}, f"ribbon reads all-green: {severities}"
