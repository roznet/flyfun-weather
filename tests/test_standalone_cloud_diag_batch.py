"""GFS/ICON cloud-diag decode is fanned out through ``_dispatch_decode_parallel``.

Before #459 both ``fetch_gfs_cloud_diag`` and ``fetch_icon_cloud_diag`` walked
one blocking ``_dispatch_decode`` per forecast hour, so only one pool worker was
ever busy. The fix collects the per-hour decodes into a single batch. These
tests assert the batch shape and that a per-job decode failure degrades just
that hour (the rest still map back in order).
"""

from __future__ import annotations

from unittest.mock import patch

from weatherbrief.tasks.standalone_grib import (
    fetch_gfs_cloud_diag,
    fetch_icon_cloud_diag,
)


def test_gfs_cloud_diag_batches_and_isolates_failures():
    fhours = [6, 9, 12]
    captured: dict = {}

    def fake_parallel(jobs, *, priority=None, return_exceptions=False, max_inflight=None):
        captured["jobs"] = list(jobs)
        captured["return_exceptions"] = return_exceptions
        # f6 ok, f9 fails (e.g. TTL-expired file), f12 ok — one result per job.
        return [[{"i": 0}], ValueError("boom f9"), [{"i": 2}]]

    with patch("weatherbrief.tasks.standalone_grib.is_cached", return_value=True), \
         patch("weatherbrief.fetch.grib._dispatch_decode_parallel", fake_parallel), \
         patch(
             "weatherbrief.fetch.grib.decode.build_cloud_diagnostics",
             return_value=None,
         ):
        result = fetch_gfs_cloud_diag("20260618", 0, fhours, [50.0], [0.0])

    # Exactly one batched dispatch carrying every hour, isolation on.
    assert captured["return_exceptions"] is True
    assert [name for name, _ in captured["jobs"]] == ["decode_gfs_cloud_diag"] * 3
    # File-path args are strings (picklable across the spawn boundary).
    for _, args in captured["jobs"]:
        assert isinstance(args[0], str)

    # The failed hour is dropped; the healthy hours map back by position.
    assert set(result) == {6, 12}
    assert len(result[6]) == 1 and len(result[12]) == 1


def test_icon_cloud_diag_batches_and_isolates_failures():
    fhours = [3, 6]
    captured: dict = {}

    def fake_parallel(jobs, *, priority=None, return_exceptions=False, max_inflight=None):
        captured["jobs"] = list(jobs)
        return [ValueError("boom f3"), [{"i": 1}]]

    with patch("weatherbrief.tasks.standalone_grib.is_cached", return_value=True), \
         patch("weatherbrief.fetch.grib._dispatch_decode_parallel", fake_parallel), \
         patch(
             "weatherbrief.fetch.grib.decode.build_icon_cloud_diagnostics",
             return_value=None,
         ):
        result = fetch_icon_cloud_diag("20260618", 0, fhours, [50.0], [0.0])

    assert [name for name, _ in captured["jobs"]] == ["decode_icon_cloud_diag"] * 2
    assert set(result) == {6}


def test_cloud_diag_no_jobs_skips_dispatch():
    """When nothing is cached and every fetch yields no data, the helper must
    not call the batch dispatcher at all."""
    called = {"n": 0}

    def fake_parallel(jobs, **kwargs):
        called["n"] += 1
        return [[] for _ in jobs]

    with patch("weatherbrief.tasks.standalone_grib.is_cached", return_value=False), \
         patch(
             "weatherbrief.fetch.grib.grib_fetch.fetch_idx", return_value="",
         ), \
         patch(
             "weatherbrief.fetch.grib.gfs_idx.plan_cloud_diag_byte_ranges",
             return_value=[],
         ), \
         patch("weatherbrief.fetch.grib._dispatch_decode_parallel", fake_parallel):
        result = fetch_gfs_cloud_diag("20260618", 0, [6, 9], [50.0], [0.0])

    assert result == {}
    assert called["n"] == 0, "no cached files → no decode batch"
