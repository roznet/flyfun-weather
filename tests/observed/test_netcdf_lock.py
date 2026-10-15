"""netCDF4 access is serialised process-wide (#786).

The netCDF4 wheel's vendored libhdf5 is not thread-safe; overlapping reads
from the FastAPI threadpool segfaulted uvicorn in prod.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from weatherbrief.observed import ctth, lightning
from weatherbrief.observed.grid import GridWindow
from weatherbrief.observed.netcdf_io import NETCDF_LOCK, open_netcdf

SRC = Path(__file__).resolve().parents[2] / "src" / "weatherbrief"


def _other_thread_can_acquire() -> bool:
    result: list[bool] = []

    def probe():
        got = NETCDF_LOCK.acquire(blocking=False)
        if got:
            NETCDF_LOCK.release()
        result.append(got)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return result[0]


def test_lock_covers_the_whole_with_block(ctth_path):
    assert _other_thread_can_acquire()
    with open_netcdf(ctth_path) as dataset:
        assert not _other_thread_can_acquire()
        ctth.read_grid(dataset)
        assert not _other_thread_can_acquire()
    assert _other_thread_can_acquire()


def test_lock_is_released_when_the_block_raises(ctth_path):
    try:
        with open_netcdf(ctth_path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert _other_thread_can_acquire()


def test_nested_open_on_the_same_thread_does_not_deadlock(ctth_path, li_path):
    with open_netcdf(ctth_path):
        # read_metadata opens its own dataset under the same (re-entrant) lock.
        meta = lightning.read_metadata(li_path)
    assert meta["quantity"] == "flash"


def test_concurrent_reads_from_many_threads(ctth_path, li_path):
    window = GridWindow(2, 20, 5, 9, full_width=True)
    expected_ctth = ctth.read_window(ctth_path, window, source="eumetsat_ctth")
    expected_li = lightning.read_flashes(li_path, source="eumetsat_li", window_minutes=10.0)

    def work(i: int):
        if i % 2:
            frame = ctth.read_window(ctth_path, window, source="eumetsat_ctth")
            return np.array_equal(frame.values, expected_ctth.values, equal_nan=True)
        frame = lightning.read_flashes(li_path, source="eumetsat_li", window_minutes=10.0)
        return np.array_equal(frame.lats, expected_li.lats)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(work, range(400)))
    assert all(results)


def test_every_netcdf4_open_goes_through_the_helper():
    # Aliased imports would let a raw Dataset open slip past a dotted-name match.
    pattern = re.compile(
        r"netCDF4\.(MF)?Dataset\(|from netCDF4 import|import netCDF4 as"
    )
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.name != "netcdf_io.py" and pattern.search(path.read_text())
    ]
    assert offenders == []
