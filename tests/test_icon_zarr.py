"""ICON-EU model levels cached as zarr (#705).

The zarr path must read exactly what the direct eccodes decoder reads from the
GRIB it was converted from: same levels, same None pattern, bit-identical
floats. Every test here decodes both and compares, on synthetic blobs from
``grib_synth`` (the same writer ``test_grib_reader.py`` pins the GRIB path
with), so the frozen ICON fixtures there carry over to this path.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("eccodes")
pytest.importorskip("zarr")

from weatherbrief.fetch.grib import decode as dec  # noqa: E402
from weatherbrief.fetch.grib import icon_zarr  # noqa: E402
from weatherbrief.fetch.grib.cache import is_store_cached  # noqa: E402

from grib_synth import write as _write  # noqa: E402

ICON_EU = dict(lat_first=29.5, lat_last=70.5, lon_first=-23.5, lon_last=62.5)
ICON_D2 = dict(lat_first=43.2, lat_last=58.0, lon_first=356.0, lon_last=20.3)
LEVELS = list(range(30, 41))


def _blob_file(path: Path, short: str, levels=LEVELS, *, base=270.0, amp=10.0,
               area=ICON_EU, step=0.5, j_positive=True, mask=None) -> Path:
    with open(path, "ab") as f:
        for li, lev in enumerate(levels):
            _write(
                f, sample="regular_ll_sfc_grib2", short=short,
                type_of_level="generalVerticalLayer", level=lev, area=area,
                step=step, j_positive=j_positive, base=base, amp=amp, seed=li,
                mask=mask,
            )
    return path


def _targets() -> tuple[list[float], list[float]]:
    rng = np.random.default_rng(7)
    lats = list(rng.uniform(29.6, 70.4, 60))
    lons = list(rng.uniform(-23.4, 62.4, 60))
    fixed = [
        (48.0, 11.5), (50.0, 0.0), (51.75, -0.25),   # on rows / columns
        (29.5, 5.0), (70.5, 5.0), (45.0, -23.5), (45.0, 62.5),  # grid edges
        (20.0, 5.0), (75.0, 10.0), (45.0, 80.0),      # outside
    ]
    return lats + [a for a, _ in fixed], lons + [b for _, b in fixed]


def _assert_identical(got: dict, want: dict) -> None:
    assert sorted(got) == sorted(want)
    for lev in want:
        assert got[lev] == want[lev], f"level {lev} differs"  # exact floats


def _convert(tmp_path: Path, grib: Path) -> Path:
    store = tmp_path / icon_zarr.store_name(grib.name)
    assert icon_zarr.convert_grib_to_store(grib, store)
    return store


@pytest.mark.parametrize("tile", [64, 7])  # 7: many tiles, ragged edges
def test_zarr_read_is_bit_identical_to_direct_decode(tmp_path, monkeypatch, tile):
    monkeypatch.setattr(icon_zarr, "TILE", tile)
    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)
    lats, lons = _targets()

    want = dec._decode_icon_eu_single_var(grib.read_bytes(), lats, lons)
    got = dec._decode_icon_eu_single_var(store, lats, lons)

    _assert_identical(got, want)
    assert sum(v is None for v in got[35]) == 3  # exactly the outside points


def test_masked_cells_and_scan_order_match(tmp_path):
    """Bitmap-masked cells stay NaN → None, north-to-south scan is undone."""
    def band(la, lo):
        return (la > 47.0) & (la < 49.0)

    north_first = dict(ICON_EU, lat_first=70.5, lat_last=29.5)
    grib = _blob_file(tmp_path / "f003_ICON_EU_CLC.grib2", "t",
                      area=north_first, j_positive=False, mask=band)
    store = _convert(tmp_path, grib)
    lats = [48.0, 46.9, 49.0, 47.0, 52.0]
    lons = [10.0, 10.0, 10.0, 10.0, 10.0]

    want = dec._decode_icon_eu_single_var(grib.read_bytes(), lats, lons)
    got = dec._decode_icon_eu_single_var(store, lats, lons)

    _assert_identical(got, want)
    assert got[33][0] is None and got[33][4] is not None


def test_grid_written_in_0_360_matches(tmp_path):
    """Targets west of Greenwich align to the stored axis as on the GRIB path."""
    grib = _blob_file(tmp_path / "f003_X_T.grib2", "t", levels=[40, 41, 42],
                      area=ICON_D2, step=0.1)
    store = _convert(tmp_path, grib)
    lats, lons = [50.0, 48.35, 52.5, 47.0, 55.1], [8.5, -3.5, 13.4, -0.13, 20.0]

    want = dec._decode_icon_eu_single_var(grib.read_bytes(), lats, lons)
    got = dec._decode_icon_eu_single_var(store, lats, lons)

    _assert_identical(got, want)
    assert all(v is not None for v in got[41])


def test_chunked_decode_from_stores_matches_grib(tmp_path):
    """End to end: the per-point chunked decoder fed stores == fed GRIB bytes
    (the bytes path is pinned by ``icon_chunked.json`` in test_grib_reader)."""
    blobs = {
        "p": _blob_file(tmp_path / "p.grib2", "pres", base=60000.0, amp=500.0),
        "t": _blob_file(tmp_path / "t.grib2", "t", base=270.0, amp=10.0),
        "u": _blob_file(tmp_path / "u.grib2", "u", base=5.0, amp=10.0),
        "clc": _blob_file(tmp_path / "clc.grib2", "t", base=20.0, amp=30.0),
    }
    lats, lons = [48.0, 50.3, 75.0, 45.1], [11.5, 4.4, 10.0, -2.0]

    want = dec.decode_icon_eu_per_point_chunked(
        {v: p.read_bytes() for v, p in blobs.items()}, lats, lons,
    )
    got = dec.decode_icon_eu_per_point_chunked(
        {v: _convert(tmp_path, p) for v, p in blobs.items()}, lats, lons,
    )

    assert got == want
    assert got[0][0]  # something was decoded


def test_refuses_blobs_the_decoder_would_not_read_as_one_column(tmp_path):
    two_vars = _blob_file(tmp_path / "two.grib2", "t")
    _blob_file(two_vars, "u")
    single = _blob_file(tmp_path / "one.grib2", "t", levels=[40])

    for grib in (two_vars, single):
        store = tmp_path / icon_zarr.store_name(grib.name)
        assert not icon_zarr.convert_grib_to_store(grib, store)
        assert not store.exists()
    # Nothing half-built left behind either.
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_existing_store_wins_a_race(tmp_path):
    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)
    before = (store / "zarr.json").stat().st_mtime_ns
    assert icon_zarr.convert_grib_to_store(grib, store)
    assert (store / "zarr.json").stat().st_mtime_ns == before


def _grib_bytes(tmp_path: Path, short: str = "t") -> bytes:
    path = _blob_file(tmp_path / f"src_{short}.grib2", short)
    data = io.FileIO(path).readall()
    path.unlink()
    return data


@pytest.fixture
def inline_pool(monkeypatch):
    """A decode pool that runs jobs in this process.

    Conversion is skipped when no pool is configured, so the write-hook tests
    report a pool and run the worker function inline instead of spawning one.
    """
    import weatherbrief.fetch.grib as grib_mod
    from weatherbrief.fetch.grib import decode_worker

    monkeypatch.delenv("WB_ICON_ZARR", raising=False)
    monkeypatch.setattr(grib_mod, "decode_pool_enabled", lambda: True)
    monkeypatch.setattr(
        grib_mod, "_dispatch_decode",
        lambda name, *args, priority=None: getattr(decode_worker, name)(*args),
    )


def test_cache_write_converts_and_drops_grib(tmp_path, inline_pool):
    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    ck = "f003_ICON_EU_T.grib2"

    entry = icon_zarr.cache_model_level_blob(run_dir, ck, _grib_bytes(tmp_path))

    assert entry == run_dir / "f003_ICON_EU_T.zarr"
    assert not (run_dir / ck).exists()
    assert icon_zarr.cached_model_level_entry(run_dir, ck) == entry


def test_rollback_switch_keeps_grib(tmp_path, monkeypatch):
    monkeypatch.setenv("WB_ICON_ZARR", "0")
    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    ck = "f003_ICON_EU_T.grib2"

    entry = icon_zarr.cache_model_level_blob(run_dir, ck, _grib_bytes(tmp_path))

    assert entry == run_dir / ck and entry.exists()
    assert icon_zarr.cached_model_level_entry(run_dir, ck) == entry


def test_no_decode_pool_keeps_grib(tmp_path, monkeypatch):
    """GRIB_DECODE_WORKERS=0: no inline ~360 MB conversion on the app process."""
    monkeypatch.delenv("WB_ICON_ZARR", raising=False)
    monkeypatch.setenv("GRIB_DECODE_WORKERS", "0")
    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    ck = "f003_ICON_EU_T.grib2"

    entry = icon_zarr.cache_model_level_blob(run_dir, ck, _grib_bytes(tmp_path))

    assert entry == run_dir / ck and entry.exists()
    assert not (run_dir / "f003_ICON_EU_T.zarr").exists()


def test_failed_conversion_keeps_grib(tmp_path, inline_pool):
    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    ck = "f003_ICON_EU_T.grib2"
    data = _grib_bytes(tmp_path, "t") + _grib_bytes(tmp_path, "u")  # refused

    entry = icon_zarr.cache_model_level_blob(run_dir, ck, data)

    assert entry == run_dir / ck and entry.exists()


def test_store_ttl_expiry_removes_it(tmp_path):
    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    run_dir.mkdir(parents=True)
    grib = _blob_file(run_dir / "f003_ICON_EU_T.grib2", "t")
    store = _convert(run_dir, grib)
    assert is_store_cached(run_dir, store.name)

    old = time.time() - 13 * 3600  # icon-eu TTL is 12 h
    os.utime(store / "zarr.json", (old, old))

    assert not is_store_cached(run_dir, store.name)
    assert not store.exists()


def test_worker_reads_store_when_grib_was_converted_under_it(tmp_path):
    """The parent listed the GRIB, then a concurrent writer converted and
    deleted it: the worker falls back to the store beside it."""
    from weatherbrief.fetch.grib.decode_worker import _icon_var_source

    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)
    raw = grib.read_bytes()

    assert _icon_var_source([str(grib)]) == raw  # GRIB still there: GRIB
    grib.unlink()
    assert _icon_var_source([str(grib)]) == store
    assert _icon_var_source([str(store)]) == store


# ---------------------------------------------------------------------------
# Prefetch and decode planners see a store as the cached variable
# ---------------------------------------------------------------------------


def _fake_store(run_dir: Path, fhour: int, var: str) -> Path:
    """A store as the planners see it: a directory with a fresh zarr.json."""
    store = run_dir / f"f{fhour:03d}_ICON_EU_{var.upper()}.zarr"
    store.mkdir(parents=True)
    (store / "zarr.json").write_text("{}")
    return store


def _eu_ctx(run_dir: Path):
    from weatherbrief.fetch.grib import _IconEuContext
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU as EU

    return _IconEuContext(
        init_date="20261024", init_hour=6, forecast_hours=[12], run_dir=run_dir,
        levels=[35, 36], point_lats=[48.0], point_lons=[11.0],
        session=None, variant=EU,
    )


def test_decode_planner_prefers_store_and_mixes_with_grib(tmp_path):
    from datetime import datetime, timezone
    from unittest.mock import patch

    import weatherbrief.fetch.grib as grib_mod
    from weatherbrief.fetch.grib.cache import cache_key, put_cached
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_VARIABLES
    from weatherbrief.models import ModelSource, RouteCrossSection

    for var in ICON_EU_VARIABLES:
        if var == "t":
            put_cached(tmp_path, cache_key(12, "ICON_EU_T"), b"grib")  # pre-#705
        else:
            _fake_store(tmp_path, 12, var)
    cs = RouteCrossSection(
        model=ModelSource.ICON, route_points=[],
        fetched_at=datetime(2026, 10, 24, tzinfo=timezone.utc), point_forecasts=[],
    )
    captured = {}

    def fake_parallel(job_list):
        captured["jobs"] = job_list
        return []

    # The #912 surface decode fetches its own missing files: not under test.
    with patch.object(grib_mod, "_dispatch_decode_parallel", side_effect=fake_parallel), \
            patch.object(grib_mod, "_decode_icon_surface", return_value={}):
        grib_mod._decode_and_merge_icon_eu(_eu_ctx(tmp_path), [cs], [], [])

    (worker, (var_paths, _, _)), = captured["jobs"]
    assert worker == "decode_icon_chunked"
    assert set(var_paths) == set(ICON_EU_VARIABLES)
    assert var_paths["t"] == [str(tmp_path / "f012_ICON_EU_T.grib2")]
    assert var_paths["u"] == [str(tmp_path / "f012_ICON_EU_U.zarr")]


def test_prefetch_skips_variables_held_as_stores(tmp_path, monkeypatch):
    import weatherbrief.fetch.grib.icon_eu_fetch as icon_fetch_mod
    from weatherbrief.fetch.grib import _prefetch_icon_eu_data_inner
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU_VARIABLES

    fetched = []

    def fake_per_variable(init_date, init_hour, fhour, levels, variables, **kw):
        fetched.append(variables[0])
        return {}

    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_per_variable", fake_per_variable)
    monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_single_level", lambda *a, **k: {})
    _fake_store(tmp_path, 12, "qc")

    _prefetch_icon_eu_data_inner(_eu_ctx(tmp_path))

    assert "qc" not in fetched
    assert set(fetched) == set(ICON_EU_VARIABLES) - {"qc"}


def test_unreadable_store_is_removed_so_prefetch_refetches(tmp_path):
    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)
    grib.unlink()
    # Every shard: which band holds the target's tiles depends on the grid.
    for shard in (p for p in store.rglob("*") if p.is_file() and p.name != "zarr.json"):
        shard.write_bytes(b"garbage")

    assert dec._decode_icon_eu_single_var(store, [48.0], [11.5]) == {}
    assert not store.exists()
    assert icon_zarr.cached_model_level_entry(tmp_path, grib.name) is None


def test_transient_read_error_keeps_the_store(tmp_path, monkeypatch):
    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)

    def boom(*a, **k):
        raise OSError("I/O pressure")

    monkeypatch.setattr(icon_zarr, "read_model_level_columns", boom)
    assert dec._decode_icon_eu_single_var(store, [48.0], [11.5]) == {}
    assert (store / "zarr.json").exists()


def test_reader_bug_keeps_the_store(tmp_path, monkeypatch):
    """A failure outside the zarr reads (here the weights) is not corruption:
    skip the variable, never delete a good store."""
    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)

    def bug(*a, **k):
        raise ValueError("reader bug")

    monkeypatch.setattr(dec, "_bilinear_grid_weights", bug)
    assert dec._decode_icon_eu_single_var(store, [48.0], [11.5]) == {}
    assert (store / "zarr.json").exists()


# ---------------------------------------------------------------------------
# Briefing prefetch: conversion queued, never waited for
# ---------------------------------------------------------------------------


@pytest.fixture
def queued_pool(monkeypatch):
    """A dispatcher whose submit_one hands back a future the test resolves."""
    from concurrent.futures import Future

    import weatherbrief.fetch.grib as grib_mod

    monkeypatch.delenv("WB_ICON_ZARR", raising=False)
    monkeypatch.setattr(grib_mod, "decode_pool_enabled", lambda: True)
    monkeypatch.setattr(grib_mod, "_priority_enabled", lambda: True)
    submitted = []

    class _Dispatcher:
        def submit_one(self, name, args, priority):
            fut = Future()
            submitted.append((name, args, priority, fut))
            return fut

    monkeypatch.setattr(grib_mod, "_get_dispatcher", lambda: _Dispatcher())
    return submitted


def test_queued_conversion_returns_at_once_and_drops_grib_when_done(tmp_path, queued_pool):
    from weatherbrief.fetch.grib import DecodePriority
    from weatherbrief.fetch.grib.decode_worker import convert_icon_zarr

    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    ck = "f003_ICON_EU_T.grib2"

    entry = icon_zarr.cache_model_level_blob(
        run_dir, ck, _grib_bytes(tmp_path),
        priority=DecodePriority.BACKGROUND, wait=False,
    )

    # The briefing gets the GRIB straight away and decodes it as before.
    assert entry == run_dir / ck and entry.exists()
    (name, args, priority, fut), = queued_pool
    assert name == "convert_icon_zarr" and priority == int(DecodePriority.BACKGROUND)

    fut.set_result(convert_icon_zarr(*args))  # the pool runs it later

    assert not (run_dir / ck).exists()
    assert icon_zarr.cached_model_level_entry(run_dir, ck) == run_dir / "f003_ICON_EU_T.zarr"


def test_queued_conversion_that_fails_keeps_grib(tmp_path, queued_pool):
    run_dir = tmp_path / "icon-eu" / "20261024_06z"
    ck = "f003_ICON_EU_T.grib2"

    icon_zarr.cache_model_level_blob(run_dir, ck, _grib_bytes(tmp_path), wait=False)
    (_, _, _, fut), = queued_pool
    fut.set_exception(RuntimeError("dispatcher_shutdown"))

    assert (run_dir / ck).exists()
    assert icon_zarr.cached_model_level_entry(run_dir, ck) == run_dir / ck


def test_failed_job_still_returns_a_store_a_racing_writer_finished(tmp_path):
    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)

    assert icon_zarr._after_conversion(False, grib, store) == store
    assert not grib.exists()


def test_worker_reads_store_when_grib_vanishes_at_read_time(tmp_path, monkeypatch):
    """The GRIB passes any existence check, then a queued conversion deletes
    it just before the read: the read itself falls back to the store."""
    from weatherbrief.fetch.grib.decode_worker import _icon_var_source

    grib = _blob_file(tmp_path / "f003_ICON_EU_T.grib2", "t")
    store = _convert(tmp_path, grib)
    real_read = Path.read_bytes

    def read_after_unlink(self):
        if self == grib:
            grib.unlink(missing_ok=True)
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", read_after_unlink)
    assert _icon_var_source([str(grib)]) == store
