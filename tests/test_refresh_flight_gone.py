"""A refresh whose flight is moved/deleted mid-run stops early and is abandoned.

The safety property under test: a gate abandons only on a *confirmed* absence
of the flight row — never on a DB error, never at a non-gate stage — and the
clean-up only ever removes the run's own pack directory.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FlightRow
from weatherbrief.tasks import refresh_flight_gone as gone_mod
from weatherbrief.tasks.refresh_flight_gone import (
    GATE_STAGES,
    FlightGoneError,
    check_flight_at_stage,
    discard_refresh_for_gone_flight,
    flight_confirmed_gone,
)

FLIGHT_ID = "egtf_lfat-2026-12-01-abcd"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def flight_db(monkeypatch):
    """Test engine with one flight, wired as the module's SessionLocal."""
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(id=DEV_USER_ID, provider="google", provider_sub=DEV_USER_ID,
                  email="dev@localhost", display_name="dev", approved=True))
    s.flush()
    s.add(FlightRow(id=FLIGHT_ID, user_id=DEV_USER_ID, route_name="egtf_lfat",
                    waypoints_json='["EGTF", "LFAT"]',
                    departure_time=_now() + timedelta(days=3), created_at=_now()))
    s.commit()
    s.close()
    monkeypatch.setattr(gone_mod, "SessionLocal", TestSession)
    yield TestSession
    engine.dispose()


def _delete_flight(session_factory) -> None:
    s = session_factory()
    try:
        s.delete(s.get(FlightRow, FLIGHT_ID))
        s.commit()
    finally:
        s.close()


def _make_pack(tmp_path: Path, flight_id: str = FLIGHT_ID, ts: str = "2026-11-26T13-21-17p00-00") -> Path:
    pack = tmp_path / "packs" / DEV_USER_ID / flight_id / ts
    pack.mkdir(parents=True)
    (pack / "briefing.json").write_text("{}")
    return pack


class TestFlightConfirmedGone:
    def test_existing_flight_is_not_gone(self, flight_db):
        assert flight_confirmed_gone(FLIGHT_ID) is False

    def test_deleted_flight_is_gone(self, flight_db):
        _delete_flight(flight_db)
        assert flight_confirmed_gone(FLIGHT_ID) is True

    def test_db_error_fails_open(self, monkeypatch):
        """Can't tell → the flight is assumed to exist; a refresh never dies on doubt."""
        def _broken_session():
            raise RuntimeError("pool exhausted")

        monkeypatch.setattr(gone_mod, "SessionLocal", _broken_session)
        assert flight_confirmed_gone(FLIGHT_ID) is False

    def test_query_error_fails_open(self, monkeypatch):
        class _Session:
            def get(self, *_a, **_kw):
                raise RuntimeError("lost connection")

            def close(self):
                pass

        monkeypatch.setattr(gone_mod, "SessionLocal", _Session)
        assert flight_confirmed_gone(FLIGHT_ID) is False


class TestCheckFlightAtStage:
    def test_non_gate_stages_never_query(self, monkeypatch):
        def _must_not_be_called(_fid):
            raise AssertionError("queried at a non-gate stage")

        monkeypatch.setattr(gone_mod, "flight_confirmed_gone", _must_not_be_called)
        for stage in ("route_interpolation", "fetch_forecasts", "grib_enrichment",
                      "waypoint_analysis", "save_snapshot", "llm_digest",
                      "live_observations"):
            check_flight_at_stage(FLIGHT_ID, stage)

    def test_grib_enrichment_is_not_a_gate(self):
        """Emitted inside the decode code — raising there is not safe."""
        assert "grib_enrichment" not in GATE_STAGES

    def test_gate_raises_when_gone(self, flight_db):
        _delete_flight(flight_db)
        with pytest.raises(FlightGoneError) as info:
            check_flight_at_stage(FLIGHT_ID, "briefing_ready")
        assert info.value.stage == "briefing_ready"
        assert info.value.flight_id == FLIGHT_ID

    def test_gate_passes_when_present(self, flight_db):
        for stage in GATE_STAGES:
            check_flight_at_stage(FLIGHT_ID, stage)


class TestDiscardRefreshForGoneFlight:
    def test_real_failure_on_existing_flight_is_untouched(self, flight_db, tmp_path):
        pack = _make_pack(tmp_path)
        assert discard_refresh_for_gone_flight(FLIGHT_ID, pack, RuntimeError("GRIB")) is False
        assert pack.is_dir()

    def test_fk_failure_after_move_is_discarded(self, flight_db, tmp_path):
        """The 11-26 case: the final insert hit the FK because the flight moved."""
        pack = _make_pack(tmp_path)
        _delete_flight(flight_db)
        exc = IntegrityError("INSERT INTO briefing_packs", {}, Exception("1452"))
        assert discard_refresh_for_gone_flight(FLIGHT_ID, pack, exc) is True
        assert not pack.exists()
        assert not pack.parent.exists(), "empty flight dir should go too"

    def test_sibling_packs_keep_the_flight_dir(self, flight_db, tmp_path):
        pack = _make_pack(tmp_path)
        sibling = _make_pack(tmp_path, ts="2026-11-25T08-00-00p00-00")
        assert discard_refresh_for_gone_flight(FLIGHT_ID, pack, FlightGoneError(FLIGHT_ID)) is True
        assert not pack.exists()
        assert sibling.is_dir()

    def test_gate_error_cleans_up_without_requery(self, monkeypatch, tmp_path):
        """A FlightGoneError is already the confirmation; even if the ID was
        re-created since, the run's own pack dir is referenced by no row."""
        monkeypatch.setattr(gone_mod, "flight_confirmed_gone", lambda _fid: False)
        pack = _make_pack(tmp_path)
        assert discard_refresh_for_gone_flight(FLIGHT_ID, pack, FlightGoneError(FLIGHT_ID)) is True
        assert not pack.exists()

    def test_never_deletes_outside_the_flight_dir(self, tmp_path):
        stray = tmp_path / "somewhere" / "else"
        stray.mkdir(parents=True)
        assert discard_refresh_for_gone_flight(FLIGHT_ID, stray, FlightGoneError(FLIGHT_ID)) is True
        assert stray.is_dir()

    def test_missing_pack_path_is_fine(self):
        assert discard_refresh_for_gone_flight(FLIGHT_ID, None, FlightGoneError(FLIGHT_ID)) is True

    def test_db_error_leaves_a_real_failure_a_failure(self, monkeypatch, tmp_path):
        def _broken_session():
            raise RuntimeError("pool exhausted")

        monkeypatch.setattr(gone_mod, "SessionLocal", _broken_session)
        pack = _make_pack(tmp_path)
        assert discard_refresh_for_gone_flight(FLIGHT_ID, pack, RuntimeError("x")) is False
        assert pack.is_dir()


    def test_rmtree_failure_is_logged(self, monkeypatch, tmp_path, caplog):
        """A half-removed dir is the orphan we exist to prevent — never silent."""
        def _boom(_path):
            raise PermissionError("denied")

        monkeypatch.setattr(gone_mod.shutil, "rmtree", _boom)
        pack = _make_pack(tmp_path)
        with caplog.at_level("WARNING", logger=gone_mod.__name__):
            assert discard_refresh_for_gone_flight(FLIGHT_ID, pack, FlightGoneError(FLIGHT_ID))
        assert "could not remove" in caplog.text


class _Registry:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str, str | None]] = []

    def mark_outcome(self, flight_id: str, status: str, error: str | None = None) -> None:
        self.outcomes.append((flight_id, status, error))


class TestCloseFailedRefresh:
    def test_gone_flight_closes_abandoned(self, tmp_path):
        reg = _Registry()
        pack = _make_pack(tmp_path)
        exc = FlightGoneError(FLIGHT_ID, "fetch_gramet")
        assert gone_mod.close_failed_refresh(reg, FLIGHT_ID, pack, exc, label="Test refresh")
        assert reg.outcomes == [(FLIGHT_ID, "abandoned", str(exc))]
        assert not pack.exists()

    def test_real_failure_closes_failed(self, flight_db, tmp_path):
        reg = _Registry()
        pack = _make_pack(tmp_path)
        exc = RuntimeError("GRIB")
        assert not gone_mod.close_failed_refresh(reg, FLIGHT_ID, pack, exc, label="Test refresh")
        assert reg.outcomes == [(FLIGHT_ID, "failed", "GRIB")]
        assert pack.is_dir()


class TestPipelineStopsBeforeDigest:
    """Through the real orchestrator: once the flight is gone, the digest and
    the provisional persist never run."""

    def _patch_phases(self, monkeypatch, digest_calls: list):
        from weatherbrief.tasks.advise import AdvisoryResult
        from weatherbrief.tasks.analyze import AnalysisResult
        from weatherbrief.tasks.fetch import FetchResult
        from weatherbrief.tasks.outputs import DigestResult, GrametResult, SkewtResult
        import weatherbrief.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "run_fetch", lambda **_: FetchResult(
            route_points=[], all_forecasts=[], cross_sections=[],
            elevation_profile=None, models_fetched=["gfs"],
        ))
        monkeypatch.setattr(pipeline_mod, "run_analysis", lambda **_: AnalysisResult(
            waypoint_analyses=[], route_analyses=[], route_analyses_manifest=None,
            model_names=["gfs"],
        ))
        monkeypatch.setattr(pipeline_mod, "run_advisories", lambda **_: AdvisoryResult(manifest=None))
        monkeypatch.setattr(pipeline_mod, "run_gramet", lambda **_: GrametResult())
        monkeypatch.setattr(pipeline_mod, "run_skewt", lambda **_: SkewtResult())

        def _digest(**_):
            digest_calls.append(1)
            return DigestResult()

        monkeypatch.setattr(pipeline_mod, "run_llm_digest", _digest)

    def _run(self, tmp_path, progress, ready):
        from weatherbrief.models import RouteConfig, Waypoint
        from weatherbrief.pipeline import BriefingOptions, execute_briefing

        route = RouteConfig(
            name="test",
            waypoints=[Waypoint(icao="EGTF", name="Fairoaks", lat=51.35, lon=-0.56),
                       Waypoint(icao="LFAT", name="Le Touquet", lat=50.52, lon=1.62)],
            cruise_altitude_ft=6000, flight_duration_hours=1.0,
        )
        execute_briefing(
            route=route, departure_time=_now() + timedelta(days=2),
            options=BriefingOptions(output_dir=tmp_path / "pack", fetch_gramet=True,
                                    generate_skewt=True, generate_llm_digest=True),
            progress_callback=progress, briefing_ready_callback=ready,
        )

    def test_gone_flight_never_reaches_the_digest(self, monkeypatch, tmp_path, flight_db):
        digest_calls: list = []
        ready_calls: list = []
        self._patch_phases(monkeypatch, digest_calls)

        def progress(stage, _detail=None):
            if stage == "generate_skewt":
                _delete_flight(flight_db)  # the pilot's move lands here
            check_flight_at_stage(FLIGHT_ID, stage)

        with pytest.raises(FlightGoneError) as info:
            self._run(tmp_path, progress, ready_calls.append)
        assert info.value.stage == "briefing_ready"
        assert digest_calls == []
        assert ready_calls == [], "provisional persist must not run for a gone flight"

    def test_present_flight_runs_to_the_end(self, monkeypatch, tmp_path, flight_db):
        digest_calls: list = []
        ready_calls: list = []
        self._patch_phases(monkeypatch, digest_calls)
        self._run(tmp_path, lambda s, _d=None: check_flight_at_stage(FLIGHT_ID, s),
                  ready_calls.append)
        assert digest_calls == [1]
        assert len(ready_calls) == 1


class TestResumeAbandons:
    @pytest.mark.asyncio
    async def test_resume_of_a_moved_flight_records_abandoned(self, monkeypatch):
        """`_auto_refresh_one` raising FlightGoneError closes the job abandoned."""
        from weatherbrief import scheduler as scheduler_mod
        from weatherbrief.tasks import refresh_resume

        outcomes: list[tuple] = []

        class _Registry:
            def try_register(self, *a, **kw):
                return object()

            def set_refreshing(self, *_a):
                pass

            def mark_outcome(self, fid, status, error=None):
                outcomes.append((fid, status, error))

            def unregister(self, *_a):
                pass

        class _Job:
            flight_id = FLIGHT_ID
            attempt = 1
            source = "user"
            user_id = DEV_USER_ID
            status = "running"

        class _Session:
            def get(self, model, _id):
                return _Job() if model.__name__ == "BriefingRefreshJobRow" else object()

            def close(self):
                pass

        def _raise_gone(*_a, **_kw):
            raise FlightGoneError(FLIGHT_ID, "briefing_ready")

        monkeypatch.setattr("weatherbrief.api.packs.refresh_registry", _Registry())
        monkeypatch.setattr(refresh_resume, "SessionLocal", _Session)
        monkeypatch.setattr(refresh_resume, "decide_resume",
                            lambda db, job: type("D", (), {"action": "resume", "reason": "attempt 2 of 2"})())
        monkeypatch.setattr(refresh_resume, "_close_orphan", lambda *a, **kw: None)
        monkeypatch.setattr(scheduler_mod, "_auto_refresh_one", _raise_gone)

        await refresh_resume.reconcile_one(1, app_state=None)
        assert [o[1] for o in outcomes] == ["abandoned"]
