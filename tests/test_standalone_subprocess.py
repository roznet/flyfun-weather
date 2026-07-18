"""Tests for subprocess isolation of standalone cycles (issue #236).

The scheduler runs standalone forecast/verification cycles in a short-lived
child process so the cycle's transient heap peak is returned to the OS on
exit instead of ratcheting the uvicorn parent's anon working set. These
tests cover the supervisor: command construction, the rollback switch, and
failure recording for children that die without writing their own cycle row
(SIGKILL/timeout never reach the in-cycle exception path).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

import weatherbrief.scheduler as scheduler
from weatherbrief.db.models import VerificationCycleRow
from weatherbrief.scheduler import (
    _ensure_failed_cycle_recorded,
    _run_standalone_cycle_supervised,
    _standalone_subprocess_enabled,
)


class FakeProc:
    """Stand-in for asyncio.subprocess.Process."""

    def __init__(self, returncode: int = 0, hang: bool = False):
        self.returncode = None
        self._rc = returncode
        self.terminated = False
        self.killed = False
        self._done = asyncio.Event()
        if not hang:
            self._done.set()

    async def wait(self):
        await self._done.wait()
        self.returncode = self._rc
        return self._rc

    def terminate(self):
        self.terminated = True
        self._rc = -15
        self._done.set()

    def kill(self):
        self.killed = True
        self._rc = -9
        self._done.set()


def _app_state(db_path: str = "/tmp/airports.db"):
    return SimpleNamespace(db_path=db_path)


def _patch_exec(monkeypatch, proc: FakeProc) -> dict:
    """Patch asyncio.create_subprocess_exec, capturing cmd and env."""
    captured: dict = {}

    async def fake_exec(*cmd, env=None, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = env
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


# ---------------------------------------------------------------------------
# Rollback switch
# ---------------------------------------------------------------------------


def test_subprocess_enabled_by_default(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    assert _standalone_subprocess_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", " 0 "])
def test_subprocess_disabled_via_env(monkeypatch, value):
    monkeypatch.setenv("STANDALONE_SUBPROCESS", value)
    assert _standalone_subprocess_enabled() is False


@pytest.mark.asyncio
async def test_fallback_runs_in_process(monkeypatch):
    """STANDALONE_SUBPROCESS=0 reverts to the in-process thread path."""
    monkeypatch.setenv("STANDALONE_SUBPROCESS", "0")
    calls = []

    def fake_once(app_state, fetch, score):
        calls.append((fetch, score))

    monkeypatch.setattr(scheduler, "_run_standalone_once", fake_once)

    async def fail_exec(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("subprocess must not be spawned when disabled")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_exec)

    await _run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    )
    assert calls == [(True, False)]


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forecast_cycle_command(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc(returncode=0))

    await _run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    )

    cmd = captured["cmd"]
    assert cmd[1:4] == ["-m", "weatherbrief.verify", "standalone"]
    assert "--forecast-only" in cmd
    assert "--light" not in cmd
    assert "--with-rollup" in cmd
    assert "--background" in cmd
    # The child runs its own bounded pool for sounding batches + decode
    # (#448 PR B): default 2 workers, overridable via STANDALONE_ANALYSIS_WORKERS.
    assert captured["env"]["GRIB_DECODE_WORKERS"] == "2"


@pytest.mark.asyncio
async def test_analysis_workers_env_override(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    monkeypatch.setenv("STANDALONE_ANALYSIS_WORKERS", "0")
    captured = _patch_exec(monkeypatch, FakeProc(returncode=0))

    await _run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    )

    # 0 restores the pre-#448 inline behaviour (rollback switch).
    assert captured["env"]["GRIB_DECODE_WORKERS"] == "0"


@pytest.mark.asyncio
async def test_light_cycle_command(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc(returncode=0))

    await _run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=False, score_observations=True,
    )

    assert "--light" in captured["cmd"]
    assert "--forecast-only" not in captured["cmd"]


@pytest.mark.asyncio
async def test_no_db_path_skips_spawn(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)

    async def fail_exec(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("must not spawn without AIRPORTS_DB")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_exec)
    await _run_standalone_cycle_supervised(
        _app_state(db_path=""), fetch_forecasts=True, score_observations=False,
    )


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_records_no_failure(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    _patch_exec(monkeypatch, FakeProc(returncode=0))

    with patch.object(scheduler, "_ensure_failed_cycle_recorded") as rec:
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=True, score_observations=False,
        )
    rec.assert_not_called()


@pytest.mark.asyncio
async def test_nonzero_exit_records_failure(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    _patch_exec(monkeypatch, FakeProc(returncode=1))

    with patch.object(scheduler, "_ensure_failed_cycle_recorded") as rec:
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=True, score_observations=False,
        )
    rec.assert_called_once()
    cycle_type, _launched_at, _t_start, error_message = rec.call_args[0]
    assert cycle_type == "forecast"
    assert "exited with code 1" in error_message


@pytest.mark.asyncio
async def test_timeout_kills_child_and_records_failure(monkeypatch):
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    monkeypatch.setattr(scheduler, "_STANDALONE_SUBPROCESS_TIMEOUT_S", 0.05)
    proc = FakeProc(hang=True)
    _patch_exec(monkeypatch, proc)

    with patch.object(scheduler, "_ensure_failed_cycle_recorded") as rec:
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=False, score_observations=True,
        )

    assert proc.terminated
    rec.assert_called_once()
    assert "exceeded" in rec.call_args[0][3]


@pytest.mark.asyncio
async def test_cancellation_terminates_child(monkeypatch):
    """App shutdown mid-cycle must signal the child and propagate the cancel."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    proc = FakeProc(hang=True)
    _patch_exec(monkeypatch, proc)

    task = asyncio.ensure_future(_run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.terminated


@pytest.mark.asyncio
async def test_cancellation_during_timeout_grace_kills_child(monkeypatch):
    """Cancellation arriving while the TimeoutError handler waits out the
    SIGTERM grace period escapes that handler (a sibling `except
    CancelledError` only matches the same try once) — the supervisor must
    still escalate to SIGKILL synchronously and propagate the cancel."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    monkeypatch.setattr(scheduler, "_STANDALONE_SUBPROCESS_TIMEOUT_S", 0.05)

    class StubbornProc(FakeProc):
        def terminate(self):
            self.terminated = True  # ignores SIGTERM — never completes wait()

    proc = StubbornProc(hang=True)
    _patch_exec(monkeypatch, proc)

    task = asyncio.ensure_future(_run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    ))
    # Let the supervisor hit the timeout and enter the SIGTERM grace wait.
    await asyncio.sleep(0.2)
    assert proc.terminated
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed


# ---------------------------------------------------------------------------
# _ensure_failed_cycle_recorded — dedup against child-written rows
# ---------------------------------------------------------------------------


def _seed_cycle_row(session, source: str, started_at: datetime) -> None:
    session.add(VerificationCycleRow(
        started_at=started_at,
        duration_ms=1000,
        source=source,
        airports=0,
        observations_stored=0,
        scored=0,
    ))
    session.commit()


def test_failed_cycle_recorded_when_no_child_row(db_engine, monkeypatch):
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=db_engine))
    launched_at = datetime.now(timezone.utc)

    with patch(
        "weatherbrief.tasks.standalone_verification._record_failed_cycle"
    ) as rec:
        _ensure_failed_cycle_recorded(
            "forecast", launched_at, 0.0, "subprocess exited with code -9",
        )
    rec.assert_called_once()
    assert rec.call_args.kwargs["error_message"] == "subprocess exited with code -9"


def test_failed_cycle_not_duplicated_when_child_recorded(db_engine, monkeypatch):
    """A child that failed via its own exception path already wrote a cycle
    row — the parent must not add a second one."""
    session_factory = sessionmaker(bind=db_engine)
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)
    launched_at = datetime.now(timezone.utc)

    session = session_factory()
    try:
        _seed_cycle_row(
            session, "standalone_forecast",
            launched_at + timedelta(seconds=5),
        )

        with patch(
            "weatherbrief.tasks.standalone_verification._record_failed_cycle"
        ) as rec:
            _ensure_failed_cycle_recorded(
                "forecast", launched_at, 0.0, "subprocess exited with code 1",
            )
        rec.assert_not_called()
    finally:
        session.query(VerificationCycleRow).delete()
        session.commit()
        session.close()


# ---------------------------------------------------------------------------
# CLI flag wiring — the subprocess command must reproduce the scheduler's
# in-process flag combinations exactly
# ---------------------------------------------------------------------------


def _run_cli_standalone(monkeypatch, args):
    import weatherbrief.verify.__main__ as vmain

    monkeypatch.setenv("AIRPORTS_DB", "/tmp/airports.db")
    monkeypatch.setattr(vmain, "_init_db", lambda: None)
    monkeypatch.setattr(vmain, "load_dotenv", lambda: None)

    calls: dict = {}

    def fake_cycle(watchlist, db, *, fetch_forecasts, score_observations):
        calls["flags"] = (fetch_forecasts, score_observations)
        return {
            "cycle_type": "forecast" if not score_observations else "light",
            "models_fetched": 0, "snapshots_stored": 0,
            "observations_stored": 0, "scores_created": 0,
            "pruned": 0, "duration_ms": 1,
        }

    with patch(
        "weatherbrief.tasks.standalone_verification.run_standalone_cycle",
        new=fake_cycle,
    ), patch(
        "weatherbrief.tasks.standalone_verification.run_post_cycle_tasks"
    ) as post, patch(
        "weatherbrief.tasks.airport_watchlist.load_watchlist_with_coords",
        return_value=[object()],
    ), patch(
        "weatherbrief.tasks.airport_watchlist.get_configs_dir",
        return_value=".",
    ):
        vmain.cmd_standalone(args)
    calls["post"] = post
    return calls


def test_cli_forecast_only_maps_to_fetch_no_score(monkeypatch):
    args = SimpleNamespace(
        light=False, forecast_only=True, with_rollup=True, background=False,
    )
    calls = _run_cli_standalone(monkeypatch, args)
    assert calls["flags"] == (True, False)
    calls["post"].assert_called_once_with("/tmp/airports.db", "forecast")


def test_cli_light_maps_to_score_no_fetch(monkeypatch):
    args = SimpleNamespace(
        light=True, forecast_only=False, with_rollup=False, background=False,
    )
    calls = _run_cli_standalone(monkeypatch, args)
    assert calls["flags"] == (False, True)
    calls["post"].assert_not_called()


def test_failed_cycle_ignores_rows_from_before_launch(db_engine, monkeypatch):
    """Old cycle rows (previous fires) must not mask a missing row for the
    current launch."""
    session_factory = sessionmaker(bind=db_engine)
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)
    launched_at = datetime.now(timezone.utc)

    session = session_factory()
    try:
        _seed_cycle_row(
            session, "standalone_forecast",
            launched_at - timedelta(hours=12),
        )

        with patch(
            "weatherbrief.tasks.standalone_verification._record_failed_cycle"
        ) as rec:
            _ensure_failed_cycle_recorded(
                "forecast", launched_at, 0.0, "subprocess exited with code 1",
            )
        rec.assert_called_once()
    finally:
        session.query(VerificationCycleRow).delete()
        session.commit()
        session.close()
