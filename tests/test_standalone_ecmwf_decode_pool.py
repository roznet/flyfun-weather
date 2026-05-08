"""Verify ECMWF GRIB decode in standalone_verification dispatches through the
pool (issue #134).

Before the fix, ``fetch_ecmwf_grib_snapshots`` called
``decode_ecmwf_pressure_per_point`` / ``decode_ecmwf_surface_per_point``
directly in the parent process, leaking cfgrib/ECCODES native memory into
uvicorn until the cgroup OOM-killed it. The fix routes both decodes through
``_dispatch_decode`` so the work runs in a recyclable worker.
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


def test_fetch_ecmwf_grib_snapshots_dispatches_decode_to_pool():
    """Both surface and pressure decodes must go through ``_dispatch_decode``.

    Verifying by worker-function name: the in-process path called the local
    ``decode_ecmwf_*_per_point`` functions, which would not be visible as
    dispatched calls.
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

    def fake_dispatch(worker_fn_name: str, *args):
        if worker_fn_name == "decode_ecmwf_surface":
            return (list(surface_payload), [True] * n)
        if worker_fn_name == "decode_ecmwf_pressure":
            return (list(pressure_payload), [False] * n)
        raise AssertionError(f"unexpected worker: {worker_fn_name}")

    with patch(
        "weatherbrief.fetch.grib._dispatch_decode",
        side_effect=fake_dispatch,
    ) as mock_dispatch:
        snaps = fetch_ecmwf_grib_snapshots(
            run_files, airports, sample_hours=[9, 12, 15, 18], days=0,
        )

    assert mock_dispatch.called, "decode must go through the pool dispatcher"

    worker_names = {c.args[0] for c in mock_dispatch.call_args_list}
    assert worker_names == {"decode_ecmwf_surface", "decode_ecmwf_pressure"}, (
        f"expected both worker names dispatched, got {worker_names}"
    )

    # Verify the file path argument is a string (worker functions expect str,
    # not Path — Path is not picklable across spawned interpreters reliably).
    for call in mock_dispatch.call_args_list:
        path_arg = call.args[1]
        assert isinstance(path_arg, str), (
            f"_dispatch_decode args must be picklable strings, got {type(path_arg)}"
        )

    assert len(snaps) > 0, "fetcher should produce snapshots from canned data"
    icaos = {s["icao"] for s in snaps}
    assert icaos == {"LFPG", "EDDF"}


def test_fetch_ecmwf_grib_snapshots_caches_a1_across_steps():
    """``_decode_a1`` caches surface decode results so step-diff reuse doesn't
    re-dispatch the same file. Important because each dispatch is a worker
    round-trip — caching kept the in-process path cheap; the pool path must
    keep the same property.
    """
    init_time = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)
    # Two consecutive steps share the previous-step lookup chain:
    #  - Step 9 reads a1[step=9] and a1[step=8] for tp diff
    #  - Step 12 reads a1[step=12] and a1[step=11]
    # Step 8 and 11 don't exist in the run, so they hit the empty-fallback
    # cache once each. We expect exactly 4 unique a1 dispatches: steps 9, 8 (miss → empty),
    # 12, 11 (miss → empty), but the empty path doesn't dispatch at all.
    # So the actual count is 2 (one per real step that has an a1 file).
    steps = [9, 12]
    run_files = _fake_run_files(init_time, steps)
    airports = _airports()
    n = len(airports)

    a1_calls: list[str] = []
    a2_calls: list[str] = []

    def fake_dispatch(worker_fn_name: str, path: str, *args):
        if worker_fn_name == "decode_ecmwf_surface":
            a1_calls.append(path)
            return ([{"raw_t2m_k": 285.0} for _ in range(n)], [True] * n)
        if worker_fn_name == "decode_ecmwf_pressure":
            a2_calls.append(path)
            return ([{} for _ in range(n)], [False] * n)
        raise AssertionError(worker_fn_name)

    with patch(
        "weatherbrief.fetch.grib._dispatch_decode",
        side_effect=fake_dispatch,
    ):
        fetch_ecmwf_grib_snapshots(
            run_files, airports, sample_hours=[15, 18], days=0,
        )

    # 15Z → step_h=9, 18Z → step_h=12. Each calls _decode_a1(step) and
    # _decode_a1(step-1). Steps 8 and 11 have no a1 file, so they short-circuit
    # to the empty fallback without dispatch. Real a1 dispatches: step 9 and 12.
    assert sorted(a1_calls) == [
        "/fake/a1_step12.grib",
        "/fake/a1_step9.grib",
    ], f"unexpected a1 dispatches: {a1_calls}"

    # Pressure decode runs once per step — no caching.
    assert sorted(a2_calls) == [
        "/fake/a2_step12.grib",
        "/fake/a2_step9.grib",
    ], f"unexpected a2 dispatches: {a2_calls}"
