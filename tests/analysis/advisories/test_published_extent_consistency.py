"""Every advisory publishes ONE extent measurement (#571 review finding).

The PR's whole thesis is that the number the gate graded on, the number the
sentence prints and the number the API publishes are the same number. Stage 1
removed the four competing geometries, but `ModelAdvisoryResult.build` still let
a caller pass `affected_nm` *without* its `domain_nm`, in which case the
denominator silently fell back to the whole route — so a partially-assessed
model published an `affected_pct` computed against a denominator neither the
gate nor the sentence used. Ten of thirteen evaluators had that shape.

These tests are deliberately generic: they walk the live registry rather than a
list, so an evaluator added later is covered without anyone remembering to add
it here. That is what makes the defect unrepeatable rather than merely fixed.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from weatherbrief.analysis.advisories.registry import get_catalog
from weatherbrief.analysis.route_geometry import route_extent
from weatherbrief.models.advisories import AdvisoryStatus, ModelAdvisoryResult

_ADVISORY_DIR = pathlib.Path(
    "src/weatherbrief/analysis/advisories"
)


def _build_calls():
    """Every ``ModelAdvisoryResult.build(...)`` call in the advisory package."""
    # rglob, not glob: a future subpackage would otherwise drop out of the
    # audit silently, which is the failure mode these sweeps exist to prevent.
    for path in sorted(_ADVISORY_DIR.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "build"
            ):
                yield path.name, node.lineno, {k.arg for k in node.keywords}


class TestBuildCallShape:
    def test_no_evaluator_publishes_an_nm_without_its_denominator(self):
        """`affected_nm` and `domain_nm` must travel together.

        Prefer `extent=` (which supplies both from one `RouteExtent`); passing
        them separately is allowed for the composites that measure two
        populations, but passing only the numerator is the D2 defect.
        """
        offenders = [
            f"{name}:{line}"
            for name, line, kw in _build_calls()
            if "affected_nm" in kw and "domain_nm" not in kw and "extent" not in kw
        ]
        assert not offenders, (
            "these build() calls publish an affected_nm whose denominator "
            f"silently defaults to the whole route: {offenders}"
        )

    def test_no_evaluator_publishes_a_mod_extent_without_a_mod_count(self):
        """The higher-threshold pair travels together too.

        `extent_mod` has the same failure shape the primary extent had:
        published alone it becomes a percentage over a denominator nothing
        measured. All callers pass both today; this keeps it that way.
        """
        offenders = [
            f"{name}:{line}"
            for name, line, kw in _build_calls()
            if "extent_mod" in kw and "affected_mod" not in kw
        ]
        assert not offenders, offenders

    def test_no_evaluator_passes_a_bare_mod_nm(self):
        """The higher-threshold tier goes through an extent, not a lone float.

        `affected_mod_nm=` was the last surviving bare-numerator kwarg on
        `build()`. It could not carry its own denominator, and — the way it
        actually failed — it could not say whether its miles were real, so a
        zero-length route published synthetic tier mileage beside a suppressed
        `affected_nm` (#571 review round 7). It is gone; this keeps it gone.
        """
        offenders = [
            f"{name}:{line}"
            for name, line, kw in _build_calls()
            if "affected_mod_nm" in kw
        ]
        assert not offenders, offenders

    def test_the_advisory_package_still_has_build_calls_to_check(self):
        """Guards the guard: a refactor that renamed `build` would make the
        audit above vacuously pass."""
        assert sum(1 for _ in _build_calls()) > 10


class TestPublishedNumbersAgree:
    """The published pct is the published nm over the published denominator."""

    def _results(self, ctx):
        from weatherbrief.analysis.advisories.registry import evaluate_all

        # No `enabled_ids` — the default enabled set. Passing `{}` here would
        # enable nothing and make every sweep below vacuous, which is what
        # `test_the_printed_denominator_is_the_published_one`'s final assertion
        # guards against.
        for advisory in evaluate_all(ctx):
            for m in advisory.per_model:
                yield advisory.advisory_id, m

    @pytest.fixture
    def partial_coverage_ctx(self):
        """A route where the model resolves only the middle third.

        Partial coverage is the case that separates the assessed denominator
        from the route length — with full coverage the bug is invisible, which
        is exactly why it survived.
        """
        from datetime import datetime

        from weatherbrief.analysis.advisories import RouteContext
        from weatherbrief.models import (
            CloudCoverage,
            EnhancedCloudLayer,
            RoutePointAnalysis,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        deck = [
            EnhancedCloudLayer(base_ft=6000, top_ft=11000, coverage=CloudCoverage.OVC)
        ]
        analyses = []
        for i in range(31):
            resolved = 10 <= i <= 20
            analyses.append(
                RoutePointAnalysis(
                    point_index=i, lat=48.0, lon=2.0,
                    distance_from_origin_nm=i * 10.0,
                    interpolated_time=datetime(2026, 3, 1, 10, 0),
                    forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=90.0,
                    sounding=(
                        {"gfs": SoundingAnalysis(
                            indices=ThermodynamicIndices(freezing_level_ft=5000),
                            cloud_layers=deck if 12 <= i <= 16 else [],
                        )}
                        if resolved else {}
                    ),
                )
            )
        return RouteContext(
            analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            total_distance_nm=300.0,
        )

    def test_pct_is_nm_over_domain_for_every_advisory(self, partial_coverage_ctx):
        for advisory_id, m in self._results(partial_coverage_ctx):
            if m.domain_nm <= 0:
                continue
            expected = round(100.0 * m.affected_nm / m.domain_nm, 1)
            assert m.affected_pct == pytest.approx(expected, abs=0.05), (
                f"{advisory_id}/{m.model}: affected_pct={m.affected_pct} but "
                f"{m.affected_nm}nm / {m.domain_nm}nm = {expected}"
            )

    def test_domain_never_exceeds_the_route(self, partial_coverage_ctx):
        for advisory_id, m in self._results(partial_coverage_ctx):
            assert m.domain_nm <= m.total_nm + 0.05, (
                f"{advisory_id}/{m.model}: domain_nm {m.domain_nm} exceeds the "
                f"route's {m.total_nm}"
            )

    def test_affected_never_exceeds_its_domain(self, partial_coverage_ctx):
        for advisory_id, m in self._results(partial_coverage_ctx):
            assert m.affected_nm <= m.domain_nm + 0.05, (
                f"{advisory_id}/{m.model}: affected_nm {m.affected_nm} exceeds "
                f"domain_nm {m.domain_nm}"
            )

    def test_the_printed_denominator_is_the_published_one(self, partial_coverage_ctx):
        """Where a detail prints "AnmBnm", B must be the published domain_nm.

        This is the assertion that would have caught the regression: on partial
        coverage the sentence quoted the assessed span while the field published
        the whole route.
        """
        pattern = re.compile(r"(\d+)nm/(\d+)nm")
        checked = 0
        for advisory_id, m in self._results(partial_coverage_ctx):
            match = pattern.search(m.detail)
            if not match or m.domain_nm <= 0:
                continue
            checked += 1
            printed_domain = int(match.group(2))
            assert printed_domain == round(m.domain_nm), (
                f"{advisory_id}/{m.model}: detail prints /{printed_domain}nm "
                f"but publishes domain_nm={m.domain_nm} — {m.detail!r}"
            )
        assert checked, "no advisory printed an extent; fixture proves nothing"

    def test_the_catalog_is_non_empty(self):
        """The generic sweeps above are only meaningful over a real registry."""
        assert len(get_catalog()) > 10


class TestDegenerateRoute:
    """A zero-length route publishes no miles — on *every* mile field.

    Round 6 suppressed the synthetic scaffolding on `affected_nm`/`domain_nm`
    but not on the higher-threshold tier beside them, and nothing here exercised
    `build()` directly, so the sibling field kept publishing invented mileage for
    another round (#571 review round 7). These assert the whole published
    object, not one field of it.
    """

    def _degenerate(self, **kw):
        """A pattern flight: four coincident points, three affected."""
        ext = route_extent([0.0] * 4, 0.0, [True, True, True, False])
        assert ext.distance_known is False
        return ext, ModelAdvisoryResult.build(
            model="icon",
            status=AdvisoryStatus.AMBER,
            detail="test",
            affected=3,
            total=4,
            total_distance_nm=0.0,
            extent=ext,
            **kw,
        )

    def test_no_field_publishes_synthetic_miles(self):
        ext_mod = route_extent([0.0] * 4, 0.0, [True, False, False, False])
        assert ext_mod.nm > 0, "the synthetic geometry is what we are suppressing"
        _, m = self._degenerate(affected_mod=1, extent_mod=ext_mod)
        assert (m.affected_nm, m.domain_nm, m.total_nm, m.affected_mod_nm) == (
            0.0, 0.0, 0.0, 0.0,
        )

    def test_both_percentages_are_the_real_ratio(self):
        """Suppressing the miles must not zero the coverage with them.

        `affected_mod_pct` is `_pct(nm, domain_nm)` on a normal route; with both
        zeroed that would publish 0% beside `affected_mod_points: 1`.
        """
        ext_mod = route_extent([0.0] * 4, 0.0, [True, False, False, False])
        _, m = self._degenerate(affected_mod=1, extent_mod=ext_mod)
        assert m.affected_pct == 75.0
        assert m.affected_mod_pct == 25.0
        assert m.affected_points == 3
        assert m.affected_mod_points == 1

    def test_a_real_route_is_untouched_by_the_guard(self):
        """The guard keys on `distance_known`, not on a zero somewhere."""
        ext = route_extent([0.0, 100.0, 200.0, 300.0], 300.0, [True, True, True, False])
        ext_mod = route_extent(
            [0.0, 100.0, 200.0, 300.0], 300.0, [True, False, False, False],
        )
        m = ModelAdvisoryResult.build(
            model="icon", status=AdvisoryStatus.AMBER, detail="test",
            affected=3, total=4, total_distance_nm=300.0,
            extent=ext, affected_mod=1, extent_mod=ext_mod,
        )
        assert m.affected_nm == round(ext.nm, 1)
        assert m.affected_mod_nm == round(ext_mod.nm, 1)
        assert m.domain_nm == round(ext.domain_nm, 1)
        assert m.affected_mod_pct == pytest.approx(
            100.0 * ext_mod.nm / ext.domain_nm, abs=0.1,
        )


class TestNoGeometryNoRoute:
    """An advisory with no extent AND no route length still reports honestly.

    The airport-scoped advisories (airport_wind, density_altitude, llws,
    flight_category) build with `affected=1, total=1` and no `extent=`: their
    verdict is about a point, not a span. Stage 2 derived `affected_pct` from
    distance, so on a zero-length route the numerator and the denominator were
    both 0 and every one of them published `affected_pct: 0.0` beside a RED
    status — worse than imprecise, since 0% reads as "nothing wrong" to the
    digest and the API (#571 review round 9). The point ratio is the only
    answer they have, and it is the one they gave before Stage 2.
    """

    def _airport_shaped(self, status, affected, total_distance_nm):
        return ModelAdvisoryResult.build(
            model="icon", status=status, detail="test",
            affected=affected, total=1, total_distance_nm=total_distance_nm,
        )

    def test_a_red_verdict_on_a_zero_length_route_is_not_zero_percent(self):
        m = self._airport_shaped(AdvisoryStatus.RED, 1, 0.0)
        assert m.affected_pct == 100.0
        assert m.affected_points == 1
        assert m.affected_nm == 0.0, "no route means no miles to claim"
        assert m.total_nm == 0.0

    def test_a_green_verdict_is_still_zero_percent(self):
        m = self._airport_shaped(AdvisoryStatus.GREEN, 0, 0.0)
        assert m.affected_pct == 0.0

    def test_a_normal_route_is_unchanged(self):
        """The fallback keys on `total_distance_nm <= 0`, not on the absent extent."""
        m = self._airport_shaped(AdvisoryStatus.RED, 1, 300.0)
        assert m.affected_pct == 100.0
        assert m.affected_nm == 300.0

    def test_the_four_airport_advisories_still_build_this_way(self):
        """Guards the guard: if they gain an `extent=` this class is vacuous and
        should be replaced, not left passing."""
        legacy = {
            name for name, _, kw in _build_calls()
            if "extent" not in kw and "affected_nm" not in kw
        }
        assert {
            "airport_wind.py", "density_altitude.py",
            "llws.py", "flight_category.py",
        } <= legacy
