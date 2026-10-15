"""The one way to open a netCDF4 file in this process (#786).

The netCDF4 wheel vendors its own libhdf5, built without HDF5's thread-safe
option, and netCDF4 itself does not serialise calls.  FastAPI runs our sync
endpoints and the live layer in a threadpool, so two observed-frame reads can
overlap and crash the whole process inside libhdf5 (segfault, no traceback).

``open_netcdf`` holds a process-wide lock for the whole ``with`` block: open,
every read, and close.  Keep the block to materialising numpy arrays; nothing
read from the dataset may be touched after the block exits.

Rules:

- Every ``netCDF4.Dataset`` in ``src/weatherbrief`` goes through here.
- Never enter it from an ``async def``: it blocks, and would block the event
  loop.  All current callers are sync.
- Helpers that take an already-open dataset (``ctth.read_grid``,
  ``ctth._read_raw``, ...) need no lock of their own.  The lock is re-entrant,
  so a nested ``open_netcdf`` on the same thread is harmless.
- netCDF4 only.  h5py (``observed/opera.py``) carries its own HDF5 copy and
  its own global lock; ``opera.iter_row_blocks`` is a generator that streams a
  whole composite, and holding this lock across its yields would stall every
  other read behind a tile render.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import netCDF4

NETCDF_LOCK = threading.RLock()


@contextmanager
def open_netcdf(path: Path | str, mode: str = "r", **kwargs) -> Iterator["netCDF4.Dataset"]:
    """Open ``path`` with netCDF4 under the process-wide lock, closing it on exit."""
    import netCDF4

    with NETCDF_LOCK:
        with netCDF4.Dataset(str(path), mode, **kwargs) as dataset:
            yield dataset
