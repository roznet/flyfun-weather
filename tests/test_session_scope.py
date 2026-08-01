"""Session scope-down guards (#mysql-review).

The audit found pooled web-pool connections pinned across minutes of non-DB
work: the briefing pipeline (GRIB + LLM), the aviationweather.gov METAR/TAF
fetch, and the timing-scenario GRIB scan. Per site these tests spy the
session factory and the slow call (mocked — no network, no real pipeline)
and assert:

* no session remains unclosed across the slow-call boundary;
* the finalize/commit after the slow work happens in a FRESH session;
* notify still fires after commit on the refresh path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class _SessionSpy:
    """Session factory recording every session it hands out."""

    def __init__(self) -> None:
        self.sessions: list[MagicMock] = []

    def __call__(self) -> MagicMock:
        session = MagicMock(name=f"session-{len(self.sessions)}")
        self.sessions.append(session)
        return session

    def unclosed(self) -> list[MagicMock]:
        return [s for s in self.sessions if not s.close.called]


# ---------------------------------------------------------------------------
# scheduler.process_auto_refreshes — the due-flight read session must be
# closed before asyncio.to_thread(_auto_refresh_one, ...) runs.
# ---------------------------------------------------------------------------


class TestProcessAutoRefreshes:
    @pytest.mark.asyncio
    async def test_read_session_closed_before_per_flight_pipeline(self):
        from weatherbrief import scheduler
        from weatherbrief.api import packs as packs_mod

        spy = _SessionSpy()
        seen: dict = {}

        def _fake_refresh(flight_id, app_state, user_id, **kwargs):
            seen["unclosed"] = spy.unclosed()
            return True

        registry = MagicMock()
        registry.try_register.return_value = object()

        with (
            patch.object(scheduler, "SessionLocal", spy),
            patch.object(
                scheduler, "_find_due_flights",
                return_value=[SimpleNamespace(id="f1", user_id="u1")],
            ),
            patch.object(scheduler, "_auto_refresh_one", _fake_refresh),
            patch.object(packs_mod, "refresh_registry", registry),
        ):
            await scheduler.process_auto_refreshes(SimpleNamespace())

        # The due-flight read session was closed before the refresh ran.
        assert seen["unclosed"] == []
        # Two sessions total: the due-flight read, then the last_auto_refresh
        # marker write after the refresh — committed and closed.
        assert len(spy.sessions) == 2
        spy.sessions[1].commit.assert_called_once()
        assert all(s.close.called for s in spy.sessions)


# ---------------------------------------------------------------------------
# scheduler._auto_refresh_one — reads + gate + _prepare_refresh in one short
# session, execute_briefing with none held, finalize+commit+notify in a
# fresh one.
# ---------------------------------------------------------------------------


class TestAutoRefreshOne:
    def _flight(self):
        return SimpleNamespace(
            id="f1", user_id="u1",
            departure_time=datetime.now(timezone.utc) + timedelta(days=2),
            profile_id=None,
        )

    def test_pipeline_runs_sessionless_and_finalize_uses_a_fresh_one(self):
        from weatherbrief import scheduler
        from weatherbrief.api import packs as packs_mod
        from weatherbrief.storage import flights as flights_mod

        spy = _SessionSpy()
        seen: dict = {}

        def _fake_exec(**kwargs):
            seen["unclosed"] = spy.unclosed()
            return SimpleNamespace(usage=SimpleNamespace())

        def _fake_notify(db, flight, meta, pack_path, **kwargs):
            seen["notify_db"] = db
            seen["committed_before_notify"] = db.commit.called

        registry = MagicMock()
        registry.get_timing.return_value = (1.0, 2.0)
        fetch_ts = datetime.now(timezone.utc)

        with (
            patch.object(scheduler, "SessionLocal", spy),
            patch.object(flights_mod, "_row_to_flight", return_value=self._flight()),
            patch.object(flights_mod, "list_packs", return_value=[]),
            patch.object(packs_mod, "refresh_registry", registry),
            patch.object(
                packs_mod, "_prepare_refresh",
                return_value=(MagicMock(), fetch_ts, Path("/tmp/pack"), MagicMock(), {"m": 1}, None),
            ) as mock_prepare,
            patch("weatherbrief.pipeline.execute_briefing", side_effect=_fake_exec) as mock_exec,
            patch.object(packs_mod, "_finalize_refresh", return_value=MagicMock()) as mock_finalize,
            patch.object(packs_mod, "_notify_refresh_complete", side_effect=_fake_notify),
        ):
            ran = scheduler._auto_refresh_one("f1", SimpleNamespace(db_path="/fake/db"), "u1")

        assert ran is True
        mock_exec.assert_called_once()
        # No session pinned across the pipeline.
        assert seen["unclosed"] == []
        # Reads used the first session; finalize + notify a fresh second one.
        assert len(spy.sessions) == 2
        read_db, final_db = spy.sessions
        assert mock_prepare.call_args.kwargs["db"] is read_db
        assert mock_finalize.call_args.args[5] is final_db
        final_db.commit.assert_called_once()
        # Notify-after-commit ordering, on that same fresh session.
        assert seen["notify_db"] is final_db
        assert seen["committed_before_notify"]
        assert read_db.close.called and final_db.close.called


# ---------------------------------------------------------------------------
# tasks.verification.collect_and_store — reads commit + close before
# fetch_observations_batch (aviationweather.gov); store in a fresh session.
# ---------------------------------------------------------------------------


class TestCollectAndStore:
    def test_fetch_runs_sessionless_and_store_uses_a_fresh_one(self):
        from weatherbrief.tasks import verification

        spy = _SessionSpy()
        seen: dict = {}

        def _fake_fetch(icaos, airports_db_path):
            seen["unclosed"] = spy.unclosed()
            return []

        with (
            patch.object(verification, "SessionLocal", spy),
            patch.object(verification, "finalize_completed_flights", return_value=0),
            patch.object(verification, "_score_completed", return_value=0),
            patch.object(
                verification, "find_verifiable_flights",
                return_value=[SimpleNamespace(id="f1")],
            ),
            patch.object(
                verification, "gather_airports",
                return_value={"EGTK": {"f1"}},
            ) as mock_gather,
            patch.object(verification, "fetch_observations_batch", side_effect=_fake_fetch),
            patch.object(verification, "store_observations", return_value=0) as mock_store,
        ):
            result = verification.collect_and_store("/fake/airports.db")

        assert result["flights"] == 1
        # No session pinned across the network fetch.
        assert seen["unclosed"] == []
        # Reads used the first session; the store phase a fresh second one.
        assert len(spy.sessions) == 2
        read_db, store_db = spy.sessions
        assert mock_gather.call_args.args[1] is read_db
        assert mock_store.call_args.args[2] is store_db
        store_db.commit.assert_called_once()
        assert read_db.close.called and store_db.close.called


# ---------------------------------------------------------------------------
# tasks.time_scan_runner._run_scan_job — reads in one short session,
# run_time_scan (slow GRIB scan) with none held, pack-row update + usage row
# in a fresh one.
# ---------------------------------------------------------------------------


class TestRunScanJob:
    def test_scan_runs_sessionless_and_writes_use_a_fresh_one(self):
        from weatherbrief.api import packs as packs_mod
        from weatherbrief.api import usage as usage_mod
        from weatherbrief.storage import flights as flights_mod
        from weatherbrief.tasks import artifacts as artifacts_mod
        from weatherbrief.tasks import time_scan as time_scan_mod
        from weatherbrief.tasks import time_scan_runner

        spy = _SessionSpy()
        seen: dict = {}
        flight = SimpleNamespace(
            id="f1", user_id="u1", flexibility="same_day",
            alt_departure_time=None,
            departure_time=datetime.now(timezone.utc) + timedelta(days=1),
            profile_id=None,
        )

        def _fake_scan(*args, **kwargs):
            seen["unclosed"] = spy.unclosed()
            return SimpleNamespace(candidates=[])

        with (
            patch("flyfun_common.db.SessionLocal", spy),
            patch.object(flights_mod, "load_flight", return_value=flight),
            patch.object(packs_mod, "_build_route_config", return_value=MagicMock()),
            patch.object(
                packs_mod, "_load_advisory_profile",
                return_value=(["a"], None, {}, MagicMock(), None, None, None,
                              None, MagicMock(), "en", False, []),
            ),
            patch.object(time_scan_runner, "_write_status"),
            patch.object(time_scan_runner, "_reusable_scan", return_value=None),
            patch.object(time_scan_runner, "merge_confirmed"),
            patch.object(time_scan_mod, "run_time_scan", side_effect=_fake_scan) as mock_scan,
            patch.object(artifacts_mod, "load_time_options", return_value=None),
            patch.object(artifacts_mod, "save_time_options"),
            patch.object(usage_mod, "log_api_usage") as mock_usage,
        ):
            time_scan_runner._run_scan_job(
                "f1", Path("/tmp/pack"), datetime.now(timezone.utc), "/fake/db",
            )

        mock_scan.assert_called_once()
        # No session pinned across the scan.
        assert seen["unclosed"] == []
        # Reads used the first session; the usage/pack-row writes a fresh one.
        assert len(spy.sessions) == 2
        read_db, write_db = spy.sessions
        assert mock_usage.call_args.args[0] is write_db
        write_db.commit.assert_called_once()
        assert read_db.close.called and write_db.close.called


# ---------------------------------------------------------------------------
# tasks.refresh_resume.reconcile_one — the run_db session must not stay open
# across asyncio.to_thread(_auto_refresh_one, ...).
# ---------------------------------------------------------------------------


class TestReconcileOne:
    @pytest.mark.asyncio
    async def test_no_session_held_across_the_resumed_refresh(self):
        from weatherbrief import scheduler as scheduler_mod
        from weatherbrief.api import packs as packs_mod
        from weatherbrief.tasks import refresh_resume

        spy = _SessionSpy()
        seen: dict = {}

        def _fake_refresh(flight_id, app_state, user_id, **kwargs):
            seen["unclosed"] = spy.unclosed()
            return True

        registry = MagicMock()
        registry.try_register.return_value = object()

        with (
            patch.object(refresh_resume, "SessionLocal", spy),
            patch.object(
                refresh_resume, "decide_resume",
                return_value=refresh_resume.ResumeDecision("resume", "go"),
            ),
            patch.object(refresh_resume, "_close_orphan"),
            patch.object(packs_mod, "refresh_registry", registry),
            patch.object(scheduler_mod, "_auto_refresh_one", _fake_refresh),
        ):
            await refresh_resume.reconcile_one(1, SimpleNamespace())

        # The job-read and flight-exists sessions were both closed before the
        # resumed refresh ran in its thread.
        assert seen["unclosed"] == []
        assert all(s.close.called for s in spy.sessions)
