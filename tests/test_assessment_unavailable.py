"""The flight-level UNAVAILABLE assessment (issue #392).

The advisory stack already refuses to grade absent data as clear
(``AdvisoryStatus.worst([])`` → UNAVAILABLE, commit dec094f0). The
*flight-level* traffic light did not: it filtered the UNAVAILABLE advisories
out, found an empty list, and returned GREEN — so a briefing that assessed
nothing showed a pilot the same colour as one we assessed and found clear.

Four decisions are pinned here:

1. Nothing gradeable → ``"UNAVAILABLE"``, never GREEN. NULL keeps its existing
   and different meaning: no pack / not briefed yet.
2. The LLM digest cannot override it. The digest is the pack's assessment in the
   normal case, and its schema only permits GREEN/AMBER/RED — so an empty
   manifest gets a confident traffic light out of it. The deterministic
   derivation wins, and the pipeline skips the digest entirely.
3. The trigger is binary: *zero* graded advisories. Partial coverage still
   grades from what graded.
4. An UNAVAILABLE briefing does not notify. It is not weather news and the pilot
   cannot act on it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flyfun_common.db import DEV_USER_ID
from weatherbrief.models import (
    AdvisoryStatus,
    BriefingPackMeta,
    Flight,
    ForecastSnapshot,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    RouteConfig,
    Waypoint,
)
from weatherbrief.pipeline import BriefingResult
from weatherbrief.storage.flights import list_packs, save_flight
from weatherbrief.tasks.advise import (
    ASSESSMENT_UNAVAILABLE,
    advisories_ungradeable,
    derive_assessment_from_advisories,
)


# --- Helpers ---------------------------------------------------------------


def _manifest(*statuses: AdvisoryStatus) -> RouteAdvisoriesManifest:
    return RouteAdvisoriesManifest(
        advisories=[
            RouteAdvisoryResult(
                advisory_id=f"adv_{i}", aggregate_status=s, per_model=[],
            )
            for i, s in enumerate(statuses)
        ],
    )


def _make_route() -> RouteConfig:
    return RouteConfig(
        name="test",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8, lon=-1.3),
            Waypoint(icao="LSGS", name="Sion", lat=46.2, lon=7.3),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=4.0,
    )


def _make_flight(flight_id: str = "unavail-flight") -> Flight:
    return Flight(
        id=flight_id,
        user_id=DEV_USER_ID,
        route_name="test",
        waypoints=["EGTK", "LSGS"],
        departure_time=datetime.now(timezone.utc) + timedelta(days=2),
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=4.0,
        created_at=datetime.now(timezone.utc),
    )


def _result_with(pack_path: Path, manifest: RouteAdvisoriesManifest) -> BriefingResult:
    snapshot = ForecastSnapshot(
        route=_make_route(),
        target_date=(datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat(),
        fetch_date=datetime.now(timezone.utc).date().isoformat(),
        days_out=2,
    )
    result = BriefingResult(
        snapshot=snapshot, snapshot_path=pack_path / "briefing.json",
    )
    result.route_advisories_manifest = manifest
    result.models_fetched = ["ecmwf", "gfs"]
    result.grib_init_times = {}
    return result


# --- 1. The derivation -----------------------------------------------------


class TestDeriveAssessment:
    def test_all_unavailable_is_not_green(self):
        """The defect this issue is named for."""
        m = _manifest(AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE)
        assessment, reason = derive_assessment_from_advisories(m)
        assert assessment == "UNAVAILABLE"
        assert assessment != "GREEN"
        assert reason  # says *why*, rather than the old "No advisory data available"

    def test_empty_manifest_is_not_green(self):
        m = _manifest()
        assessment, _ = derive_assessment_from_advisories(m)
        assert assessment == "UNAVAILABLE"

    def test_genuinely_clear_still_grades_green(self):
        """The whole point: absent ≠ clear, but clear must still read clear.
        A fix that blanks a real GREEN trades one false reading for another."""
        m = _manifest(AdvisoryStatus.GREEN, AdvisoryStatus.GREEN)
        assessment, _ = derive_assessment_from_advisories(m)
        assert assessment == "GREEN"

    def test_partial_coverage_grades_on_what_graded(self):
        """Decision 3 — binary, not a coverage threshold. One real GREEN among
        many gaps is still a GREEN; the grey advisory cards carry the gaps."""
        m = _manifest(
            AdvisoryStatus.GREEN,
            AdvisoryStatus.UNAVAILABLE,
            AdvisoryStatus.UNAVAILABLE,
        )
        assessment, _ = derive_assessment_from_advisories(m)
        assert assessment == "GREEN"

    def test_hazard_beats_gap(self):
        m = _manifest(AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.RED)
        assessment, _ = derive_assessment_from_advisories(m)
        assert assessment == "RED"


class TestUngradeableHelper:
    def test_none_manifest_is_not_ungradeable(self):
        """None = the advisory stage never ran = the pre-existing NULL case.
        Distinct from a stage that ran and graded nothing. Conflating them would
        make NULL and UNAVAILABLE mean the same thing."""
        assert advisories_ungradeable(None) is False

    def test_all_unavailable_is_ungradeable(self):
        assert advisories_ungradeable(_manifest(AdvisoryStatus.UNAVAILABLE)) is True

    def test_empty_is_ungradeable(self):
        assert advisories_ungradeable(_manifest()) is True

    def test_one_graded_is_enough(self):
        m = _manifest(AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.GREEN)
        assert advisories_ungradeable(m) is False


# --- 2. The clamp: the LLM cannot override it ------------------------------


class TestDigestClamp:
    def test_digest_green_is_clamped_when_nothing_graded(
        self, db_session, dev_user, tmp_path,
    ):
        """Decision 2. Without this, fixing the derivation changes nothing for a
        normal briefing: the digest's assessment is what the pilot sees, and its
        schema only lets it say GREEN/AMBER/RED.
        """
        from weatherbrief.api.packs import _persist_pack_finalize

        flight = _make_flight()
        save_flight(db_session, flight, dev_user)
        pack_path = tmp_path / "pack"
        pack_path.mkdir(parents=True, exist_ok=True)

        result = _result_with(
            pack_path, _manifest(AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE),
        )

        class FakeDigest:
            assessment = "GREEN"
            assessment_reason = "Nothing of concern along the route."

        result.digest = FakeDigest()
        result.digest_path = pack_path / "digest.json"

        meta = _persist_pack_finalize(
            flight.id, flight, datetime.now(timezone.utc).replace(microsecond=0),
            pack_path, result, db_session, user_id=dev_user,
        )

        assert meta.assessment == ASSESSMENT_UNAVAILABLE
        assert list_packs(db_session, flight.id)[0].assessment == ASSESSMENT_UNAVAILABLE

    def test_digest_still_wins_when_something_graded(
        self, db_session, dev_user, tmp_path,
    ):
        """The clamp must be surgical — with real data behind it, the digest's
        assessment remains the pack's assessment (existing behaviour)."""
        from weatherbrief.api.packs import _persist_pack_finalize

        flight = _make_flight("clamp-noop")
        save_flight(db_session, flight, dev_user)
        pack_path = tmp_path / "pack2"
        pack_path.mkdir(parents=True, exist_ok=True)

        result = _result_with(
            pack_path, _manifest(AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE),
        )

        class FakeDigest:
            assessment = "AMBER"
            assessment_reason = "Crosswind at destination."

        result.digest = FakeDigest()
        result.digest_path = pack_path / "digest.json"

        meta = _persist_pack_finalize(
            flight.id, flight, datetime.now(timezone.utc).replace(microsecond=0),
            pack_path, result, db_session, user_id=dev_user,
        )

        assert meta.assessment == "AMBER"
        assert meta.assessment_reason == "Crosswind at destination."


# --- 2b. The pipeline doesn't even call the LLM ----------------------------


class TestPipelineSkipsDigest:
    """The clamp above is the safety property; this is the cost one. There is
    nothing to narrate, and the LLM would charge us to invent a traffic light.
    """

    def _patch_phases(self, monkeypatch, manifest):
        import weatherbrief.pipeline as pipeline_mod
        from weatherbrief.models import RouteAnalysesManifest, RoutePoint
        from weatherbrief.tasks.advise import AdvisoryResult
        from weatherbrief.tasks.analyze import AnalysisResult
        from weatherbrief.tasks.fetch import FetchResult
        from weatherbrief.tasks.outputs import DigestResult, GrametResult, SkewtResult

        digest_calls: list[int] = []

        # The advisory phase is itself gated on a non-empty analysis manifest, so
        # these have to be real enough to get there — an empty analysis result
        # skips advisories entirely and would make this test vacuous.
        monkeypatch.setattr(pipeline_mod, "run_fetch", lambda **_k: FetchResult(
            route_points=[
                RoutePoint(lat=51.8, lon=-1.3, distance_from_origin_nm=0.0),
                RoutePoint(lat=46.2, lon=7.3, distance_from_origin_nm=420.0),
            ],
            all_forecasts=[], cross_sections=[],
            elevation_profile=None, models_fetched=["ecmwf"],
        ))
        monkeypatch.setattr(pipeline_mod, "run_analysis", lambda **_k: AnalysisResult(
            waypoint_analyses=[],
            route_analyses=[object()],  # non-empty: only its truthiness gates the phase
            route_analyses_manifest=RouteAnalysesManifest(
                route_name="test",
                target_date=(datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat(),
                departure_time=datetime.now(timezone.utc) + timedelta(days=2),
                flight_duration_hours=4.0,
                total_distance_nm=420.0,
                cruise_altitude_ft=8000,
                models=["ecmwf"],
                analyses=[],
            ),
            model_names=["ecmwf"],
        ))
        monkeypatch.setattr(
            pipeline_mod, "run_advisories", lambda **_k: AdvisoryResult(manifest=manifest),
        )
        monkeypatch.setattr(pipeline_mod, "run_gramet", lambda **_k: GrametResult())
        monkeypatch.setattr(pipeline_mod, "run_skewt", lambda **_k: SkewtResult())

        def fake_digest(**_k):
            digest_calls.append(1)
            return DigestResult()

        monkeypatch.setattr(pipeline_mod, "run_llm_digest", fake_digest)
        return digest_calls

    def _run(self, tmp_path):
        from weatherbrief.pipeline import BriefingOptions, execute_briefing

        execute_briefing(
            route=_make_route(),
            departure_time=datetime.now(timezone.utc) + timedelta(days=2),
            options=BriefingOptions(
                output_dir=tmp_path / "pack",
                fetch_gramet=False,
                generate_skewt=False,
                generate_llm_digest=True,
            ),
        )

    def test_digest_skipped_when_nothing_graded(self, monkeypatch, tmp_path):
        calls = self._patch_phases(
            monkeypatch, _manifest(AdvisoryStatus.UNAVAILABLE, AdvisoryStatus.UNAVAILABLE),
        )
        self._run(tmp_path)
        assert calls == [], "the LLM must not be asked to summarise an empty manifest"

    def test_digest_still_runs_when_something_graded(self, monkeypatch, tmp_path):
        """Control — the skip is surgical, not a blanket disable."""
        calls = self._patch_phases(
            monkeypatch, _manifest(AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE),
        )
        self._run(tmp_path)
        assert calls, "a briefing with real data must still get its digest"


# --- 3. Notifications ------------------------------------------------------


class TestNotifySuppressed:
    def _meta(self, assessment: str | None) -> BriefingPackMeta:
        return BriefingPackMeta(
            flight_id="unavail-flight",
            fetch_timestamp=datetime.now(timezone.utc).replace(microsecond=0),
            days_out=2,
            assessment=assessment,
            artifact_path="/tmp/pack",
        )

    def test_unavailable_does_not_notify(self, db_session, dev_user, tmp_path, monkeypatch):
        """Decision 4: not news, not actionable. Must hold even for a flight the
        user asked to always be notified about — there is nothing to notify about.
        """
        import weatherbrief.notify.dispatch as dispatch

        sent: list[str] = []
        monkeypatch.setattr(dispatch, "_send_email", lambda *a, **k: sent.append("email"))
        monkeypatch.setattr(dispatch, "_send_push", lambda *a, **k: sent.append("push"))

        flight = _make_flight()
        flight.notify_override = "notify"  # the strong opt-in
        save_flight(db_session, flight, dev_user)

        dispatch.notify_briefing_refresh(
            db_session, flight, self._meta(ASSESSMENT_UNAVAILABLE), tmp_path,
            user_id=dev_user, present=False,
        )

        assert sent == [], "an unassessable briefing must not push or email"

    def test_control_a_graded_briefing_does_notify(
        self, db_session, dev_user, tmp_path, monkeypatch,
    ):
        """Control for the test above — proves the suppression is what silences
        UNAVAILABLE, and not some unrelated early return in this fixture setup.
        Identical arrangement, only the assessment differs."""
        import weatherbrief.notify.dispatch as dispatch

        sent: list[str] = []
        monkeypatch.setattr(dispatch, "_send_email", lambda *a, **k: sent.append("email"))
        monkeypatch.setattr(dispatch, "_send_push", lambda *a, **k: sent.append("push"))

        flight = _make_flight("notify-control")
        flight.notify_override = "notify"
        save_flight(db_session, flight, dev_user)

        dispatch.notify_briefing_refresh(
            db_session, flight, self._meta("AMBER"), tmp_path,
            user_id=dev_user, present=False,
        )

        assert sent, "a graded briefing must still notify — otherwise the test above proves nothing"

    def test_unavailable_never_ranks_as_worsening(self):
        """UNAVAILABLE is the absence of a rung on the GREEN<AMBER<RED ladder,
        not a rung on it — so it can't manufacture a 'worsened' transition."""
        from weatherbrief.notify.dispatch import _ASSESSMENT_RANK

        assert _ASSESSMENT_RANK.get(ASSESSMENT_UNAVAILABLE) is None
