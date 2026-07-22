"""Tests for the atomic semantics of the GRIB disk cache.

Companion to the cache tests in ``test_grib.py`` which cover happy-path
roundtrip / miss / key format. This module focuses on concurrent-writer
correctness — see issue #123.
"""

from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

from weatherbrief.fetch.grib.cache import (
    DEFAULT_CACHE_FLOOR_RUNS,
    cache_cap_bytes,
    cache_dir_for_run,
    cache_key,
    get_cached,
    init_dt_from_run_dir,
    purge_old_runs,
    put_cached,
)


def test_put_cached_concurrent_writes_no_corruption(tmp_path):
    """Racing writers must leave the file equal to one writer's payload.

    Two threads opening the same path in ``"wb"`` mode would each truncate
    and then interleave bytes; the resulting file decodes neither payload.
    With temp-file + ``os.replace`` the final file must byte-for-byte equal
    one of the inputs (last-rename-wins is fine; corruption is not).
    """
    run_dir = cache_dir_for_run(tmp_path, "20260507", 0)
    filename = cache_key(0, "T")

    # Distinct, large-ish payloads so any interleave is detectable —
    # two different random byte strings can't accidentally collide.
    payloads = [secrets.token_bytes(64 * 1024) for _ in range(8)]
    barrier = threading.Barrier(len(payloads))
    errors: list[BaseException] = []

    def writer(data: bytes) -> None:
        try:
            barrier.wait(timeout=5)
            put_cached(run_dir, filename, data)
        except BaseException as e:  # noqa: BLE001 — surface to assertion
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writer thread raised: {errors!r}"

    final = get_cached(run_dir, filename)
    assert final is not None
    assert final in payloads, "file is an interleave of multiple writers"

    # No leftover ".tmp" siblings from crashed/aborted writers.
    leftovers = [p.name for p in run_dir.iterdir() if p.name != filename]
    assert leftovers == [], f"unexpected files in cache dir: {leftovers}"


def test_put_cached_cleans_up_tempfile_on_error(tmp_path, monkeypatch):
    """If ``os.replace`` fails the tempfile must not be left behind."""
    run_dir = cache_dir_for_run(tmp_path, "20260507", 0)
    run_dir.mkdir(parents=True, exist_ok=True)

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)
    try:
        try:
            put_cached(run_dir, "f000_T.grib2", b"payload")
        except OSError:
            pass
        else:
            raise AssertionError("expected OSError from patched os.replace")
    finally:
        monkeypatch.setattr(os, "replace", real_replace)

    assert list(run_dir.iterdir()) == [], "tempfile was not cleaned up on error"


# ---------------------------------------------------------------------------
# purge_old_runs — init-time aging (issue #475 item 1)
# ---------------------------------------------------------------------------


def _make_run(data_dir, model, init_date, init_hour, *, n_files=1, size=1024):
    """Create a cache run dir with ``n_files`` files of ``size`` bytes each."""
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=model)
    for i in range(n_files):
        put_cached(run_dir, cache_key(i, "T"), b"x" * size)
    return run_dir


def test_init_dt_from_run_dir_parses_name():
    run_dir = cache_dir_for_run(
        __import__("pathlib").Path("/tmp/x"), "20260721", 12, model="icon-d2",
    )
    assert init_dt_from_run_dir(run_dir) == datetime(
        2026, 7, 21, 12, tzinfo=timezone.utc,
    )


def test_init_dt_from_run_dir_rejects_bad_names(tmp_path):
    for name in ("garbage", "20260721", "20260721_12", "notadate_12z", "20260721_XXz"):
        assert init_dt_from_run_dir(tmp_path / name) is None


def test_purge_ages_by_init_not_mtime(tmp_path):
    """A run dir with a FRESH mtime but an init older than TTL is purged.

    ICON-D2 TTL is 6 h. The dir's files are written *now* (fresh mtime), but its
    name encodes an init 10 h before ``now`` — aging by init must purge it,
    where aging by mtime would keep it. This is the core issue-#475 bug.
    """
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    init = now - timedelta(hours=10)  # > 6 h D2 TTL
    run_dir = _make_run(
        tmp_path, "icon-d2", init.strftime("%Y%m%d"), init.hour,
    )
    assert run_dir.exists()

    removed = purge_old_runs(tmp_path, model="icon-d2", now=now)

    assert removed == 1
    assert not run_dir.exists()


def test_purge_keeps_fresh_init(tmp_path):
    """A run whose init is within the TTL survives even if it looks old on disk."""
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    init = now - timedelta(hours=2)  # < 6 h D2 TTL
    run_dir = _make_run(
        tmp_path, "icon-d2", init.strftime("%Y%m%d"), init.hour,
    )

    removed = purge_old_runs(tmp_path, model="icon-d2", now=now)

    assert removed == 0
    assert run_dir.exists()


def test_purge_unparseable_name_falls_back_to_mtime(tmp_path):
    """A dir whose name doesn't parse is aged by mtime, not pinned forever."""
    cache_root = tmp_path / ".cache" / "grib" / "icon-d2"
    stray = cache_root / "legacy-run"
    stray.mkdir(parents=True)
    (stray / "f000_T.grib2").write_bytes(b"x")
    # Backdate its mtime well beyond the 6 h TTL.
    old = (datetime(2026, 7, 21, 12, tzinfo=timezone.utc) - timedelta(hours=48))
    os.utime(stray, (old.timestamp(), old.timestamp()))

    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    removed = purge_old_runs(tmp_path, model="icon-d2", now=now)

    assert removed == 1
    assert not stray.exists()


# ---------------------------------------------------------------------------
# purge_old_runs — size cap eviction (issue #475 item 2)
# ---------------------------------------------------------------------------


def test_cap_evicts_oldest_init_first(tmp_path):
    """Over-cap: the oldest-init run is evicted; newer runs survive."""
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    # Three within-TTL runs (2/4/6 h old), ~4 KiB each → 12 KiB total.
    runs = {}
    for hours_old in (6, 4, 2):
        init = now - timedelta(hours=hours_old)
        runs[hours_old] = _make_run(
            tmp_path, "icon-d2", init.strftime("%Y%m%d"), init.hour,
            n_files=4, size=1024,
        )

    # Cap at ~9 KiB with floor 1 forces eviction of the single oldest run.
    removed = purge_old_runs(
        tmp_path, model="icon-d2", cap_bytes=9 * 1024, floor_runs=1, now=now,
    )

    assert removed == 1
    assert not runs[6].exists()   # oldest init evicted
    assert runs[4].exists()
    assert runs[2].exists()


def test_cap_never_breaches_floor(tmp_path):
    """Eviction stops at the floor even when still over cap."""
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    runs = []
    for hours_old in (5, 3, 1):
        init = now - timedelta(hours=hours_old)
        runs.append(_make_run(
            tmp_path, "icon-d2", init.strftime("%Y%m%d"), init.hour,
            n_files=4, size=1024,
        ))

    # Cap far below total, floor 2 → only the single oldest may go.
    removed = purge_old_runs(
        tmp_path, model="icon-d2", cap_bytes=1, floor_runs=2, now=now,
    )

    assert removed == 1
    surviving = [r for r in runs if r.exists()]
    assert len(surviving) == 2  # floor preserved


def test_cap_logs_each_eviction(tmp_path, caplog):
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    for hours_old in (6, 4, 2):
        init = now - timedelta(hours=hours_old)
        _make_run(
            tmp_path, "icon-d2", init.strftime("%Y%m%d"), init.hour,
            n_files=4, size=1024,
        )

    with caplog.at_level("INFO"):
        purge_old_runs(
            tmp_path, model="icon-d2", cap_bytes=9 * 1024, floor_runs=1, now=now,
        )

    cap_logs = [r for r in caplog.records if "cache cap: evicted" in r.getMessage()]
    assert len(cap_logs) == 1
    assert "icon-d2" in cap_logs[0].getMessage()


def test_cap_default_applies_to_icon_d2(monkeypatch):
    monkeypatch.delenv("WB_GRIB_CACHE_CAP_GB_ICON_D2", raising=False)
    cap = cache_cap_bytes("icon-d2")
    assert cap is not None and cap == int(45.0 * 1024 ** 3)


def test_cap_env_override(monkeypatch):
    monkeypatch.setenv("WB_GRIB_CACHE_CAP_GB_ICON_D2", "40")
    assert cache_cap_bytes("icon-d2") == int(40.0 * 1024 ** 3)
    # Zero / negative disables the cap.
    monkeypatch.setenv("WB_GRIB_CACHE_CAP_GB_ICON_D2", "0")
    assert cache_cap_bytes("icon-d2") is None


def test_cap_none_for_uncapped_models(monkeypatch):
    monkeypatch.delenv("WB_GRIB_CACHE_CAP_GB_GFS", raising=False)
    monkeypatch.delenv("WB_GRIB_CACHE_CAP_GB_ICON_EU", raising=False)
    assert cache_cap_bytes("gfs") is None
    assert cache_cap_bytes("icon-eu") is None


def test_default_floor_is_two():
    assert DEFAULT_CACHE_FLOOR_RUNS == 2
