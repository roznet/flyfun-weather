"""Frame store: publication ordering, retention, and what counts as present."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_OPERA_DBZH,
    SOURCE_SPECS,
    FrameStore,
    frame_stamp,
    parse_frame_stamp,
)

NOW = datetime(2026, 8, 25, 14, 5, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path) -> FrameStore:
    return FrameStore(tmp_path / "observed")


def _write(store: FrameStore, source: str, when: datetime, payload=b"x"):
    return store.write(source, when, payload, {"quantity": "DBZH"})


def test_stamp_round_trips():
    assert parse_frame_stamp(frame_stamp(NOW)) == NOW.replace(second=0, microsecond=0)


def test_naive_valid_time_is_rejected():
    """A naive stamp would silently claim local time as UTC."""
    with pytest.raises(ValueError, match="timezone-aware"):
        frame_stamp(datetime(2026, 8, 25, 14, 5))


def test_write_then_read_back(store):
    frame = _write(store, SOURCE_OPERA_DBZH, NOW, b"payload")
    assert frame.path.read_bytes() == b"payload"
    assert store.has(SOURCE_OPERA_DBZH, NOW)
    listed = store.list_frames(SOURCE_OPERA_DBZH)
    assert [f.valid_time for f in listed] == [NOW]
    assert listed[0].meta["bytes"] == 7


def test_payload_without_sidecar_is_not_present(store):
    """A half-written frame must be re-fetched, not advertised."""
    store.write_payload(SOURCE_OPERA_DBZH, NOW, b"payload")
    assert not store.has(SOURCE_OPERA_DBZH, NOW)
    assert store.list_frames(SOURCE_OPERA_DBZH) == []
    store.write_sidecar(SOURCE_OPERA_DBZH, NOW, {"quantity": "DBZH"})
    assert store.has(SOURCE_OPERA_DBZH, NOW)


def test_frames_are_listed_newest_first(store):
    for minutes in (0, 5, 10):
        _write(store, SOURCE_OPERA_DBZH, NOW - timedelta(minutes=minutes))
    listed = store.list_frames(SOURCE_OPERA_DBZH)
    assert [f.valid_time for f in listed] == [
        NOW,
        NOW - timedelta(minutes=5),
        NOW - timedelta(minutes=10),
    ]


def test_latest_respects_max_age(store):
    _write(store, SOURCE_OPERA_DBZH, NOW - timedelta(minutes=45))
    assert store.latest(SOURCE_OPERA_DBZH, now=NOW) is not None
    stale = store.latest(SOURCE_OPERA_DBZH, max_age=timedelta(minutes=30), now=NOW)
    assert stale is None


def test_purge_uses_the_source_retention(store):
    """CTTH keeps an hour; radar keeps three."""
    assert SOURCE_SPECS[SOURCE_EUMETSAT_CTTH].retention < SOURCE_SPECS[SOURCE_OPERA_DBZH].retention
    for source in (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_CTTH):
        for hours in (0, 2):
            store.write(source, NOW - timedelta(hours=hours), b"x", {})
    assert store.purge(SOURCE_EUMETSAT_CTTH, now=NOW) == 1
    assert store.purge(SOURCE_OPERA_DBZH, now=NOW) == 0
    assert len(store.list_frames(SOURCE_EUMETSAT_CTTH)) == 1
    assert len(store.list_frames(SOURCE_OPERA_DBZH)) == 2


def test_purge_reclaims_orphaned_payloads(store):
    """A payload whose sidecar was lost is invisible — and would leak bytes."""
    old = NOW - timedelta(hours=6)
    store.write_payload(SOURCE_OPERA_DBZH, old, b"orphan")
    assert store.list_frames(SOURCE_OPERA_DBZH) == []
    assert store.purge(SOURCE_OPERA_DBZH, now=NOW) == 1
    assert not store.payload_path(SOURCE_OPERA_DBZH, old).exists()


def test_unreadable_sidecar_is_skipped_not_fatal(store):
    _write(store, SOURCE_OPERA_DBZH, NOW)
    store.sidecar_path(SOURCE_OPERA_DBZH, NOW).write_text("{not json")
    assert store.list_frames(SOURCE_OPERA_DBZH) == []


def test_attribution_survives_the_round_trip(store):
    store.write(
        SOURCE_OPERA_DBZH,
        NOW,
        b"x",
        {"attribution": {"producer": "Météo-France", "text": "OPERA · Météo-France"}},
    )
    frame = store.list_frames(SOURCE_OPERA_DBZH)[0]
    assert frame.attribution.producer == "Météo-France"
    assert "Météo-France" in frame.attribution.text


def test_disk_usage_is_reported_per_source(store):
    _write(store, SOURCE_OPERA_DBZH, NOW, b"0123456789")
    usage = store.disk_usage()
    assert usage[SOURCE_OPERA_DBZH] > 10  # payload plus sidecar
    assert usage[SOURCE_EUMETSAT_CTTH] == 0


def test_sidecar_records_valid_and_receipt_times_separately(store):
    """A frame's age is measured from its valid time, never from receipt."""
    frame = _write(store, SOURCE_OPERA_DBZH, NOW)
    meta = json.loads(store.sidecar_path(SOURCE_OPERA_DBZH, NOW).read_text())
    assert meta["valid_time"] == NOW.isoformat()
    assert meta["received_at"] != meta["valid_time"]
    assert frame.age_minutes(now=NOW + timedelta(minutes=7)) == pytest.approx(7.0)
