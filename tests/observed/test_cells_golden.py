"""Golden catalogue on real frames — macOS and Linux must agree.

OPERA and EUMETSAT frames cannot be redistributed from this repository, so the
fixture lives outside it and the test skips when it is absent (same pattern
as ``test_collect_live.py``).  Layout of ``$WB_CELLS_GOLDEN_DIR``::

    <source>/<stamp>.{h5,nc,json}          a short run of archived frames
    expected/cells/catalogues/<day>/<stamp>.json.gz

Build or refresh the expected side from a machine you trust (it replays the
frames under the current policy):

    WB_CELLS_GOLDEN_DIR=~/cells-golden WB_CELLS_GOLDEN_WRITE=1 pytest tests/observed/test_cells_golden.py

Comparison is with tolerances, not bytes: numpy/scipy link Accelerate on
macOS and OpenBLAS on Linux, and the FFT round-off may differ in the last
digits.  What must agree exactly is the *structure* — cells per tier, ids,
lineage events, motion status.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbrief.observed.cells import DEFAULT_POLICY
from weatherbrief.observed.cells.catalogue import read_catalogue
from weatherbrief.observed.cells.runner import replay
from weatherbrief.observed.frames import parse_frame_stamp

GOLDEN = os.environ.get("WB_CELLS_GOLDEN_DIR", "").strip()
pytestmark = pytest.mark.skipif(not GOLDEN, reason="set WB_CELLS_GOLDEN_DIR to run the golden catalogue")

SOURCES = ("opera_dbzh", "opera_rate", "eumetsat_li")


def _range(root: Path) -> tuple[datetime, datetime]:
    stamps = sorted(p.stem for p in (root / "opera_dbzh").glob("*.h5"))
    return parse_frame_stamp(stamps[0]), parse_frame_stamp(stamps[-1])


def _close(a, b, rel=0.01, abs_=1e-3):
    if a is None or b is None:
        return a is b
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def test_replay_matches_the_golden_catalogues(tmp_path):
    root = Path(GOLDEN).expanduser()
    start, end = _range(root)
    sources = tuple(s for s in SOURCES if (root / s).is_dir())
    replay(root, tmp_path / "out", start, end, DEFAULT_POLICY, sources=sources)
    got_dir = tmp_path / "out" / "cells" / "catalogues"

    if os.environ.get("WB_CELLS_GOLDEN_WRITE") in ("1", "true", "yes"):
        target = root / "expected" / "cells" / "catalogues"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(got_dir, target)
        pytest.skip(f"wrote golden catalogues to {target}")

    expected_files = sorted((root / "expected" / "cells" / "catalogues").rglob("*.json.gz"))
    assert expected_files, "no expected catalogues — run once with WB_CELLS_GOLDEN_WRITE=1"
    for path in expected_files:
        want = read_catalogue(path)
        got = read_catalogue(got_dir / path.parent.name / path.name)
        assert got is not None, path.name
        if want["policy_version"] != got["policy_version"]:
            pytest.skip("policy changed since the golden catalogues were written — refresh them")
        want_cells = {c["id"]: c for c in want["cells"]}
        got_cells = {c["id"]: c for c in got["cells"]}
        assert set(got_cells) == set(want_cells), path.name
        for cid, w in want_cells.items():
            g = got_cells[cid]
            assert g["lineage"]["event"] == w["lineage"]["event"], cid
            assert g["motion"]["status"] == w["motion"]["status"], cid
            assert _close(g["lat"], w["lat"], abs_=1e-3) and _close(g["lon"], w["lon"], abs_=1e-3), cid
            assert _close(g["area_km2"], w["area_km2"]), cid
            assert _close(g["motion"]["speed_kt"], w["motion"]["speed_kt"], abs_=0.5), cid
            assert g["flashes"] == w["flashes"], cid
            assert g["trend"]["state"] == w["trend"]["state"], cid


def test_golden_dir_layout_is_usable():
    root = Path(GOLDEN).expanduser()
    assert (root / "opera_dbzh").is_dir()
    start, end = _range(root)
    assert start <= end and start.tzinfo == timezone.utc
