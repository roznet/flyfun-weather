"""Tests for the atomic semantics of the GRIB disk cache.

Companion to the cache tests in ``test_grib.py`` which cover happy-path
roundtrip / miss / key format. This module focuses on concurrent-writer
correctness — see issue #123.
"""

from __future__ import annotations

import os
import secrets
import threading

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,
    get_cached,
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
