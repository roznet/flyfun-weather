"""Verify ECMWF GRIB decode in standalone_verification dispatches through the
pool (issue #134).

Before the fix, ``fetch_ecmwf_grib_snapshots`` called
``decode_ecmwf_pressure_per_point`` / ``decode_ecmwf_surface_per_point``
directly in the parent process, leaking cfgrib/ECCODES native memory into
uvicorn until the cgroup OOM-killed it. The fix routes both decodes through
the shared pool so the work runs in a recyclable worker.

Since #459 the whole run's a1/a2 decodes are collected into a single
``_dispatch_decode_parallel`` batch (real pool fan-out) rather than walking one
blocking ``_dispatch_decode`` per step, so these tests patch the batched
primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from weatherbrief.tasks.airport_watchlist import WatchlistAirport
from weatherbrief.tasks.standalone_verification import fetch_ecmwf_grib_snapshots


@dataclass
class _FakeECMWFFile:
    """Stand-in for ``ecmwf_fetch.ECMWFFileInfo`` — only the fields read by
    ``fetch_ecmwf_grib_snapshots`` are populated."""

    path: Path
    base_time: datetime
    step_hours: int
    feed: str  # "a1" (surface) or "a2" (pressure)

    @property
    def is_pressure_level(self) -> bool:
        return self.feed == "a2"

    @property
    def is_surface(self) -> bool:
        return self.feed == "a1"


def _airports() -> list[WatchlistAirport]:
    return [
        WatchlistAirport(icao="LFPG", lat=49.0097, lon=2.5479),
        WatchlistAirport(icao="EDDF", lat=50.0379, lon=8.5622),
    ]


def _fake_run_files(init_time: datetime, steps: list[int]) -> list[_FakeECMWFFile]:
    """One a1 + one a2 file per step."""
    files: list[_FakeECMWFFile] = []
    for s in steps:
        files.append(
            _FakeECMWFFile(
                path=Path(f"/fake/a1_step{s}.grib"),
                base_time=init_time, step_hours=s, feed="a1",
            ),
        )
        files.append(
            _FakeECMWFFile(
                path=Path(f"/fake/a2_step{s}.grib"),
                base_time=init_time, step_hours=s, feed="a2",
            ),
        )
    return files


def _fake_parallel_factory(surface_payload, pressure_payload, n, a1_calls=None, a2_calls=None):
    """Build a stand-in for ``_dispatch_decode_parallel`` that returns one
    result per job (surface/pressure), optionally recording decoded paths."""

    def fake_parallel(jobs, *, priority=None, return_exceptions=False, max_inflight=None):
        results = []
        for name, args in jobs:
            path = args[0]
            if name == "decode_ecmwf_surface":
                if a1_calls is not None:
                    a1_calls.append(path)
                results.append((list(surface_payload), [True] * n))
            elif name == "decode_ecmwf_pressure":
                if a2_calls is not None:
                    a2_calls.append(path)
                results.append((list(pressure_payload), [False] * n))
            else:
                raise AssertionError(f"unexpected worker: {name}")
        return results

    return fake_parallel


def test_fetch_ecmwf_grib_snapshots_dispatches_decode_to_pool():
    """Both surface and pressure decodes must be fanned out through
    ``_dispatch_decode_parallel``.

    Verifying by worker-function name: the in-process path called the local
    ``decode_ecmwf_*_per_point`` functions, which would not be visible as
    dispatched jobs.
    """
    init_time = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    # Steps land on 09Z, 12Z, 15Z, 18Z within day 0 — 4 steps in SAMPLE_HOURS_UTC.
    steps = [3, 6, 9, 12]
    run_files = _fake_run_files(init_time, steps)
    airports = _airports()
    n = len(airports)

    surface_payload: list[dict[str, float]] = [
        {
            "raw_t2m_k": 285.0, "raw_d2m_k": 280.0,
            "raw_u10_ms": 5.0, "raw_v10_ms": 0.0,
            "raw_tp_m": 0.0,
        }
        for _ in range(n)
    ]
    pressure_payload: list[dict[int, dict[str, float]]] = [{} for _ in range(n)]

    fake_parallel = _fake_parallel_factory(surface_payload, pressure_payload, n)

    with patch(
        "weatherbrief.fetch.grib._dispatch_decode_parallel",
        side_effect=fake_parallel,
    ) as mock_dispatch:
        snaps = fetch_ecmwf_grib_snapshots(
            run_files, airports, sample_hours=[9, 12, 15, 18], days=0,
        )

    assert mock_dispatch.called, "decode must go through the pool dispatcher"

    # A single batched call carries every a1/a2 job.
    jobs = mock_dispatch.call_args_list[0].args[0]
    worker_names = {name for name, _ in jobs}
    assert worker_names == {"decode_ecmwf_surface", "decode_ecmwf_pressure"}, (
        f"expected both worker names dispatched, got {worker_names}"
    )

    # Verify the file path argument is a string (worker functions expect str,
    # not Path — Path is not picklable across spawned interpreters reliably).
    for name, args in jobs:
        path_arg = args[0]
        assert isinstance(path_arg, str), (
            f"dispatch args must be picklable strings, got {type(path_arg)}"
        )

    assert len(snaps) > 0, "fetcher should produce snapshots from canned data"
    icaos = {s["icao"] for s in snaps}
    assert icaos == {"LFPG", "EDDF"}


def test_fetch_ecmwf_grib_snapshots_decodes_each_file_once():
    """Each a1/a2 file is decoded exactly once per run, even when a later
    step's accumulation diff reads an earlier step's surface file.

    The batched primitive dedups steps into a set before building the jobs
    list, so the step-diff reuse must not re-decode the shared file — the same
    property the old per-step ``_decode_a1`` cache guaranteed.
    """
    init_time = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    # 15Z → step_h=9, 18Z → step_h=12. Step 12's tp/sf diff reads the previous
    # delivered step (9), so a1_step9 is referenced by both steps but must be
    # decoded only once. Steps 8/11 have no file → no decode.
    steps = [9, 12]
    run_files = _fake_run_files(init_time, steps)
    airports = _airports()
    n = len(airports)

    a1_calls: list[str] = []
    a2_calls: list[str] = []

    fake_parallel = _fake_parallel_factory(
        [{"raw_t2m_k": 285.0} for _ in range(n)],
        [{} for _ in range(n)],
        n, a1_calls=a1_calls, a2_calls=a2_calls,
    )

    with patch(
        "weatherbrief.fetch.grib._dispatch_decode_parallel",
        side_effect=fake_parallel,
    ):
        fetch_ecmwf_grib_snapshots(
            run_files, airports, sample_hours=[15, 18], days=0,
        )

    assert sorted(a1_calls) == [
        "/fake/a1_step12.grib",
        "/fake/a1_step9.grib",
    ], f"unexpected a1 decodes (each file should decode once): {a1_calls}"

    assert sorted(a2_calls) == [
        "/fake/a2_step12.grib",
        "/fake/a2_step9.grib",
    ], f"unexpected a2 decodes: {a2_calls}"


def test_fetch_ecmwf_grib_snapshots_honours_worker_override(monkeypatch):
    """``GRIB_DECODE_WORKERS_ECMWF`` flows to the batch as ``max_inflight``;
    unset leaves it ``None`` (full pool)."""
    init_time = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    steps = [3, 6, 9, 12]
    run_files = _fake_run_files(init_time, steps)
    airports = _airports()
    n = len(airports)

    fake_parallel = _fake_parallel_factory(
        [{"raw_t2m_k": 285.0} for _ in range(n)], [{} for _ in range(n)], n,
    )

    # Unset → None
    monkeypatch.delenv("GRIB_DECODE_WORKERS_ECMWF", raising=False)
    with patch(
        "weatherbrief.fetch.grib._dispatch_decode_parallel",
        side_effect=fake_parallel,
    ) as mock_dispatch:
        fetch_ecmwf_grib_snapshots(
            run_files, airports, sample_hours=[9, 12, 15, 18], days=0,
        )
    assert mock_dispatch.call_args_list[0].kwargs["max_inflight"] is None

    # Set → int window
    monkeypatch.setenv("GRIB_DECODE_WORKERS_ECMWF", "2")
    with patch(
        "weatherbrief.fetch.grib._dispatch_decode_parallel",
        side_effect=fake_parallel,
    ) as mock_dispatch:
        fetch_ecmwf_grib_snapshots(
            run_files, airports, sample_hours=[9, 12, 15, 18], days=0,
        )
    assert mock_dispatch.call_args_list[0].kwargs["max_inflight"] == 2
