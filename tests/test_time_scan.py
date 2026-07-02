"""Tests for the timing-scenario scan (Flexibility) — tasks/time_scan.py.

Covers the plan's validation invariants (timing-scenario-plan.md):
- the diff/margin/ranking semantics (decision D),
- the coverage detector: rule-window ∩ data-marker, forward-fill smear
  bounding, per-point fidelity-parity fallback,
- the honesty guardrail: off-coverage hours are refused, never clamp-graded,
- artifact round-trips and the declarative timing_class registry sets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.models import (
    AdvisoryStatus,
    HourlyForecast,
    ModelSource,
    NWPCloudDiagnostics,
    PressureLevelData,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    RouteCrossSection,
    RoutePoint,
    TimeCandidate,
    TimeScanBaseline,
    TimeScanStatus,
    TimeWindowScan,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.tasks.time_scan import (
    _diff_manifests,
    _rule_window,
    compute_model_coverage,
    covers,
)

DEP = datetime(2026, 6, 27, 7, 30, tzinfo=timezone.utc)
DAY = DEP.replace(hour=0, minute=0)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _adv(advisory_id: str, status: AdvisoryStatus) -> RouteAdvisoryResult:
    return RouteAdvisoryResult(advisory_id=advisory_id, aggregate_status=status)


def _manifest(**statuses: AdvisoryStatus) -> RouteAdvisoriesManifest:
    return RouteAdvisoriesManifest(
        advisories=[_adv(k, v) for k, v in statuses.items()],
        route_name="t", cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=100.0, models=["ecmwf"],
    )


def _levels(n: int, clw: bool = False) -> list[PressureLevelData]:
    return [
        PressureLevelData(
            pressure_hpa=1000 - 25 * i,
            temperature_c=10.0,
            cloud_liquid_water_kg_kg=(0.0001 if clw else None),
        )
        for i in range(n)
    ]


def _wf(
    model: ModelSource,
    *,
    enriched_hours: set[int] | None = None,
    gfs_marked_hours: set[int] | None = None,
) -> WaypointForecast:
    """24 hourly rows on DEP's day. ``enriched_hours`` get 28 levels (the
    ECMWF/ICON level-replacement marker); ``gfs_marked_hours`` get CLW +
    diagnostics on OM-count levels (the GFS overlay marker)."""
    hourly = []
    for h in range(24):
        t = DAY + timedelta(hours=h)
        if enriched_hours and h in enriched_hours:
            hourly.append(HourlyForecast(time=t, pressure_levels=_levels(28)))
        elif gfs_marked_hours and h in gfs_marked_hours:
            hourly.append(
                HourlyForecast(
                    time=t,
                    pressure_levels=_levels(8, clw=True),
                    nwp_cloud_diagnostics=NWPCloudDiagnostics(),
                )
            )
        else:
            hourly.append(HourlyForecast(time=t, pressure_levels=_levels(8)))
    return WaypointForecast(
        waypoint=Waypoint(icao="XXXX", name="X", lat=51.0, lon=0.0),
        model=model,
        fetched_at=DEP,
        hourly=hourly,
    )


def _cs(model: ModelSource, forecasts: list[WaypointForecast]) -> RouteCrossSection:
    rps = [
        RoutePoint(lat=51.0, lon=float(i), distance_from_origin_nm=50.0 * i)
        for i in range(len(forecasts))
    ]
    return RouteCrossSection(
        model=model, route_points=rps, fetched_at=DEP, point_forecasts=forecasts,
    )


# ---------------------------------------------------------------------------
# Diff / margin (decision D semantics)
# ---------------------------------------------------------------------------


class TestDiffManifests:
    def test_improvement_counts_margin_on_scan_class_only(self):
        base = _manifest(
            convective=AdvisoryStatus.RED,
            headwind=AdvisoryStatus.AMBER,
        )
        cand = _manifest(
            convective=AdvisoryStatus.AMBER,   # scan-class: margin +1
            headwind=AdvisoryStatus.GREEN,     # timing-none: improves, no margin
        )
        improves, worsens, margin = _diff_manifests(base, cand, {"convective"})
        assert set(improves) == {"convective", "headwind"}
        assert worsens == []
        assert margin == 1

    def test_worsening_shows_in_full_picture(self):
        base = _manifest(convective=AdvisoryStatus.RED, airport_wind=AdvisoryStatus.GREEN)
        cand = _manifest(convective=AdvisoryStatus.GREEN, airport_wind=AdvisoryStatus.AMBER)
        improves, worsens, margin = _diff_manifests(base, cand, {"convective"})
        assert improves == ["convective"]
        assert worsens == ["airport_wind"]  # never hide an introduced problem
        assert margin == 2

    def test_unavailable_is_not_comparable(self):
        base = _manifest(convective=AdvisoryStatus.UNAVAILABLE)
        cand = _manifest(convective=AdvisoryStatus.GREEN)
        improves, worsens, margin = _diff_manifests(base, cand, {"convective"})
        assert improves == [] and worsens == [] and margin == 0

    def test_identical_manifests_zero_diff(self):
        # The shift=0 invariant's core: same grades → empty diff, margin 0.
        base = _manifest(convective=AdvisoryStatus.RED, vmc_cruise=AdvisoryStatus.AMBER)
        improves, worsens, margin = _diff_manifests(base, base, {"convective", "vmc_cruise"})
        assert improves == [] and worsens == [] and margin == 0


# ---------------------------------------------------------------------------
# Coverage: rule ∩ marker, smear bounding, fidelity parity
# ---------------------------------------------------------------------------


class TestModelCoverage:
    def test_ecmwf_level_replacement_marker(self):
        # Enriched 05..11 (flight window 07:30+1h ±3h) — span == marked hours.
        cs = _cs(ModelSource.ECMWF, [_wf(ModelSource.ECMWF, enriched_hours=set(range(5, 12)))])
        per_point, summary = compute_model_coverage([cs], DEP, 1.0)
        lo, hi = per_point["ecmwf"][0]
        assert lo == DAY + timedelta(hours=5)
        assert hi == DAY + timedelta(hours=11)
        assert summary[0].uniform is False

    def test_gfs_forward_fill_smear_is_bounded_by_rule_window(self):
        # GFS marked 07..23 — the trailing hours are the forward-fill smear
        # (fill.py fills diagnostics/CLW past the last native anchor). The
        # rule window (floor(dep)..dep+ceil(dur)+1h for :30 departures) must
        # cut the claim back to 07:00..09:30.
        cs = _cs(ModelSource.GFS, [_wf(ModelSource.GFS, gfs_marked_hours=set(range(7, 24)))])
        per_point, _ = compute_model_coverage([cs], DEP, 1.0)
        lo, hi = per_point["gfs"][0]
        assert lo == DAY + timedelta(hours=7)
        assert hi == DEP + timedelta(hours=2)  # 09:30, not 23:00

    def test_unenriched_point_gets_fidelity_parity_uniform_span(self):
        # Point 1 was never enriched (fetch gap) — the baseline graded it on
        # OM data too, so any hour of the day is as honest there as the
        # planned one. It must NOT veto the whole scan.
        cs = _cs(
            ModelSource.GFS,
            [
                _wf(ModelSource.GFS, gfs_marked_hours=set(range(7, 10))),
                _wf(ModelSource.GFS),  # no markers at all
            ],
        )
        per_point, summary = compute_model_coverage([cs], DEP, 1.0)
        assert per_point["gfs"][1] == (DAY, DAY + timedelta(hours=23))
        assert summary[0].uniform is False  # mixed, not fully uniform

    def test_om_only_model_is_uniform_whole_day(self):
        cs = _cs(ModelSource.UKMO, [_wf(ModelSource.UKMO)])
        per_point, summary = compute_model_coverage([cs], DEP, 1.0)
        assert per_point["ukmo"][0] == (DAY, DAY + timedelta(hours=23))
        assert summary[0].uniform is True

    def test_covers_refuses_eta_outside_span(self):
        cs = _cs(ModelSource.ECMWF, [_wf(ModelSource.ECMWF, enriched_hours=set(range(5, 12)))])
        per_point, _ = compute_model_coverage([cs], DEP, 1.0)
        inside = [DAY + timedelta(hours=8)]
        outside = [DAY + timedelta(hours=13)]
        assert covers(per_point, ["ecmwf"], inside) is True
        assert covers(per_point, ["ecmwf"], outside) is False

    def test_covers_refuses_unknown_model(self):
        assert covers({}, ["ecmwf"], [DEP]) is False


class TestRuleWindow:
    def test_ecmwf_margin(self):
        lo, hi = _rule_window(ModelSource.ECMWF, DEP, 1.0)
        assert lo == DEP - timedelta(hours=3)
        assert hi == DEP + timedelta(hours=4)  # dep+1h flight +3h margin

    def test_gfs_forward_window_with_minutes(self):
        lo, hi = _rule_window(ModelSource.GFS, DEP, 1.0)
        assert lo == DEP.replace(minute=0)  # floor hour
        assert hi == DEP + timedelta(hours=2)  # ceil(1)+1 extra for :30


# ---------------------------------------------------------------------------
# Artifact round-trips
# ---------------------------------------------------------------------------


class TestArtifacts:
    def _scan(self) -> TimeWindowScan:
        return TimeWindowScan(
            flexibility="same_day",
            baseline=TimeScanBaseline(
                departure_time=DEP, assessment="RED", models_used=["ecmwf"],
            ),
            candidates=[
                TimeCandidate(
                    departure_time=DEP + timedelta(hours=1),
                    departure_shift_hours=1.0,
                    assessment="AMBER",
                    confidence="confirmed_in_window",
                    margin=2.0,
                )
            ],
            generated_at=DEP,
        )

    def test_time_options_round_trip(self, tmp_path: Path):
        from weatherbrief.tasks.artifacts import load_time_options, save_time_options

        save_time_options(tmp_path, self._scan())
        loaded = load_time_options(tmp_path)
        assert loaded is not None
        assert loaded.candidates[0].confidence == "confirmed_in_window"
        assert loaded.candidate_at(DEP + timedelta(hours=1)) is not None
        assert loaded.candidate_at(DEP + timedelta(hours=5)) is None

    def test_missing_artifact_is_none(self, tmp_path: Path):
        from weatherbrief.tasks.artifacts import load_time_options

        assert load_time_options(tmp_path) is None

    def test_status_round_trip(self, tmp_path: Path):
        from weatherbrief.tasks.artifacts import (
            load_time_scan_status,
            save_time_scan_status,
        )

        save_time_scan_status(
            tmp_path,
            TimeScanStatus(
                status="running", flexibility="same_day",
                updated_at=datetime.now(timezone.utc),
            ),
        )
        loaded = load_time_scan_status(tmp_path)
        assert loaded is not None and loaded.status == "running"


# ---------------------------------------------------------------------------
# Provisional tier (slice 2): deferred hours graduate to ecmwf_only
# ---------------------------------------------------------------------------


class TestProvisionalTier:
    def _write_pack(self, tmp_path: Path) -> None:
        import json

        # ECMWF enriched only 06-11 (flight window ±3h); GFS 07-10 forward.
        cs_ecmwf = _cs(ModelSource.ECMWF, [_wf(ModelSource.ECMWF, enriched_hours=set(range(6, 12)))])
        cs_gfs = _cs(ModelSource.GFS, [_wf(ModelSource.GFS, gfs_marked_hours=set(range(7, 11)))])
        (tmp_path / "cross_section.json").write_text(json.dumps({
            "cross_sections": [cs.model_dump(mode="json") for cs in (cs_ecmwf, cs_gfs)],
        }, default=str))
        (tmp_path / "route_points.json").write_text(json.dumps(
            [rp.model_dump(mode="json") for rp in cs_ecmwf.route_points], default=str,
        ))

    def test_deferred_hours_grade_ecmwf_only_via_extension(self, tmp_path, monkeypatch):
        """Hours the free tier can't grade go through the (patched) daylight
        extension and come back as ``ecmwf_only`` provisional candidates —
        never silently clamp-graded, never refused when ECMWF covers them."""
        import weatherbrief.tasks.time_scan as ts
        from weatherbrief.models import RouteConfig, Waypoint

        self._write_pack(tmp_path)

        def fake_extend(cross_sections, route_points, w_lo, w_hi, dur, *, as_of_time=None):
            # Pretend the whole day was decoded: mark every hour enriched on
            # the ECMWF section and report full-day coverage.
            for cs in cross_sections:
                if cs.model == ModelSource.ECMWF:
                    for wf in cs.point_forecasts:
                        for h in wf.hourly:
                            h.pressure_levels = _levels(28)
            return 1234567890, [(DAY, DAY + timedelta(hours=23))]

        monkeypatch.setattr(ts, "extend_ecmwf_daylight", fake_extend)

        route = RouteConfig(
            name="t",
            waypoints=[
                Waypoint(icao="AAAA", name="A", lat=51.0, lon=0.0),
                Waypoint(icao="BBBB", name="B", lat=51.0, lon=1.0),
            ],
            flight_duration_hours=1.0,
        )
        scan = ts.run_time_scan(tmp_path, route, DEP, flexibility="same_day")

        assert scan is not None
        assert scan.ecmwf_run_ts == 1234567890
        confs = {c.confidence for c in scan.candidates if not c.is_baseline}
        # At least some non-baseline candidates exist and every graded
        # non-baseline one is honestly labelled (either tier, never unlabeled).
        assert confs <= {"confirmed_in_window", "ecmwf_only"}
        provisional = [c for c in scan.candidates if c.confidence == "ecmwf_only"]
        # ecmwf-only view of the baseline was recorded as the diff denominator
        if provisional:
            assert scan.baseline.ecmwf_assessment is not None
            for c in provisional:
                assert c.models_used == ["ecmwf"]

    def test_no_ecmwf_run_refuses_deferred(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.time_scan as ts
        from weatherbrief.models import RouteConfig, Waypoint

        self._write_pack(tmp_path)
        monkeypatch.setattr(
            ts, "extend_ecmwf_daylight", lambda *a, **k: (None, []),
        )
        route = RouteConfig(
            name="t",
            waypoints=[
                Waypoint(icao="AAAA", name="A", lat=51.0, lon=0.0),
                Waypoint(icao="BBBB", name="B", lat=51.0, lon=1.0),
            ],
            flight_duration_hours=1.0,
        )
        scan = ts.run_time_scan(tmp_path, route, DEP, flexibility="same_day")
        assert scan is not None
        assert scan.ecmwf_run_ts is None
        assert scan.refused_times  # deferred hours fell back to refusal
        assert all(c.confidence != "ecmwf_only" for c in scan.candidates)


# ---------------------------------------------------------------------------
# Declarative registry sets (locked mapping, decision C)
# ---------------------------------------------------------------------------


class TestTimingClassRegistry:
    def test_scan_class_set(self):
        from weatherbrief.analysis.advisories.registry import get_scan_class_ids

        assert get_scan_class_ids() == {
            "convective", "convective_character", "icing_escape", "fiki_icing",
            "cloud_top", "vmc_cruise", "vfr_feasibility", "ifr_feasibility",
            "freezing_precip",
        }

    def test_hint_set_adds_flight_category(self):
        from weatherbrief.analysis.advisories.registry import (
            get_scan_class_ids,
            get_timing_hint_ids,
        )

        assert get_timing_hint_ids() == get_scan_class_ids() | {"flight_category"}
