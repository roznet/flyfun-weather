"""Observed conditions at the watchlist, persisted as Parquet (#575)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pyarrow.parquet as pq
import pytest

from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    SOURCE_OPERA_RATE,
    FrameStore,
)
from weatherbrief.tasks import observed_archive
from weatherbrief.tasks.observed_archive import (
    ALGORITHM_VERSION,
    ArchiveStation,
    arrow_schema,
    day_path,
    is_archived,
    manifest_path,
    on_stride,
    part_path,
    pending_frames,
    run_observed_archive,
    verify_observed_archive,
)

from .conftest import STATION, STATION_NO_COVERAGE

FRAME_TIME = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 25, 14, 30, tzinfo=timezone.utc)
# Past the day's finality: midnight + DAY_FINALITY on the following day.
AFTER_FINALITY = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)

STATIONS = [
    ArchiveStation(STATION.id, STATION.lat, STATION.lon),
    ArchiveStation(STATION_NO_COVERAGE.id, STATION_NO_COVERAGE.lat, STATION_NO_COVERAGE.lon),
    # Outside every source's declared domain: must produce no rows at all.
    ArchiveStation("KJFK", 40.64, -73.78),
]
IN_DOMAIN = 2
RADII = 3


@pytest.fixture
def store(tmp_path, dbzh_path, rate_path, ctth_path, li_path) -> FrameStore:
    store = FrameStore(tmp_path / "observed")
    received = (FRAME_TIME + timedelta(minutes=6)).isoformat()
    store.write(
        SOURCE_OPERA_DBZH, FRAME_TIME, dbzh_path.read_bytes(),
        {"received_at": received,
         "url": "https://x/openradar-24h/2026/08/25/OPERA/COMP/OPERA@20260825T1400@0@DBZH.h5"},
    )
    store.write(SOURCE_OPERA_RATE, FRAME_TIME, rate_path.read_bytes(), {"received_at": received})
    store.write(
        SOURCE_EUMETSAT_CTTH, FRAME_TIME, ctth_path.read_bytes(),
        {"received_at": received, "product_id": "W_XX-EUMETSAT-CTTH-test"},
    )
    store.write(SOURCE_EUMETSAT_LI, FRAME_TIME, li_path.read_bytes(), {"received_at": received})
    return store


@pytest.fixture
def root(tmp_path):
    return tmp_path / "archive" / "observed"


def _read(path):
    return pq.read_table(path).to_pylist()


def test_every_source_is_archived_for_in_domain_airports_only(store, root):
    result = run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    assert not result.errors
    for source in (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, SOURCE_EUMETSAT_CTTH, SOURCE_EUMETSAT_LI):
        assert result.frames[source] == 1
        path = part_path(root, source, FRAME_TIME)
        assert path.exists()
        table = pq.read_table(path)
        assert table.schema.equals(arrow_schema())
        rows = table.to_pylist()
        assert len(rows) == IN_DOMAIN * RADII
        assert {r["icao"] for r in rows} == {STATION.id, STATION_NO_COVERAGE.id}
        assert all(r["source"] == source for r in rows)
        assert all(r["algorithm_version"] == ALGORITHM_VERSION for r in rows)


def test_rows_carry_provenance(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    radar = _read(part_path(root, SOURCE_OPERA_DBZH, FRAME_TIME))[0]
    assert radar["product_id"] == "OPERA@20260825T1400@0@DBZH.h5"
    assert radar["ingested_at"] == FRAME_TIME + timedelta(minutes=6)
    # Latency is measured from the frame's own valid time, read from the file.
    assert radar["retrieval_latency_s"] == pytest.approx(
        (radar["ingested_at"] - radar["frame_valid_time"]).total_seconds()
    )
    assert radar["sampled_at"] == NOW
    assert radar["attribution_text"]
    # Radar is ground-projected: no viewing geometry to record.
    assert radar["satellite_view_angle_deg"] is None

    tops = _read(part_path(root, SOURCE_EUMETSAT_CTTH, FRAME_TIME))[0]
    assert tops["product_id"] == "W_XX-EUMETSAT-CTTH-test"
    assert tops["satellite_view_angle_deg"] == pytest.approx(50.5, abs=0.5)


def test_no_coverage_is_recorded_not_dropped(store, root):
    """A station with no radar still gets a row, saying it was not observed."""
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    rows = _read(part_path(root, SOURCE_OPERA_DBZH, FRAME_TIME))
    west = [r for r in rows if r["icao"] == STATION_NO_COVERAGE.id]
    assert west
    for row in west:
        assert row["insufficient_coverage"] is True
        assert row["coverage_floor"] == pytest.approx(0.35)
        assert row["nodata_px"] > 0
        assert row["total_px"] == row["valid_px"] + row["nodata_px"]
        assert row["valid_px"] == row["detected_px"] + row["undetect_px"]


def test_cloud_top_rows_keep_the_quality_histogram(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    rows = _read(part_path(root, SOURCE_EUMETSAT_CTTH, FRAME_TIME))
    lfat = [r for r in rows if r["icao"] == STATION.id]
    widest = max(lfat, key=lambda r: r["radius_nm"])
    histogram = dict(widest["quality_method"])
    assert histogram, "quality_method histogram must travel with the row"
    assert sum(histogram.values()) == widest["valid_px"]
    assert widest["qm_multilayer_px"] == histogram.get(9, 0)
    # The fixture's parallax-displaced cirrus is at FL350: it only reaches the
    # archive if the correction was applied before disc membership.
    assert widest["highest_fl"] is not None and widest["highest_fl"] > 300


def test_lightning_rows_have_no_coverage_split(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    for row in _read(part_path(root, SOURCE_EUMETSAT_LI, FRAME_TIME)):
        assert row["flash_count"] is not None
        assert row["area_km2"] > 0
        assert row["satellite_view_angle_deg"] is not None
        assert row["total_px"] is None
        assert row["insufficient_coverage"] is None


def test_banding_does_not_change_the_samples(store, root, tmp_path, monkeypatch):
    """Reading per latitude band must give exactly the single-read answer."""
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    monkeypatch.setattr(observed_archive, "BAND_STATIONS", 1)
    banded_root = tmp_path / "banded"
    run_observed_archive(STATIONS, store=store, root=banded_root, now=NOW)

    for source in (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_CTTH):
        assert _read(part_path(root, source, FRAME_TIME)) == _read(
            part_path(banded_root, source, FRAME_TIME)
        )


def test_a_second_run_archives_nothing_new(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    again = run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    assert sum(again.frames.values()) == 0


def test_dbzh_is_thinned_to_its_rolling_window():
    """DBZH is a rolling 10-min max: the :00/:10 frames tile time on their own."""
    at = lambda minute: datetime(2026, 8, 25, 14, minute, tzinfo=timezone.utc)  # noqa: E731
    assert on_stride(SOURCE_OPERA_DBZH, at(10))
    assert not on_stride(SOURCE_OPERA_DBZH, at(5))
    assert on_stride(SOURCE_OPERA_RATE, at(45))
    assert not on_stride(SOURCE_OPERA_RATE, at(10))
    assert on_stride(SOURCE_EUMETSAT_CTTH, at(50))


def test_off_stride_frames_are_not_pending(store, root, dbzh_path):
    store.write(
        SOURCE_OPERA_DBZH, FRAME_TIME + timedelta(minutes=5), dbzh_path.read_bytes(), {}
    )
    pending = pending_frames(store, SOURCE_OPERA_DBZH, root)
    assert [f.valid_time for f in pending] == [FRAME_TIME]


def test_a_broken_frame_does_not_stop_the_others(store, root):
    store.payload_path(SOURCE_OPERA_RATE, FRAME_TIME).write_bytes(b"not an hdf5 file")

    result = run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    assert len(result.errors) == 1 and SOURCE_OPERA_RATE in result.errors[0]
    assert result.frames[SOURCE_OPERA_RATE] == 0
    assert result.frames[SOURCE_OPERA_DBZH] == 1
    # Not marked done: the next run retries it while it is still on disk.
    assert not is_archived(root, SOURCE_OPERA_RATE, FRAME_TIME)


def test_days_are_not_compacted_before_they_are_final(store, root):
    result = run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    assert result.compacted == {}
    assert not day_path(root, SOURCE_OPERA_DBZH, "2026-08-25").exists()


def test_final_days_are_compacted_and_verifiable(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    result = run_observed_archive(STATIONS, store=store, root=root, now=AFTER_FINALITY)

    assert result.compacted[SOURCE_OPERA_DBZH] == ["2026-08-25"]
    day_file = day_path(root, SOURCE_OPERA_DBZH, "2026-08-25")
    assert pq.read_table(day_file).num_rows == IN_DOMAIN * RADII
    assert not (root / "parts" / SOURCE_OPERA_DBZH / "2026-08-25").exists()

    manifest = json.loads(manifest_path(root, SOURCE_OPERA_DBZH, "2026-08-25").read_text())
    assert manifest["rows"] == IN_DOMAIN * RADII
    assert manifest["frames"] == ["20260825T1400"]
    assert manifest["frames_expected"] == 144
    assert manifest["frames_missing"] == 143
    assert manifest["algorithm_versions"] == [ALGORITHM_VERSION]

    report = verify_observed_archive(root)
    assert report and all(r["ok"] for r in report)

    # The frame is still on disk, but its day is sealed: it is not re-written
    # as an orphan part.
    assert is_archived(root, SOURCE_OPERA_DBZH, FRAME_TIME)
    assert pending_frames(store, SOURCE_OPERA_DBZH, root) == []


def test_a_late_part_is_merged_into_its_compacted_day(store, root, dbzh_path):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    run_observed_archive(STATIONS, store=store, root=root, now=AFTER_FINALITY)

    late = FRAME_TIME + timedelta(minutes=10)
    store.write(SOURCE_OPERA_DBZH, late, dbzh_path.read_bytes(), {})
    stored = next(f for f in store.list_frames(SOURCE_OPERA_DBZH) if f.valid_time == late)
    observed_archive.archive_frame(stored, STATIONS, root=root)
    observed_archive.compact_day(root, SOURCE_OPERA_DBZH, "2026-08-25")

    manifest = json.loads(manifest_path(root, SOURCE_OPERA_DBZH, "2026-08-25").read_text())
    assert manifest["rows"] == 2 * IN_DOMAIN * RADII
    assert manifest["frames"] == ["20260825T1400", "20260825T1410"]


def test_verify_flags_a_tampered_day(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    run_observed_archive(STATIONS, store=store, root=root, now=AFTER_FINALITY)
    with day_path(root, SOURCE_OPERA_DBZH, "2026-08-25").open("ab") as fh:
        fh.write(b"x")

    bad = [r for r in verify_observed_archive(root) if not r["ok"]]
    assert [(r["source"], r["problem"]) for r in bad] == [(SOURCE_OPERA_DBZH, "sha256 mismatch")]


def test_archive_reads_no_forecast_data(store, root, monkeypatch):
    """Observations only: nothing in the archive path may touch the network."""
    import socket

    def _refuse(*_a, **_k):
        raise AssertionError("observed archive attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    result = run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    assert not result.errors


def _compact_with_crash_before_part_cleanup(store, root, monkeypatch, *, lose_manifest):
    """Compact, but die before the parts are deleted (timeout / OOM kill)."""
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)

    def _killed(*_a, **_k):
        raise KeyboardInterrupt("child killed")

    monkeypatch.setattr(observed_archive.shutil, "rmtree", _killed)
    with pytest.raises(KeyboardInterrupt):
        observed_archive.compact_day(root, SOURCE_OPERA_DBZH, "2026-08-25")
    monkeypatch.undo()
    if lose_manifest:
        # The other crash window: day file renamed, manifest not yet written.
        manifest_path(root, SOURCE_OPERA_DBZH, "2026-08-25").unlink()
    assert part_path(root, SOURCE_OPERA_DBZH, FRAME_TIME).exists()


@pytest.mark.parametrize("lose_manifest", [False, True])
def test_recompaction_after_a_crash_does_not_duplicate_rows(
    store, root, monkeypatch, lose_manifest
):
    _compact_with_crash_before_part_cleanup(
        store, root, monkeypatch, lose_manifest=lose_manifest
    )

    result = run_observed_archive(STATIONS, store=store, root=root, now=AFTER_FINALITY)

    assert result.compacted[SOURCE_OPERA_DBZH] == ["2026-08-25"]
    table = pq.read_table(day_path(root, SOURCE_OPERA_DBZH, "2026-08-25"))
    assert table.num_rows == IN_DOMAIN * RADII
    keys = list(zip(
        table.column("icao").to_pylist(), table.column("radius_nm").to_pylist(),
    ))
    assert len(keys) == len(set(keys))
    manifest = json.loads(manifest_path(root, SOURCE_OPERA_DBZH, "2026-08-25").read_text())
    assert manifest["rows"] == IN_DOMAIN * RADII
    assert manifest["frames"] == ["20260825T1400"]
    assert not (root / "parts" / SOURCE_OPERA_DBZH / "2026-08-25").exists()
    assert all(r["ok"] for r in verify_observed_archive(root))


def test_an_unreadable_manifest_is_reported_not_fatal(store, root):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    run_observed_archive(STATIONS, store=store, root=root, now=AFTER_FINALITY)
    manifest_path(root, SOURCE_OPERA_DBZH, "2026-08-25").write_text("")

    report = verify_observed_archive(root)

    bad = [r for r in report if not r["ok"]]
    assert [(r["source"], r["problem"]) for r in bad] == [
        (SOURCE_OPERA_DBZH, "manifest unreadable")
    ]
    # The other sources' days are still checked.
    assert len(report) == 4


def test_a_day_sealed_with_missing_frames_is_logged(store, root, caplog):
    run_observed_archive(STATIONS, store=store, root=root, now=NOW)
    with caplog.at_level("WARNING", logger="weatherbrief.tasks.observed_archive"):
        run_observed_archive(STATIONS, store=store, root=root, now=AFTER_FINALITY)
    assert any("143 of 144 frames missing" in m for m in caplog.messages)
