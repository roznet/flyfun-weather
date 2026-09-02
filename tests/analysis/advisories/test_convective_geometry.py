"""Convective cutout geometry vs. the tops-below-cruise filter (#592).

``check_top_ft`` used to do two jobs at once in ``grade_convective_model``: it
was the box the client draws AND the value the tops-below-cruise filter tests.
Because the thermo fallback that resolves it was gated on "the DD floor moved
the grade", a plain ``active_track`` point — the model says "convection here"
without publishing base/top — drew a terrain-to-top ghost while complete
thermodynamic bounds sat unread in the same sounding (38 % of all flagged points
across the eval corpus; 69 % of them recoverable).

The split these tests pin:

- **geometry** resolves each missing bound from the thermo track, ungated, and
  says so in ``kind`` (``tower_estimated``);
- **the filter** keeps the old gate verbatim, so no grade can move. That is the
  invariant with teeth — opening the filter's gate is a meteorological policy
  change (meteorology-decisions), not a rendering fix — and
  ``test_thermo_bounds_never_move_an_active_track_grade`` is what enforces it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective_grading import (
    CONVECTIVE_PARAM_DEFAULTS,
    grade_convective_model,
)
from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRisk,
    RoutePointAnalysis,
    SoundingAnalysis,
)

CRUISE_FT = 8000
PARAMS = dict(CONVECTIVE_PARAM_DEFAULTS)


def _rpa(i: int, sounding: SoundingAnalysis) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i,
        lat=48.0 + i * 0.5,
        lon=2.0 + i * 0.5,
        distance_from_origin_nm=i * 20.0,
        interpolated_time=datetime(2026, 8, 27, 10, 0),
        forecast_hour=datetime(2026, 8, 27, 9, 0),
        track_deg=135.0,
        sounding={"gfs": sounding},
    )


def _ctx(sounding: SoundingAnalysis, *, points: int = 10) -> RouteContext:
    return RouteContext(
        analyses=[_rpa(i, sounding.model_copy(deep=True)) for i in range(points)],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=CRUISE_FT,
        flight_ceiling_ft=18000,
        total_distance_nm=180.0,
    )


def _sounding(
    *,
    nwp_base: float | None,
    nwp_top: float | None,
    thermo_base: float | None,
    thermo_top: float | None,
    risk: ConvectiveRisk = ConvectiveRisk.MODERATE,
    thermo_risk: ConvectiveRisk | None = None,
    fallback: bool = False,
) -> SoundingAnalysis:
    """A sounding whose ACTIVE track is the model's own scheme, with a separate
    thermo track beside it. ``fallback=True`` collapses the two (the #568 case:
    no native track at all, so the active track *is* the thermo one)."""
    thermo = ConvectiveAssessment(
        risk_level=thermo_risk if thermo_risk is not None else risk,
        cape_jkg=1800, base_ft=thermo_base, top_ft=thermo_top, method="thermo",
    )
    if fallback:
        return SoundingAnalysis(
            convective=thermo,
            convective_thermo=thermo,
            convective_nwp=None,
            convective_nwp_fallback=True,
            convective_method_effective="thermo",
        )
    nwp = ConvectiveAssessment(
        risk_level=risk, cape_jkg=1200,
        base_ft=nwp_base, top_ft=nwp_top, method="nwp",
    )
    return SoundingAnalysis(
        convective=nwp,
        convective_thermo=thermo,
        convective_nwp=nwp,
        convective_method_effective="nwp",
    )


def _regions(ctx: RouteContext):
    grade = grade_convective_model(ctx, "gfs", PARAMS)
    return grade, [cell for _, cell in grade.region_cells if cell is not None]


def _grade_tuple(grade):
    """Everything about the grade a pilot sees a colour or a number from."""
    return (
        grade.status, grade.affected, grade.affected_mod, grade.total,
        grade.dd_trigger_count, grade.below_cruise_count, grade.worst_risk,
        grade.extent.pct, grade.extent_mod.pct,
    )


class TestGeometryRecovery:
    def test_active_track_ghost_recovers_thermo_bounds(self):
        """The headline case: the model flags convection but publishes no
        base/top, and the thermo track in the SAME sounding has both."""
        ctx = _ctx(_sounding(
            nwp_base=None, nwp_top=None, thermo_base=3000, thermo_top=32000,
        ))
        _, cells = _regions(ctx)
        assert cells and all(c.kind == "tower_estimated" for c in cells)
        assert all(c.base_ft == 3000 and c.top_ft == 32000 for c in cells)
        # The grade source is untouched — only the geometry was borrowed. The
        # two are read together (kind says the bounds came from elsewhere).
        assert all(c.method_id == "nwp" for c in cells)
        assert all(c.reason_code == "active_track" for c in cells)

    def test_model_bounds_are_never_overridden(self):
        """Fill the gap, never override: a bound the model published is the
        model's own claim and stands, even beside a deeper thermo tower."""
        ctx = _ctx(_sounding(
            nwp_base=4000, nwp_top=20000, thermo_base=1000, thermo_top=45000,
        ))
        _, cells = _regions(ctx)
        assert all(c.kind == "tower" for c in cells)
        assert all(c.base_ft == 4000 and c.top_ft == 20000 for c in cells)

    def test_only_the_missing_side_is_borrowed(self):
        """A half-resolved box keeps the model's own bound and borrows the other
        — and still reads as estimated, because part of it is."""
        ctx = _ctx(_sounding(
            nwp_base=5000, nwp_top=None, thermo_base=1000, thermo_top=30000,
        ))
        _, cells = _regions(ctx)
        assert all(c.kind == "tower_estimated" for c in cells)
        assert all(c.base_ft == 5000 and c.top_ft == 30000 for c in cells)

    def test_nothing_to_borrow_stays_depth_unknown(self):
        """The surviving ~31 %: neither track has bounds. Still a region — the
        point IS flagged — but base/top stay None, and the renderers draw a
        narrow depth-unknown marker for it rather than a full-height box."""
        ctx = _ctx(_sounding(
            nwp_base=None, nwp_top=None, thermo_base=None, thermo_top=None,
        ))
        _, cells = _regions(ctx)
        assert cells and all(c.kind == "tower_unresolved" for c in cells)
        assert all(c.base_ft is None and c.top_ft is None for c in cells)

    def test_dd_fallback_box_is_not_marked_estimated(self):
        """Under the #568 fallback the active track IS the thermo track: there
        is no second source and nothing is borrowed, so the box is a plain
        tower. Marking it estimated would claim a method mix that didn't happen."""
        ctx = _ctx(_sounding(
            nwp_base=None, nwp_top=None, thermo_base=2000, thermo_top=28000,
            fallback=True,
        ))
        _, cells = _regions(ctx)
        assert all(c.kind == "tower" for c in cells)
        assert all(c.method_id == "thermo" for c in cells)
        assert all(c.reason_code == "dd_fallback" for c in cells)

    def test_dd_fallback_without_an_el_stays_depth_unknown(self):
        """The 631 unrecoverable ghosts measured on the corpus: fallback-graded
        points whose own thermo track has no EL. There is no second source to
        consult, so they stay depth-unknown."""
        ctx = _ctx(_sounding(
            nwp_base=None, nwp_top=None, thermo_base=2000, thermo_top=None,
            fallback=True,
        ))
        _, cells = _regions(ctx)
        assert all(c.kind == "tower_unresolved" for c in cells)


class TestGradeInvariance:
    """The acceptance criterion: geometry moved, grades did not."""

    @pytest.mark.parametrize("risk", [ConvectiveRisk.LOW, ConvectiveRisk.MODERATE,
                                      ConvectiveRisk.HIGH])
    @pytest.mark.parametrize("thermo_top", [4000, 12000, 45000, None])
    @pytest.mark.parametrize("nwp_top", [None, 30000])
    def test_thermo_bounds_never_move_an_active_track_grade(
        self, risk, thermo_top, nwp_top,
    ):
        """On an ``active_track`` point (the model's own scheme drives the
        grade, so the filter's fallback gate is closed by construction) the
        thermo track's geometry must be invisible to every graded number.

        ``thermo_top=4000`` is the case that would break it: 4000 + 2000
        clearance <= 8000 cruise, so a filter reading the borrowed top would
        drop the point as "tops below cruise" and turn a flagged route green.
        """
        with_thermo = _ctx(_sounding(
            nwp_base=None, nwp_top=nwp_top, thermo_base=1000, thermo_top=thermo_top,
            risk=risk,
        ))
        # Same sounding with the thermo track stripped of geometry: nothing to
        # borrow, so this is the pre-#592 behaviour for this point.
        without_thermo = _ctx(_sounding(
            nwp_base=None, nwp_top=nwp_top, thermo_base=None, thermo_top=None,
            risk=risk,
        ))
        assert _grade_tuple(grade_convective_model(with_thermo, "gfs", PARAMS)) == \
            _grade_tuple(grade_convective_model(without_thermo, "gfs", PARAMS))

    def test_dd_trigger_filter_gate_still_uses_the_thermo_top(self):
        """The one place the filter DOES consult thermo — a quiet NWP raised to
        amber by the DD trigger — keeps its #283 behaviour: a thermo tower
        topping out below cruise is still filtered out."""
        ctx = _ctx(_sounding(
            nwp_base=None, nwp_top=None, thermo_base=1000, thermo_top=4000,
            risk=ConvectiveRisk.NONE, thermo_risk=ConvectiveRisk.HIGH,
        ))
        grade = grade_convective_model(ctx, "gfs", PARAMS)
        assert grade.affected == 0
        assert grade.below_cruise_count == len(ctx.analyses)

    def test_dd_trigger_above_cruise_draws_an_estimated_box(self):
        """Same setup with a tower that does reach cruise: flagged as before,
        and now drawn from the thermo bounds instead of as a ghost."""
        ctx = _ctx(_sounding(
            nwp_base=None, nwp_top=None, thermo_base=1000, thermo_top=34000,
            risk=ConvectiveRisk.NONE, thermo_risk=ConvectiveRisk.HIGH,
        ))
        grade, cells = _regions(ctx)
        assert grade.affected == len(ctx.analyses)
        assert grade.dd_trigger_count == len(ctx.analyses)
        assert all(c.kind == "tower_estimated" for c in cells)
        assert all(c.reason_code == "dd_trigger" for c in cells)
