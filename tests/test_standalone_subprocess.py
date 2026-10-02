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
import os
import signal
import sys
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

    def __init__(self, returncode: int = 0, hang: bool = False, pid: int = 4242):
        self.returncode = None
        self._rc = returncode
        self.pid = pid
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
    """Patch create_subprocess_exec + the process-group syscalls.

    ``captured`` collects the launch (cmd/env/kwargs) and every ``killpg`` the
    supervisor issues as ``(pgid, signal)`` pairs. ``os.getpgid`` is stubbed to
    a group distinct from the test runner's own, so a real ``killpg`` would
    never reach the test process even if one leaked through.
    """
    captured: dict = {"killpg": []}

    async def fake_exec(*cmd, env=None, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = env
        captured["kwargs"] = kwargs
        return proc

    def fake_getpgid(pid: int) -> int:
        captured["getpgid_pid"] = pid
        return pid  # start_new_session ⇒ the child leads its own group

    def fake_killpg(pgid: int, sig: int) -> None:
        captured["killpg"].append((pgid, sig))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(scheduler.os, "getpgid", fake_getpgid)
    monkeypatch.setattr(scheduler.os, "killpg", fake_killpg)
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
    captured = _patch_exec(monkeypatch, proc)

    with patch.object(scheduler, "_ensure_failed_cycle_recorded") as rec:
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=False, score_observations=True,
        )

    assert proc.terminated
    rec.assert_called_once()
    assert "exceeded" in rec.call_args[0][3]
    # The tree, not just the child: SIGTERM alongside the child's terminate(),
    # then the post-exit SIGKILL sweep for anything that ignored it.
    assert captured["killpg"] == [
        (proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL),
    ]


@pytest.mark.asyncio
async def test_cancellation_terminates_child(monkeypatch):
    """App shutdown mid-cycle must signal the child and propagate the cancel."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    proc = FakeProc(hang=True)
    captured = _patch_exec(monkeypatch, proc)

    task = asyncio.ensure_future(_run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.terminated
    # Graceful shutdown: the group gets SIGTERM so pool workers can exit
    # tidily. No SIGKILL sweep — the container teardown collects the tree.
    assert captured["killpg"] == [(proc.pid, signal.SIGTERM)]


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
# Process-group cleanup (issue #451)
#
# The child owns a bounded pool of sounding/decode workers. Those workers
# survive their parent's death — verified below against real processes — so a
# child that is SIGKILLed (the cgroup OOM killer does this on prod, 11 times in
# the 9 days to 2026-07-27) strands them in the same cgroup holding hundreds of
# MB apiece, making the *next* cycle likelier to die the same way.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_launched_in_its_own_session(monkeypatch):
    """Without start_new_session there is no group to signal but the
    supervisor's own — the safety check in _child_pgid then disables cleanup."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc(returncode=0))

    await _run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    )

    assert captured["kwargs"].get("start_new_session") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode", [0, 1, -9])
async def test_any_exit_sweeps_the_process_group(monkeypatch, returncode):
    """Clean exit, crash and OOM kill all leave the same possible orphans."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    proc = FakeProc(returncode=returncode)
    captured = _patch_exec(monkeypatch, proc)

    with patch.object(scheduler, "_ensure_failed_cycle_recorded"):
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=True, score_observations=False,
        )

    assert captured["killpg"] == [(proc.pid, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_group_cleanup_skipped_when_child_shares_our_group(monkeypatch):
    """A child in the supervisor's own group must never be group-signalled —
    that would kill the web app. Refuse and log instead."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    proc = FakeProc(returncode=-9)
    captured = _patch_exec(monkeypatch, proc)
    monkeypatch.setattr(scheduler.os, "getpgid", lambda pid: os.getpgrp())

    with patch.object(scheduler, "_ensure_failed_cycle_recorded") as rec:
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=True, score_observations=False,
        )

    assert captured["killpg"] == []
    rec.assert_called_once()  # the failure is still recorded


@pytest.mark.asyncio
async def test_group_cleanup_tolerates_a_vanished_group(monkeypatch):
    """The common case: nothing was orphaned, so the group is already empty.
    ProcessLookupError must not break the cycle's failure accounting."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc(returncode=-9))

    def empty_group(pgid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(scheduler.os, "killpg", empty_group)

    with patch.object(scheduler, "_ensure_failed_cycle_recorded") as rec:
        await _run_standalone_cycle_supervised(
            _app_state(), fetch_forecasts=True, score_observations=False,
        )

    assert captured["killpg"] == []  # our recorder never ran; no crash either
    rec.assert_called_once()


@pytest.mark.asyncio
async def test_getpgid_failure_disables_cleanup(monkeypatch):
    """Child already reaped between spawn and getpgid: no pgid, no signals."""
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc(returncode=0))

    def gone(pid):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(scheduler.os, "getpgid", gone)

    await _run_standalone_cycle_supervised(
        _app_state(), fetch_forecasts=True, score_observations=False,
    )

    assert captured["killpg"] == []


@pytest.mark.slow
def test_pool_workers_survive_a_killed_parent_and_killpg_reaps_them(tmp_path):
    """The premise, against real processes.

    Spawns a child that owns a ProcessPoolExecutor (one worker busy in a call,
    one idle on the queue), SIGKILLs it as the OOM killer would, and asserts
    both workers are still alive — then that a group SIGKILL is what actually
    ends them. If CPython ever starts reaping workers on parent death, this
    test fails and issue #451's premise is gone.
    """
    import subprocess
    import time

    child_src = tmp_path / "pool_child.py"
    child_src.write_text(
        "import multiprocessing as mp, time\n"
        "from concurrent.futures import ProcessPoolExecutor\n"
        "def busy(n):\n"
        "    t = time.monotonic()\n"
        "    while time.monotonic() - t < n:\n"
        "        pass\n"
        "    return n\n"
        "if __name__ == '__main__':\n"
        "    ex = ProcessPoolExecutor(max_workers=2, mp_context=mp.get_context('spawn'))\n"
        "    ex.submit(busy, 120)\n"
        "    ex.submit(busy, 0.1)\n"
        "    time.sleep(4)\n"
        "    print(' '.join(str(p) for p in ex._processes), flush=True)\n"
        "    time.sleep(120)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(child_src)], stdout=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    workers: list[int] = []
    try:
        workers = [int(p) for p in proc.stdout.readline().split()]
        assert len(workers) == 2
        pgid = os.getpgid(proc.pid)

        os.kill(proc.pid, signal.SIGKILL)  # what the cgroup OOM killer does
        proc.wait()

        time.sleep(1.0)
        alive = [w for w in workers if _pid_alive(w)]
        assert alive == workers, (
            "premise broken: pool workers no longer outlive a SIGKILLed parent"
        )

        scheduler._killpg(pgid, signal.SIGKILL, "test")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(_pid_alive(w) for w in workers):
            time.sleep(0.1)
        assert not [w for w in workers if _pid_alive(w)], "group kill left survivors"
    finally:
        proc.stdout.close()
        for pid in (proc.pid, *workers):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, not ours
        return True
    return True


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

    def fake_cycle(watchlist, db, *, fetch_forecasts, score_observations,
                   pool_soundings=False, region="eu"):
        calls["flags"] = (fetch_forecasts, score_observations)
        calls["pool_soundings"] = pool_soundings
        calls["region"] = region
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
        emit_artifact=None, region=None,
    )
    calls = _run_cli_standalone(monkeypatch, args)
    assert calls["flags"] == (True, False)
    # The CLI owns a disposable process → pooled soundings on (#450 review);
    # the scheduler's in-process fallback keeps the default False.
    assert calls["pool_soundings"] is True
    calls["post"].assert_called_once_with("/tmp/airports.db", "forecast")


def test_cli_light_maps_to_score_no_fetch(monkeypatch):
    args = SimpleNamespace(
        light=True, forecast_only=False, with_rollup=False, background=False,
        emit_artifact=None, region=None,
    )
    calls = _run_cli_standalone(monkeypatch, args)
    assert calls["flags"] == (False, True)
    calls["post"].assert_not_called()


def test_cli_region_us_errors_before_running(monkeypatch):
    """--region us must fail fast (US not onboarded) rather than store EU data
    mislabeled as us; it exits before the cycle runs."""
    args = SimpleNamespace(
        light=False, forecast_only=True, with_rollup=False, background=False,
        region="us", emit_artifact=None,
    )
    with pytest.raises(SystemExit):
        _run_cli_standalone(monkeypatch, args)


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


# ---------------------------------------------------------------------------
# Observed-conditions archive (#575) riding the METAR ingest tick
# ---------------------------------------------------------------------------


def _enable_observed_archive(monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    monkeypatch.setenv("WB_OBSERVED_ARCHIVE_ENABLED", "1")


def test_observed_archive_is_off_by_default(monkeypatch):
    monkeypatch.delenv("WB_OBSERVED_ARCHIVE_ENABLED", raising=False)
    monkeypatch.setenv("WB_OBSERVED_ENABLED", "1")
    captured = _patch_exec(monkeypatch, FakeProc())
    asyncio.run(scheduler._run_observed_archive_after_ingest(_app_state()))
    assert "cmd" not in captured  # never launched


def test_observed_archive_needs_the_collector(monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_ARCHIVE_ENABLED", "1")
    monkeypatch.delenv("WB_OBSERVED_ENABLED", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc())
    asyncio.run(scheduler._run_observed_archive_after_ingest(_app_state()))
    assert "cmd" not in captured  # never launched


def test_observed_archive_runs_the_cli_in_a_child(monkeypatch):
    _enable_observed_archive(monkeypatch)
    monkeypatch.delenv("STANDALONE_SUBPROCESS", raising=False)
    captured = _patch_exec(monkeypatch, FakeProc(returncode=0))
    asyncio.run(scheduler._run_observed_archive_after_ingest(_app_state()))
    assert captured["cmd"][1:] == [
        "-m", "weatherbrief.verify", "observed-archive", "run", "--background",
    ]


def test_observed_archive_failure_never_reaches_the_metar_loop(monkeypatch):
    """A failing archive must not push METAR ingest into its error back-off."""
    _enable_observed_archive(monkeypatch)

    async def boom(*_a, **_k):
        raise OSError("cannot spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    asyncio.run(scheduler._run_observed_archive_after_ingest(_app_state()))


def test_observed_archive_child_is_killed_on_timeout(monkeypatch):
    _enable_observed_archive(monkeypatch)
    proc = FakeProc(hang=True)
    _patch_exec(monkeypatch, proc)
    monkeypatch.setattr(scheduler, "_OBSERVED_ARCHIVE_TIMEOUT_S", 0.01)
    asyncio.run(scheduler._run_observed_archive_after_ingest(_app_state()))
    assert proc.terminated


def test_observed_archive_runs_beside_ingest_one_at_a_time(monkeypatch):
    """A slow archive must neither block the ingest tick nor run twice."""
    started = []
    release = None

    async def fake_run(_app_state):
        started.append(1)
        await release.wait()

    monkeypatch.setattr(scheduler, "_run_observed_archive_after_ingest", fake_run)

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        first = scheduler._start_observed_archive(_app_state(), None)
        await asyncio.sleep(0)
        second = scheduler._start_observed_archive(_app_state(), first)
        assert second is first and len(started) == 1
        release.set()
        await first
        third = scheduler._start_observed_archive(_app_state(), first)
        await third
        assert third is not first and len(started) == 2

    asyncio.run(scenario())
