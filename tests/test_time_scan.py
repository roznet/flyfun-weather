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


class TestPerModelReasons:
    """per_model_reasons_from_manifest — the confirm's dot-table breakdown."""

    def test_split_by_model_red_amber_only(self):
        from weatherbrief.models import ModelAdvisoryResult
        from weatherbrief.tasks.advise import per_model_reasons_from_manifest

        manifest = RouteAdvisoriesManifest(advisories=[
            RouteAdvisoryResult(
                advisory_id="turbulence",
                aggregate_status=AdvisoryStatus.RED,
                per_model=[
                    ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.AMBER),
                    ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.RED),
                    ModelAdvisoryResult(model="icon", status=AdvisoryStatus.GREEN),
                ],
            ),
            RouteAdvisoryResult(
                advisory_id="icing",
                aggregate_status=AdvisoryStatus.AMBER,
                per_model=[
                    ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.AMBER),
                ],
            ),
        ])
        out = per_model_reasons_from_manifest(manifest)
        assert out == {
            "ecmwf": "turbulence=AMBER",
            "gfs": "turbulence=RED, icing=AMBER",
        }
        # All-green model is absent — the client treats absence as clear.
        assert "icon" not in out

    def test_empty_manifest(self):
        from weatherbrief.tasks.advise import per_model_reasons_from_manifest

        assert per_model_reasons_from_manifest(RouteAdvisoriesManifest()) == {}

    def test_per_model_status_includes_green(self):
        """#434: the full per-(advisory, model) status map records EVERY graded
        status (GREEN too), so the UI can reconstruct dot rows without
        re-grading — unlike the RED/AMBER-only reason string."""
        from weatherbrief.models import ModelAdvisoryResult
        from weatherbrief.tasks.advise import per_model_status_from_manifest

        manifest = RouteAdvisoriesManifest(advisories=[
            RouteAdvisoryResult(
                advisory_id="convective",
                aggregate_status=AdvisoryStatus.RED,
                per_model=[
                    ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.RED),
                    ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.GREEN),
                ],
            ),
        ])
        assert per_model_status_from_manifest(manifest) == {
            "convective": {"ecmwf": "RED", "gfs": "GREEN"},
        }

    def test_per_model_status_empty_without_per_model(self):
        # An aggregate-only advisory (no per-model breakdown) contributes nothing.
        from weatherbrief.tasks.advise import per_model_status_from_manifest

        manifest = RouteAdvisoriesManifest(advisories=[
            RouteAdvisoryResult(advisory_id="convective", aggregate_status=AdvisoryStatus.RED),
        ])
        assert per_model_status_from_manifest(manifest) == {}

    def test_confirmation_round_trips_field(self):
        from weatherbrief.models import TimeConfirmation

        conf = TimeConfirmation(
            models_checked=["ecmwf", "gfs"],
            assessment="RED",
            per_model_reasons={"gfs": "turbulence=RED"},
            better_than_baseline=False,
            confirmed_at=DEP,
        )
        again = TimeConfirmation.model_validate(conf.model_dump(mode="json"))
        assert again.per_model_reasons == {"gfs": "turbulence=RED"}
        # Old artifacts (pre-field) still validate.
        legacy = conf.model_dump(mode="json")
        legacy.pop("per_model_reasons")
        assert TimeConfirmation.model_validate(legacy).per_model_reasons == {}


class TestDisposition:
    """#434 taxonomy: improving / neutral / worse vs the like-coverage baseline."""

    def test_improving_when_margin_clears_and_nothing_worsens(self):
        from weatherbrief.tasks.time_scan import _disposition

        assert _disposition(2, []) == "improving"

    def test_neutral_when_no_gain_no_regression(self):
        from weatherbrief.tasks.time_scan import _disposition

        assert _disposition(0, []) == "neutral"

    def test_worse_when_something_worsens_even_with_positive_margin(self):
        # A full-set regression trumps a scan-class gain — never call a window
        # that introduced a crosswind "improving".
        from weatherbrief.tasks.time_scan import _disposition

        assert _disposition(2, ["airport_wind"]) == "worse"

    def test_worse_on_negative_margin(self):
        from weatherbrief.tasks.time_scan import _disposition

        assert _disposition(-1, []) == "worse"


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

    def test_shift_zero_reproduces_independent_grade(self, tmp_path, monkeypatch):
        """Plan Validation #1: the scan's baseline row must equal a grade of
        the planned time computed independently through run_alt_from_pack —
        not just a self-consistent zero diff."""
        import weatherbrief.tasks.time_scan as ts
        from weatherbrief.models import RouteConfig, Waypoint
        from weatherbrief.tasks.advise import (
            derive_assessment_from_advisories,
            run_alt_from_pack,
        )

        self._write_pack(tmp_path)
        monkeypatch.setattr(ts, "extend_ecmwf_daylight", lambda *a, **k: (None, []))
        route = RouteConfig(
            name="t",
            waypoints=[
                Waypoint(icao="AAAA", name="A", lat=51.0, lon=0.0),
                Waypoint(icao="BBBB", name="B", lat=51.0, lon=1.0),
            ],
            flight_duration_hours=1.0,
        )
        scan = ts.run_time_scan(tmp_path, route, DEP, flexibility="same_day")
        base_row = next(c for c in scan.candidates if c.is_baseline)

        independent = run_alt_from_pack(
            tmp_path, DEP, route, persist=False, detect_fronts=False,
        )
        ind_assess, _ = derive_assessment_from_advisories(independent.manifest)

        assert base_row.assessment == ind_assess
        assert base_row.margin == 0
        assert not base_row.improves and not base_row.worsens

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

    def test_next_day_produces_ecmwf_only_candidates(self, tmp_path, monkeypatch):
        """Slice 4 wiring: a next_day scan reaches the adjacent day — the
        daylight ECMWF extension decodes next-day hours and the deferred
        candidates come back ``ecmwf_only`` (not silently all-refused). DEP is
        in the past, so the forward-planning past-clamp leaves the grid intact
        (replay gate) and this stays deterministic."""
        import weatherbrief.tasks.time_scan as ts
        from weatherbrief.models import RouteConfig, Waypoint

        self._write_pack(tmp_path)

        def fake_extend(cross_sections, route_points, w_lo, w_hi, dur, *, as_of_time=None):
            # Decode the *next* day: append enriched ECMWF hours across the
            # extension window and report coverage that spans it.
            hi = w_hi + timedelta(hours=dur + 3)
            for cs in cross_sections:
                if cs.model == ModelSource.ECMWF:
                    for wf in cs.point_forecasts:
                        have = {h.time for h in wf.hourly}
                        t = w_lo.replace(minute=0, second=0, microsecond=0)
                        while t <= hi:
                            if t not in have:
                                wf.hourly.append(
                                    HourlyForecast(time=t, pressure_levels=_levels(28)),
                                )
                            t += timedelta(hours=1)
                        wf.hourly.sort(key=lambda h: h.time)
            return 1234567890, [(w_lo - timedelta(hours=3), hi)]

        monkeypatch.setattr(ts, "extend_ecmwf_daylight", fake_extend)
        # The real ±day path fetches the adjacent-day OM skeleton first (so the
        # ECMWF extension has rows to enrich). Spy on it (no network) — the fake
        # ECMWF extension above already supplies next-day rows for grading.
        om_calls = []
        monkeypatch.setattr(
            ts, "extend_openmeteo_adjacent_day",
            lambda cs, rps, s, e, **k: om_calls.append((s, e)) or 0,
        )

        route = RouteConfig(
            name="t",
            waypoints=[
                Waypoint(icao="AAAA", name="A", lat=51.0, lon=0.0),
                Waypoint(icao="BBBB", name="B", lat=51.0, lon=1.0),
            ],
            flight_duration_hours=1.0,
        )
        scan = ts.run_time_scan(tmp_path, route, DEP, flexibility="next_day")

        assert scan is not None
        assert scan.window is not None and scan.window.flexibility == "next_day"
        assert not scan.window.past_clipped  # replay gate: past DEP untouched
        # The wiring reached the adjacent day: the extension decoded it (run_ts
        # set) and the next-day hours were graded against it, NOT refused. That
        # they don't surface as "better" here is correct — the synthetic data
        # grades identically everywhere, so there's honestly no better window
        # (the ranking/suppression logic itself is covered mode-agnostically).
        assert scan.ecmwf_run_ts == 1234567890
        assert not scan.refused_times
        # Any surfaced non-baseline candidate is honestly labelled + on the next day.
        for c in scan.candidates:
            if not c.is_baseline:
                assert c.confidence == "ecmwf_only"
                assert c.departure_time.date() > DEP.date()


# ---------------------------------------------------------------------------
# Persist all dispositions (#434): worse/neutral rows kept + tagged, not dropped
# ---------------------------------------------------------------------------


class TestPersistAllDispositions:
    """The scan now persists every graded candidate with a disposition — the
    improving-only cut moved to the display layer. Closes the honesty gap: a
    worse alternate day is surfaced (behind the client's "show all"), never
    silently discarded."""

    def _write_pack(self, tmp_path: Path) -> None:
        import json

        cs_ecmwf = _cs(ModelSource.ECMWF, [_wf(ModelSource.ECMWF, enriched_hours=set(range(6, 12)))])
        cs_gfs = _cs(ModelSource.GFS, [_wf(ModelSource.GFS, gfs_marked_hours=set(range(7, 11)))])
        (tmp_path / "cross_section.json").write_text(json.dumps({
            "cross_sections": [cs.model_dump(mode="json") for cs in (cs_ecmwf, cs_gfs)],
        }, default=str))
        (tmp_path / "route_points.json").write_text(json.dumps(
            [rp.model_dump(mode="json") for rp in cs_ecmwf.route_points], default=str,
        ))

    def _route(self):
        from weatherbrief.models import RouteConfig, Waypoint

        return RouteConfig(
            name="t",
            waypoints=[
                Waypoint(icao="AAAA", name="A", lat=51.0, lon=0.0),
                Waypoint(icao="BBBB", name="B", lat=51.0, lon=1.0),
            ],
            flight_duration_hours=1.0,
        )

    def _full_day_extension(self):
        def fake_extend(cross_sections, route_points, w_lo, w_hi, dur, *, as_of_time=None):
            for cs in cross_sections:
                if cs.model == ModelSource.ECMWF:
                    for wf in cs.point_forecasts:
                        for h in wf.hourly:
                            h.pressure_levels = _levels(28)
            return 1234567890, [(DAY, DAY + timedelta(hours=23))]

        return fake_extend

    def test_neutral_candidates_persisted_and_tagged(self, tmp_path, monkeypatch):
        """Synthetic data grades identically at every hour → margin 0 → neutral.
        Before #434 these were dropped entirely; now they're kept and tagged so
        the UI can show "same conditions" times, not just better ones."""
        import weatherbrief.tasks.time_scan as ts

        self._write_pack(tmp_path)
        monkeypatch.setattr(ts, "extend_ecmwf_daylight", self._full_day_extension())

        scan = ts.run_time_scan(tmp_path, self._route(), DEP, flexibility="same_day")
        assert scan is not None
        swept = [c for c in scan.candidates if not c.is_baseline]
        assert swept, "graded candidates must be persisted, not discarded"
        assert all(c.disposition in {"improving", "neutral", "worse"} for c in swept)
        assert any(c.disposition == "neutral" for c in swept)
        # The baseline carries its own full per-model status map.
        base = next(c for c in scan.candidates if c.is_baseline)
        assert base.advisory_status, "baseline advisory_status map recorded"

    def test_worse_candidates_persisted_and_tagged(self, tmp_path, monkeypatch):
        """Force every diff to a regression: the swept rows must all be tagged
        ``worse`` and still be persisted (the exact rows the old code threw
        away). advisory_status is coverage-scoped to what was graded."""
        import weatherbrief.tasks.time_scan as ts

        self._write_pack(tmp_path)
        monkeypatch.setattr(ts, "extend_ecmwf_daylight", self._full_day_extension())
        # A regression on a non-scan advisory + negative scan margin → worse.
        monkeypatch.setattr(
            ts, "_diff_manifests",
            lambda *a, **k: (["convective"], ["airport_wind"], -1),
        )

        scan = ts.run_time_scan(tmp_path, self._route(), DEP, flexibility="same_day")
        assert scan is not None
        swept = [c for c in scan.candidates if not c.is_baseline]
        assert swept, "worse candidates must be persisted, not silently discarded"
        assert all(c.disposition == "worse" for c in swept)
        # Each ecmwf-only worse row carries a coverage-scoped per-model map:
        # only ECMWF (and model-agnostic "all" advisories) were graded — the
        # other weather models were never fetched for the sweep.
        for c in swept:
            assert isinstance(c.advisory_status, dict)
            for model_map in c.advisory_status.values():
                assert "gfs" not in model_map and "icon" not in model_map

    def test_persistence_stays_bounded(self, tmp_path, monkeypatch):
        """Size guardrail: keeping all dispositions must stay bounded by the
        grid cap, and each candidate's per-model status map stays compact
        (~KB, never the ~41 KB full detail which stays gated)."""
        import json

        import weatherbrief.tasks.time_scan as ts
        from weatherbrief.tasks.time_scan import _MAX_GRID

        self._write_pack(tmp_path)
        monkeypatch.setattr(ts, "extend_ecmwf_daylight", self._full_day_extension())

        scan = ts.run_time_scan(tmp_path, self._route(), DEP, flexibility="same_day")
        assert scan is not None
        swept = [c for c in scan.candidates if not c.is_baseline]
        assert len(swept) <= _MAX_GRID
        for c in scan.candidates:
            size = len(json.dumps(c.advisory_status))
            assert size < 5000, f"per-candidate advisory_status too large: {size} B"

    def test_ranking_orders_improving_then_neutral_then_worse(self, tmp_path, monkeypatch):
        """The persisted order lets the client slice same-or-better off the
        front: improving first, neutral next, worse last."""
        import weatherbrief.tasks.time_scan as ts

        self._write_pack(tmp_path)
        monkeypatch.setattr(ts, "extend_ecmwf_daylight", self._full_day_extension())

        # Deterministically stamp a disposition per candidate hour so the
        # ordering assertion doesn't depend on the synthetic grades: alternate
        # improving / worse / neutral by hour.
        real_diff = ts._diff_manifests
        seq = iter([(["a"], [], 2), ([], ["b"], -1), ([], [], 0)] * 20)

        def fake_diff(base, cand, scan_ids):
            try:
                return next(seq)
            except StopIteration:
                return real_diff(base, cand, scan_ids)

        monkeypatch.setattr(ts, "_diff_manifests", fake_diff)
        scan = ts.run_time_scan(tmp_path, self._route(), DEP, flexibility="same_day")
        assert scan is not None
        swept = [c for c in scan.candidates if not c.is_baseline]
        ranks = {"improving": 0, "neutral": 1, "worse": 2}
        order = [ranks[c.disposition] for c in swept]
        assert order == sorted(order), "candidates must be ordered improving→neutral→worse"


# ---------------------------------------------------------------------------
# Confirm scheduling: one-at-a-time guard + stale-flag recovery (slice 3)
# ---------------------------------------------------------------------------


class TestConfirmScheduling:
    def _seed_scan(self, tmp_path: Path) -> None:
        from weatherbrief.tasks.artifacts import save_time_options

        scan = TimeWindowScan(
            flexibility="same_day",
            baseline=TimeScanBaseline(departure_time=DEP, assessment="RED"),
            candidates=[
                TimeCandidate(
                    departure_time=DEP + timedelta(hours=h),
                    departure_shift_hours=float(h),
                    assessment="AMBER",
                    confidence="ecmwf_only",
                )
                for h in (3, 4)
            ],
            generated_at=DEP,
        )
        save_time_options(tmp_path, scan)

    def test_one_confirm_at_a_time_per_pack(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.time_scan_runner as runner

        self._seed_scan(tmp_path)
        # Queue but never run — the worker must not consume the inflight key.
        monkeypatch.setattr(runner._executor, "submit", lambda *a, **k: None)

        first = runner.schedule_time_confirm("f1", tmp_path, DEP + timedelta(hours=3))
        assert first == "queued"
        # Same candidate re-tapped: already on it.
        again = runner.schedule_time_confirm("f1", tmp_path, DEP + timedelta(hours=3))
        assert again == "queued"
        # A DIFFERENT candidate while one runs: rejected (server-side mirror
        # of the disabled buttons — each confirm costs a briefing-equivalent
        # of GFS+ICON fetch).
        other = runner.schedule_time_confirm("f1", tmp_path, DEP + timedelta(hours=4))
        assert other == "busy"

        # Cleanup the module-global inflight set for other tests.
        with runner._inflight_lock:
            runner._inflight.clear()

    def test_unknown_candidate_is_invalid(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.time_scan_runner as runner

        self._seed_scan(tmp_path)
        monkeypatch.setattr(runner._executor, "submit", lambda *a, **k: None)
        assert runner.schedule_time_confirm("f1", tmp_path, DEP + timedelta(hours=9)) == "invalid"

    def test_reconcile_clears_orphaned_pending(self, tmp_path):
        """A restart mid-confirm leaves confirm_pending with no live worker —
        the polling GET reconciles it so the UI never shows an eternal
        'checking all models…'."""
        import weatherbrief.tasks.time_scan_runner as runner
        from weatherbrief.tasks.artifacts import load_time_options, save_time_options

        self._seed_scan(tmp_path)
        scan = load_time_options(tmp_path)
        scan.candidates[0].confirm_pending = True
        save_time_options(tmp_path, scan)

        assert runner.reconcile_stale_confirms(tmp_path) is True
        reloaded = load_time_options(tmp_path)
        assert not any(c.confirm_pending for c in reloaded.candidates)
        # Idempotent: nothing left to clear.
        assert runner.reconcile_stale_confirms(tmp_path) is False


# ---------------------------------------------------------------------------
# Runner: decision-H reuse, stale-status reconcile, confirm-merge (round 2)
# ---------------------------------------------------------------------------


def _flight_stub(**overrides):
    from types import SimpleNamespace

    base = dict(
        flexibility="same_day",
        departure_time=DEP,
        alt_departure_time=None,
        user_id="u1",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _scan_artifact(
    tmp_path: Path,
    *,
    run_ts: int | None = 111,
    candidates: list[TimeCandidate] | None = None,
) -> TimeWindowScan:
    from weatherbrief.tasks.artifacts import save_time_options

    scan = TimeWindowScan(
        flexibility="same_day",
        baseline=TimeScanBaseline(departure_time=DEP, assessment="RED"),
        candidates=candidates if candidates is not None else [],
        ecmwf_run_ts=run_ts,
        generated_at=DEP,
    )
    save_time_options(tmp_path, scan)
    return scan


class TestReusableScan:
    """Decision-H: a scan for the same run/departure/mode skips the re-decode."""

    def _patch_run(self, monkeypatch, ts):
        import weatherbrief.tasks.time_scan as ts_mod

        monkeypatch.setattr(ts_mod, "current_ecmwf_run_ts", lambda *a, **k: ts)
        monkeypatch.setattr(ts_mod, "_pack_fetched_at", lambda p: DEP)

    def test_reuses_same_run(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.time_scan_runner as runner

        _scan_artifact(tmp_path, run_ts=111)
        self._patch_run(monkeypatch, 111)
        assert runner._reusable_scan(tmp_path, _flight_stub()) is not None

    def test_new_run_invalidates(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.time_scan_runner as runner

        _scan_artifact(tmp_path, run_ts=111)
        self._patch_run(monkeypatch, 222)
        assert runner._reusable_scan(tmp_path, _flight_stub()) is None

    def test_changed_mode_or_departure_invalidates(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.time_scan_runner as runner

        _scan_artifact(tmp_path, run_ts=111)
        self._patch_run(monkeypatch, 111)
        assert runner._reusable_scan(tmp_path, _flight_stub(flexibility="next_day")) is None
        assert runner._reusable_scan(
            tmp_path, _flight_stub(departure_time=DEP + timedelta(hours=2)),
        ) is None

    def test_new_alternate_invalidates(self, tmp_path, monkeypatch):
        # "Set as alternate" must force a re-grade of the pinned row.
        import weatherbrief.tasks.time_scan_runner as runner

        _scan_artifact(tmp_path, run_ts=111)
        self._patch_run(monkeypatch, 111)
        assert runner._reusable_scan(
            tmp_path, _flight_stub(alt_departure_time=DEP + timedelta(hours=3)),
        ) is None

    def test_free_tier_only_scan_not_reused(self, tmp_path, monkeypatch):
        # No run_ts = no decode was spent; a fresh grade wins.
        import weatherbrief.tasks.time_scan_runner as runner

        _scan_artifact(tmp_path, run_ts=None)
        self._patch_run(monkeypatch, 111)
        assert runner._reusable_scan(tmp_path, _flight_stub()) is None


class TestStaleScanReconcile:
    def test_orphaned_running_status_flips_to_failed(self, tmp_path):
        import weatherbrief.tasks.time_scan_runner as runner
        from weatherbrief.tasks.artifacts import load_time_scan_status

        runner._write_status(tmp_path, "running", "same_day")
        assert runner.reconcile_stale_scan(tmp_path) is True
        status = load_time_scan_status(tmp_path)
        assert status.status == "failed" and status.reason == "interrupted"

    def test_live_worker_is_left_alone(self, tmp_path):
        import weatherbrief.tasks.time_scan_runner as runner

        runner._write_status(tmp_path, "running", "same_day")
        with runner._inflight_lock:
            runner._inflight.add(str(tmp_path))
        try:
            assert runner.reconcile_stale_scan(tmp_path) is False
        finally:
            with runner._inflight_lock:
                runner._inflight.discard(str(tmp_path))

    def test_terminal_status_untouched(self, tmp_path):
        import weatherbrief.tasks.time_scan_runner as runner
        from weatherbrief.tasks.artifacts import load_time_scan_status

        runner._write_status(tmp_path, "done", "same_day")
        assert runner.reconcile_stale_scan(tmp_path) is False
        assert load_time_scan_status(tmp_path).status == "done"


class TestMergeConfirmed:
    def _cand(self, hours: float, confirmed: bool = False) -> TimeCandidate:
        from weatherbrief.models import TimeConfirmation

        return TimeCandidate(
            departure_time=DEP + timedelta(hours=hours),
            departure_shift_hours=hours,
            assessment="AMBER",
            confidence="ecmwf_only",
            # A confirmed prev carries the widened multi-model map + a
            # confirm-derived disposition (here: downgraded to worse).
            disposition="worse" if confirmed else "improving",
            advisory_status=(
                {"convective": {"ecmwf": "RED", "gfs": "AMBER", "icon": "RED"}}
                if confirmed else {"convective": {"ecmwf": "AMBER"}}
            ),
            confirmed=TimeConfirmation(
                models_checked=["ecmwf", "gfs", "icon"],
                assessment="RED",
                better_than_baseline=False,
                worsens=["convective"],
                confirmed_at=DEP,
            ) if confirmed else None,
        )

    def test_confirm_survives_same_run_rescan(self, tmp_path):
        from weatherbrief.tasks.time_scan_runner import merge_confirmed

        old = _scan_artifact(tmp_path, run_ts=111, candidates=[self._cand(3, confirmed=True)])
        new = TimeWindowScan(
            flexibility="same_day",
            baseline=TimeScanBaseline(departure_time=DEP, assessment="RED"),
            candidates=[self._cand(3)],  # fresh ECMWF-only regrade
            ecmwf_run_ts=111,
            generated_at=DEP,
        )
        merge_confirmed(old, new)
        merged = new.candidates[0]
        assert merged.confirmed is not None
        assert merged.confidence == "confirmed"
        # #435 finding 2: the paid confirm's widened map + confirm-derived
        # disposition survive the rescan, not just the verdict object.
        assert merged.advisory_status == {"convective": {"ecmwf": "RED", "gfs": "AMBER", "icon": "RED"}}
        assert merged.disposition == "worse"

    def test_new_run_drops_stale_confirms(self, tmp_path):
        from weatherbrief.tasks.time_scan_runner import merge_confirmed

        old = _scan_artifact(tmp_path, run_ts=111, candidates=[self._cand(3, confirmed=True)])
        new = TimeWindowScan(
            flexibility="same_day",
            baseline=TimeScanBaseline(departure_time=DEP, assessment="RED"),
            candidates=[self._cand(3)],
            ecmwf_run_ts=222,
            generated_at=DEP,
        )
        merge_confirmed(old, new)
        assert new.candidates[0].confirmed is None


class TestApplyConfirmation:
    """#435 finding 1: a confirm must move a candidate between display buckets
    by re-deriving disposition, not merely swap its verdict text — the web
    show/hide split and "N look smoother" headline key off disposition."""

    def _pending(self, disposition: str) -> TimeCandidate:
        return TimeCandidate(
            departure_time=DEP + timedelta(hours=3),
            departure_shift_hours=3.0,
            assessment="AMBER",
            confidence="ecmwf_only",
            disposition=disposition,
            advisory_status={"convective": {"ecmwf": "GREEN"}},
            confirm_pending=True,
        )

    def _result(self, disposition: str, *, better: bool, worsens):
        from weatherbrief.models import TimeConfirmation

        conf = TimeConfirmation(
            models_checked=["ecmwf", "gfs", "icon"],
            assessment="AMBER",
            better_than_baseline=better,
            worsens=worsens,
            confirmed_at=DEP,
        )
        status = {"convective": {"ecmwf": "AMBER", "gfs": "GREEN", "icon": "AMBER"}}
        return conf, status, disposition

    def test_confirm_downgrades_improving_to_worse(self):
        from weatherbrief.tasks.time_scan_runner import _apply_confirmation

        cand = self._pending("improving")
        _apply_confirmation(cand, self._result("worse", better=False, worsens=["airport_wind"]))
        assert cand.disposition == "worse"  # moved out of the default view
        assert cand.confidence == "confirmed"
        assert cand.confirm_pending is False
        # The map widened to the full graded set on confirm.
        assert cand.advisory_status["convective"] == {"ecmwf": "AMBER", "gfs": "GREEN", "icon": "AMBER"}

    def test_confirm_upgrades_worse_to_improving(self):
        from weatherbrief.tasks.time_scan_runner import _apply_confirmation

        cand = self._pending("worse")
        _apply_confirmation(cand, self._result("improving", better=True, worsens=[]))
        assert cand.disposition == "improving"  # revealed into the default view

    def test_failed_confirm_only_clears_pending(self):
        from weatherbrief.tasks.time_scan_runner import _apply_confirmation

        cand = self._pending("improving")
        _apply_confirmation(cand, None)
        assert cand.confirm_pending is False
        assert cand.disposition == "improving"  # unchanged
        assert cand.confirmed is None


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


# ---------------------------------------------------------------------------
# Slice 4: ±day past-clamp + Open-Meteo adjacent-day extension
# ---------------------------------------------------------------------------


class TestClampPastGrid:
    """Forward-planning drops elapsed hours; replay of a past pack is untouched."""

    def test_future_flight_drops_elapsed_hours(self):
        from weatherbrief.tasks.time_scan import _clamp_past_grid

        now = datetime(2026, 6, 27, 10, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc)  # still future
        grid = [DAY + timedelta(hours=h) for h in range(6, 20)]
        kept, clipped = _clamp_past_grid(grid, dep, now)
        assert clipped is True
        assert kept == [t for t in grid if t > now]
        assert all(t > now for t in kept)

    def test_all_past_yields_empty_grid(self):
        from weatherbrief.tasks.time_scan import _clamp_past_grid

        now = datetime(2026, 6, 27, 23, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc)  # future flight
        grid = [DAY + timedelta(hours=h) for h in range(6, 20)]  # all elapsed
        kept, clipped = _clamp_past_grid(grid, dep, now)
        assert kept == []
        assert clipped is True

    def test_replay_of_past_pack_not_clamped(self):
        """An already-flown pack (dep in the past) grades its whole window —
        eval/replay must be untouched by the forward-planning clamp."""
        from weatherbrief.tasks.time_scan import _clamp_past_grid

        now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
        grid = [DAY + timedelta(hours=h) for h in range(6, 20)]
        kept, clipped = _clamp_past_grid(grid, DEP, now)  # DEP = 2026-06-27 (past)
        assert kept == grid
        assert clipped is False


class _FakeOMClient:
    """Stand-in OpenMeteoClient: one 24h WaypointForecast per point on the
    requested start_date, so the adjacent-day splice has data to merge."""

    def __init__(self, *a, **k):
        pass

    def fetch_multi_point(self, points, model, *, start_date=None, end_date=None, **k):
        base = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        out = []
        for _ in points:
            hourly = [
                HourlyForecast(time=base + timedelta(hours=h), pressure_levels=_levels(8))
                for h in range(24)
            ]
            out.append(WaypointForecast(
                waypoint=Waypoint(icao="XXXX", name="X", lat=51.0, lon=0.0),
                model=model, fetched_at=DEP, hourly=hourly,
            ))
        return out


class TestExtendOpenMeteoAdjacentDay:
    def test_splices_next_day_hours(self, monkeypatch):
        import weatherbrief.fetch.open_meteo as om
        from weatherbrief.tasks.time_scan import (
            _pack_hourly_bounds,
            extend_openmeteo_adjacent_day,
        )

        cs = _cs(
            ModelSource.METEOFRANCE,
            [_wf(ModelSource.METEOFRANCE), _wf(ModelSource.METEOFRANCE)],
        )
        before = _pack_hourly_bounds([cs])
        monkeypatch.setattr(om, "OpenMeteoClient", _FakeOMClient)

        next_day = (DAY + timedelta(days=1)).date()
        extended = extend_openmeteo_adjacent_day([cs], cs.route_points, next_day, next_day)

        assert extended == 1
        after = _pack_hourly_bounds([cs])
        assert after[1] > before[1]
        assert after[1] >= DAY + timedelta(days=1, hours=23)
        # OM-only model's honest coverage now spans into the next day.
        per_point, _ = compute_model_coverage([cs], DEP, 1.0)
        for span in per_point["meteofrance"]:
            assert span is not None and span[1] >= DAY + timedelta(days=1)

    def test_dedups_existing_day(self, monkeypatch):
        """Re-fetching a day the pack already holds adds nothing (dedup by
        time) — the hourly count is unchanged, no double rows."""
        import weatherbrief.fetch.open_meteo as om
        from weatherbrief.tasks.time_scan import extend_openmeteo_adjacent_day

        cs = _cs(ModelSource.METEOFRANCE, [_wf(ModelSource.METEOFRANCE)])
        n_before = len(cs.point_forecasts[0].hourly)
        monkeypatch.setattr(om, "OpenMeteoClient", _FakeOMClient)

        extend_openmeteo_adjacent_day([cs], cs.route_points, DAY.date(), DAY.date())
        assert len(cs.point_forecasts[0].hourly) == n_before
