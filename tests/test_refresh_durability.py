"""Durable refresh tracking + resume after a container restart (issue #499).

Three layers under test:

* the write-through mirror in ``_RefreshRegistry`` (queued → running →
  heartbeat → terminal, and the crash case where the terminal write never
  happens);
* ``decide_resume`` — abandon vs re-run for an orphan row at boot;
* the ``/refresh/status`` fallback that reports "interrupted" / "abandoned"
  instead of silently nothing after a restart.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow

from weatherbrief.api.app import create_app
from weatherbrief.api.packs import _RefreshRegistry
from weatherbrief.db.models import BriefingRefreshJobRow, FlightRow
from weatherbrief.storage import refresh_jobs
from weatherbrief.tasks import refresh_resume

FLIGHT_ID = "f1"
OTHER_USER = "someone-else"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def app_db(monkeypatch):
    """In-memory DB with a dev user + one future flight, wired as SessionLocal.

    The registry's write-through opens its own session (it runs on pipeline
    worker threads), so the storage module's ``SessionLocal`` is what has to
    point at the test engine.
    """
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    for uid in (DEV_USER_ID, OTHER_USER):
        s.add(UserRow(id=uid, provider="google", provider_sub=uid,
                      email=f"{uid}@localhost", display_name=uid, approved=True))
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
    s.add(FlightRow(id=FLIGHT_ID, user_id=DEV_USER_ID, route_name="EGTF-LFAT",
                    waypoints_json='["EGTF", "LFAT"]',
                    departure_time=_now() + timedelta(days=3),
                    created_at=_now()))
    s.commit()
    s.close()

    monkeypatch.setattr(refresh_jobs, "SessionLocal", TestSession)
    monkeypatch.setattr(refresh_resume, "SessionLocal", TestSession)
    yield TestSession
    engine.dispose()


def _jobs(app_db) -> list[BriefingRefreshJobRow]:
    s = app_db()
    try:
        return s.query(BriefingRefreshJobRow).order_by(BriefingRefreshJobRow.id).all()
    finally:
        s.close()


def _only_job(app_db) -> BriefingRefreshJobRow:
    rows = _jobs(app_db)
    assert len(rows) == 1
    return rows[0]


# ---------------------------------------------------------------------------
# Registry write-through
# ---------------------------------------------------------------------------


class TestWriteThrough:
    def test_try_register_inserts_queued_row(self, app_db):
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID, source="siri")

        row = _only_job(app_db)
        assert row.flight_id == FLIGHT_ID
        assert row.user_id == DEV_USER_ID
        assert row.status == "queued"
        assert row.triggered_by == "user"
        assert row.source == "siri"
        assert row.attempt == 1
        assert row.started_at is None

    def test_set_refreshing_marks_running(self, app_db):
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.set_refreshing(FLIGHT_ID)

        row = _only_job(app_db)
        assert row.status == "running"
        assert row.started_at is not None

    def test_pack_path_is_recorded(self, app_db):
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.note_pack_path(FLIGHT_ID, "/data/packs/dev/f1/2026-07-26T10-00-00p00-00")

        assert _only_job(app_db).pack_path.endswith("2026-07-26T10-00-00p00-00")

    def test_progress_heartbeat_is_throttled(self, app_db):
        """One durable write per HEARTBEAT_MIN_INTERVAL, not one per stage."""
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.set_refreshing(FLIGHT_ID)

        reg.update_progress(FLIGHT_ID, "fetch")
        reg.update_progress(FLIGHT_ID, "analyze")

        # In-memory state always tracks the latest stage...
        assert reg.get(FLIGHT_ID).stage == "analyze"
        # ...the durable row only took the first (throttled) write.
        assert _only_job(app_db).stage == "fetch"

    def test_success_closes_the_row(self, app_db):
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.set_refreshing(FLIGHT_ID)
        reg.mark_outcome(FLIGHT_ID, "succeeded")
        reg.unregister(FLIGHT_ID)

        row = _only_job(app_db)
        assert row.status == "succeeded"
        assert row.finished_at is not None
        assert row.last_error is None

    def test_failure_records_the_error(self, app_db):
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.mark_outcome(FLIGHT_ID, "failed", "open-meteo timed out")
        reg.unregister(FLIGHT_ID)

        row = _only_job(app_db)
        assert row.status == "failed"
        assert "open-meteo" in row.last_error

    def test_unregister_without_an_outcome_is_a_failure(self, app_db):
        """An unmarked close is a bug elsewhere, not a success."""
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.unregister(FLIGHT_ID)

        assert _only_job(app_db).status == "failed"

    def test_crash_leaves_a_non_terminal_orphan(self, app_db):
        """The killed-process case: no unregister ever runs."""
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.set_refreshing(FLIGHT_ID)
        # ...container dies here. Nothing else happens.

        s = app_db()
        try:
            orphans = refresh_jobs.list_orphans(s)
        finally:
            s.close()
        assert [o.flight_id for o in orphans] == [FLIGHT_ID]

    def test_finished_jobs_are_not_orphans(self, app_db):
        reg = _RefreshRegistry(durable=True)
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.mark_outcome(FLIGHT_ID, "succeeded")
        reg.unregister(FLIGHT_ID)

        s = app_db()
        try:
            assert refresh_jobs.list_orphans(s) == []
        finally:
            s.close()

    def test_non_durable_registry_writes_nothing(self, app_db):
        reg = _RefreshRegistry()
        reg.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        reg.set_refreshing(FLIGHT_ID)
        reg.mark_outcome(FLIGHT_ID, "succeeded")
        reg.unregister(FLIGHT_ID)

        assert _jobs(app_db) == []

    def test_resume_bypasses_the_queue_cap(self):
        """A resume is finishing work the user already asked for."""
        reg = _RefreshRegistry()
        for i in range(reg.MAX_QUEUE_DEPTH):
            reg.try_register(f"other-{i}", user_id=f"u{i}")
        assert reg.try_register(FLIGHT_ID, triggered_by="resume", user_id="u0") is not None


# ---------------------------------------------------------------------------
# WB_REFRESH_MAX_ATTEMPTS
# ---------------------------------------------------------------------------


class TestMaxAttempts:
    def test_default_is_original_plus_one_resume(self, monkeypatch):
        monkeypatch.delenv("WB_REFRESH_MAX_ATTEMPTS", raising=False)
        assert refresh_resume.resume_max_attempts() == 2

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "3")
        assert refresh_resume.resume_max_attempts() == 3

    def test_one_disables_resume(self, monkeypatch):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "1")
        assert refresh_resume.resume_max_attempts() == 1

    def test_zero_clamps_to_one(self, monkeypatch):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "0")
        assert refresh_resume.resume_max_attempts() == 1

    def test_garbage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "lots")
        assert refresh_resume.resume_max_attempts() == 2


# ---------------------------------------------------------------------------
# decide_resume
# ---------------------------------------------------------------------------


def _make_job(session, **overrides) -> BriefingRefreshJobRow:
    kwargs = dict(
        flight_id=FLIGHT_ID,
        user_id=DEV_USER_ID,
        triggered_by="user",
        source="user",
        status="running",
        attempt=1,
        created_at=_now(),
        started_at=_now(),
    )
    kwargs.update(overrides)
    row = BriefingRefreshJobRow(**kwargs)
    session.add(row)
    session.flush()
    return row


class TestDecideResume:
    @pytest.fixture(autouse=True)
    def _no_packs(self, monkeypatch):
        """Default: the flight has no packs, so the gate never runs."""
        from weatherbrief.storage import flights as flights_mod

        monkeypatch.setattr(flights_mod, "latest_pack", lambda db, fid: None)

    def test_resumes_a_fresh_interruption(self, app_db):
        s = app_db()
        try:
            job = _make_job(s)
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "resume"
        assert "attempt 2 of 2" in decision.reason

    def test_abandons_when_the_retry_budget_is_spent(self, app_db):
        s = app_db()
        try:
            job = _make_job(s, attempt=2, triggered_by="resume")
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "abandon"
        assert "retry budget" in decision.reason

    def test_max_attempts_one_never_resumes(self, app_db):
        s = app_db()
        try:
            job = _make_job(s)
            decision = refresh_resume.decide_resume(s, job, max_attempts=1)
        finally:
            s.close()
        assert decision.action == "abandon"

    def test_abandons_a_pinned_backtest(self, app_db):
        s = app_db()
        try:
            job = _make_job(s, as_of_date=date(2026, 7, 1))
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "abandon"
        assert "as_of_date" in decision.reason

    def test_abandons_when_the_flight_is_gone(self, app_db):
        s = app_db()
        try:
            job = _make_job(s, flight_id="deleted-flight")
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "abandon"
        assert "no longer exists" in decision.reason

    def test_abandons_a_departed_flight(self, app_db):
        s = app_db()
        try:
            s.get(FlightRow, FLIGHT_ID).departure_time = _now() - timedelta(hours=1)
            s.flush()
            job = _make_job(s)
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "abandon"
        assert "already departed" in decision.reason

    def test_abandons_beyond_the_forecast_horizon(self, app_db):
        s = app_db()
        try:
            s.get(FlightRow, FLIGHT_ID).departure_time = _now() + timedelta(days=60)
            s.flush()
            job = _make_job(s)
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "abandon"
        assert "forecast horizon" in decision.reason

    def test_abandons_when_the_gate_no_longer_wants_a_full_run(
        self, app_db, monkeypatch,
    ):
        """The scheduler already re-briefed it — don't burn the compute twice."""
        self._patch_gate(monkeypatch, mode="none")
        s = app_db()
        try:
            job = _make_job(s)
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "abandon"
        assert "gate now says none" in decision.reason

    def test_resumes_when_the_gate_still_wants_a_full_run(self, app_db, monkeypatch):
        self._patch_gate(monkeypatch, mode="full")
        s = app_db()
        try:
            job = _make_job(s)
            decision = refresh_resume.decide_resume(s, job, max_attempts=2)
        finally:
            s.close()
        assert decision.action == "resume"

    @staticmethod
    def _patch_gate(monkeypatch, *, mode: str) -> None:
        from types import SimpleNamespace

        from weatherbrief.api import packs as packs_mod
        from weatherbrief.storage import flights as flights_mod

        monkeypatch.setattr(flights_mod, "latest_pack", lambda db, fid: "pack")
        monkeypatch.setattr(packs_mod, "_build_data_status", lambda pack, flight: "status")
        monkeypatch.setattr(
            packs_mod, "decide_refresh",
            lambda status, days_out: SimpleNamespace(mode=mode, reason="because"),
        )


# ---------------------------------------------------------------------------
# reconcile_one — the boot-time pass end to end
# ---------------------------------------------------------------------------


class TestReconcileOne:
    @pytest.fixture(autouse=True)
    def _no_packs(self, monkeypatch):
        from weatherbrief.storage import flights as flights_mod

        monkeypatch.setattr(flights_mod, "latest_pack", lambda db, fid: None)

    @pytest.fixture
    def ran(self, monkeypatch):
        """Capture calls to the shared pipeline runner instead of running it."""
        calls: list[dict] = []

        def _fake(flight_id, app_state, user_id, *, triggered_by="scheduler"):
            calls.append({
                "flight_id": flight_id,
                "user_id": user_id,
                "triggered_by": triggered_by,
            })
            return True  # the real one returns "did the pipeline run?"

        from weatherbrief import scheduler as scheduler_mod

        monkeypatch.setattr(scheduler_mod, "_auto_refresh_one", _fake)
        return calls

    def _insert_orphan(self, app_db, **overrides) -> int:
        s = app_db()
        try:
            job = _make_job(s, **overrides)
            s.commit()
            return job.id
        finally:
            s.close()

    @pytest.mark.asyncio
    async def test_resume_reruns_and_opens_a_second_attempt(
        self, app_db, ran, monkeypatch,
    ):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "2")
        job_id = self._insert_orphan(app_db, stage="fetch")

        await refresh_resume.reconcile_one(job_id, object())

        assert ran == [{
            "flight_id": FLIGHT_ID, "user_id": DEV_USER_ID, "triggered_by": "resume",
        }]
        first, second = _jobs(app_db)
        assert first.id == job_id
        assert first.status == "failed"
        assert "interrupted by process restart" in first.last_error
        # The resume is its own row, so the post-mortem keeps one row per attempt.
        assert second.attempt == 2
        assert second.triggered_by == "resume"
        assert second.status == "succeeded"

    @pytest.mark.asyncio
    async def test_abandon_closes_the_row_without_running_anything(
        self, app_db, ran, monkeypatch,
    ):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "2")
        job_id = self._insert_orphan(app_db, attempt=2, triggered_by="resume")

        await refresh_resume.reconcile_one(job_id, object())

        assert ran == []
        row = _only_job(app_db)
        assert row.id == job_id
        assert row.status == "abandoned"
        assert "retry budget" in row.last_error

    @pytest.mark.asyncio
    async def test_a_failing_resume_still_closes_its_row(
        self, app_db, ran, monkeypatch,
    ):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "3")

        def _boom(*a, **kw):
            raise RuntimeError("open-meteo exploded")

        from weatherbrief import scheduler as scheduler_mod

        monkeypatch.setattr(scheduler_mod, "_auto_refresh_one", _boom)
        job_id = self._insert_orphan(app_db)

        await refresh_resume.reconcile_one(job_id, object())

        _first, second = _jobs(app_db)
        assert second.status == "failed"
        assert "exploded" in second.last_error

    @pytest.mark.asyncio
    async def test_a_gate_skip_is_not_recorded_as_a_briefing(
        self, app_db, monkeypatch,
    ):
        """`_auto_refresh_one` returning False must not close the row as success."""
        from weatherbrief import scheduler as scheduler_mod

        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "3")
        monkeypatch.setattr(
            scheduler_mod, "_auto_refresh_one", lambda *a, **kw: False,
        )
        job_id = self._insert_orphan(app_db)

        await refresh_resume.reconcile_one(job_id, object())

        _first, second = _jobs(app_db)
        assert second.status == "skipped"
        assert "gate declined" in second.last_error

    @pytest.mark.asyncio
    async def test_a_flight_already_refreshing_is_not_claimed_as_resumed(
        self, app_db, ran,
    ):
        """The interrupted row must not claim a resume that never happened."""
        from weatherbrief.api.packs import refresh_registry

        job_id = self._insert_orphan(app_db)
        # Something else grabbed the flight during the startup delay.
        assert refresh_registry.try_register(FLIGHT_ID, user_id=DEV_USER_ID) is not None
        try:
            await refresh_resume.reconcile_one(job_id, object())
        finally:
            refresh_registry.mark_outcome(FLIGHT_ID, "succeeded")
            refresh_registry.unregister(FLIGHT_ID)

        assert ran == []
        orphan = next(j for j in _jobs(app_db) if j.id == job_id)
        assert orphan.status == "abandoned"
        assert "already active" in orphan.last_error
        assert "resumed as" not in orphan.last_error

    @pytest.mark.asyncio
    async def test_already_terminal_row_is_left_alone(self, app_db, ran):
        job_id = self._insert_orphan(app_db, status="succeeded", finished_at=_now())

        await refresh_resume.reconcile_one(job_id, object())

        assert ran == []
        assert _only_job(app_db).status == "succeeded"

    def test_snapshot_orphans_lists_only_non_terminal_rows(self, app_db):
        live = self._insert_orphan(app_db, status="running")
        self._insert_orphan(app_db, status="succeeded", finished_at=_now())
        self._insert_orphan(app_db, status="abandoned", finished_at=_now())

        assert refresh_resume.snapshot_orphans() == [live]


# ---------------------------------------------------------------------------
# /refresh/status fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test")
    app = create_app()

    def _override_get_db():
        s = app_db()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


STATUS_URL = f"/api/flights/{FLIGHT_ID}/packs/refresh/status"


class TestStatusFallback:
    def _insert(self, app_db, **overrides):
        s = app_db()
        try:
            _make_job(s, **overrides)
            s.commit()
        finally:
            s.close()

    def test_no_job_reports_inactive(self, client):
        assert client.get(STATUS_URL).json() == {"active": False}

    def test_interrupted_row_reports_retrying(self, client, app_db, monkeypatch):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "2")
        self._insert(app_db, status="running", stage="fetch", heartbeat_at=_now())

        body = client.get(STATUS_URL).json()
        assert body["active"] is True
        assert body["status"] == "interrupted"
        assert body["stage"] == "fetch"
        assert body["attempt"] == 1
        assert body["max_attempts"] == 2
        assert body["will_retry"] is True

    def test_last_attempt_reports_no_retry(self, client, app_db, monkeypatch):
        monkeypatch.setenv("WB_REFRESH_MAX_ATTEMPTS", "2")
        self._insert(app_db, status="running", attempt=2, heartbeat_at=_now())

        body = client.get(STATUS_URL).json()
        assert body["status"] == "interrupted"
        assert body["will_retry"] is False

    def test_abandoned_row_reports_gave_up(self, client, app_db):
        self._insert(
            app_db, status="abandoned", finished_at=_now(),
            last_error="interrupted by process restart; retry budget spent",
        )

        body = client.get(STATUS_URL).json()
        assert body["active"] is False
        assert body["status"] == "abandoned"
        assert "retry budget" in body["last_error"]

    def test_succeeded_row_is_not_surfaced(self, client, app_db):
        self._insert(app_db, status="succeeded", finished_at=_now())
        assert client.get(STATUS_URL).json() == {"active": False}

    def test_a_leaked_row_cannot_pin_a_flight_active(self, client, app_db):
        """Same staleness bound the warm loop uses (STALE_ENTRY_SECONDS)."""
        stale = _now() - timedelta(
            seconds=_RefreshRegistry.STALE_ENTRY_SECONDS + 600,
        )
        self._insert(app_db, status="running", created_at=stale, started_at=stale)

        assert client.get(STATUS_URL).json() == {"active": False}

    def test_another_users_job_is_not_disclosed(self, client, app_db):
        self._insert(app_db, status="running", user_id=OTHER_USER)
        assert client.get(STATUS_URL).json() == {"active": False}

    def test_live_entry_still_wins(self, client, app_db):
        """A registered refresh reports live progress, not the durable row."""
        from weatherbrief.api.packs import refresh_registry

        self._insert(app_db, status="running")
        entry = refresh_registry.try_register(FLIGHT_ID, user_id=DEV_USER_ID)
        assert entry is not None
        try:
            refresh_registry.set_refreshing(FLIGHT_ID)
            body = client.get(STATUS_URL).json()
            assert body["active"] is True
            assert body["status"] == "refreshing"
        finally:
            refresh_registry.mark_outcome(FLIGHT_ID, "succeeded")
            refresh_registry.unregister(FLIGHT_ID)
