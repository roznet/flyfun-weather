"""Droplet side of the cell overlay (#656): validation, ingest, retention, listing."""

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.observed import cells_display as cd
from weatherbrief.observed.frames import frame_stamp

from .cells_helpers import display_bytes, display_cell_doc, display_doc

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _drop(inbox, t, doc=None, raw=None):
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{frame_stamp(t)}.json.gz"
    path.write_bytes(raw if raw is not None else display_bytes(doc or display_doc(t)))
    return path


# --- Validation -------------------------------------------------------------------


def test_a_good_file_validates():
    t = NOW - timedelta(minutes=10)
    data = cd.validate(display_bytes(display_doc(t)), frame_stamp(t))
    assert data["cells"][0]["id"] == "core41-a"


@pytest.mark.parametrize("mutate, why", [
    (lambda d: d.update(schema="observed-cells/1"), "schema"),
    (lambda d: d.update(schema=None), "schema"),
    (lambda d: d.update(policy_version="cells-1"), "policy_version"),
    (lambda d: d.update(policy_version=7), "policy_version"),
    (lambda d: d.update(valid_time="2026-10-04T11:45:00+00:00"), "does not match"),
    (lambda d: d.update(valid_time="2026-10-04T11:50:00"), "does not match"),
    (lambda d: d.pop("cells"), "cells"),
    (lambda d: d.update(outlines=[]), "outlines"),
])
def test_bad_files_are_refused(mutate, why):
    t = NOW - timedelta(minutes=10)
    doc = display_doc(t)
    mutate(doc)
    with pytest.raises(cd.InvalidDisplay, match=why):
        cd.validate(display_bytes(doc), frame_stamp(t))


def test_garbage_and_oversize_are_refused():
    stamp = frame_stamp(NOW)
    with pytest.raises(cd.InvalidDisplay):
        cd.validate(b"not gzip", stamp)
    with pytest.raises(cd.InvalidDisplay):
        cd.validate(gzip.compress(b"[1,2,3]"), stamp)
    with pytest.raises(cd.InvalidDisplay, match="exceeds"):
        cd.validate(b"x" * (cd.MAX_FILE_BYTES + 1), stamp)


def test_only_stamp_named_files_count():
    assert cd.stamp_of("20261004T1150.json.gz") == "20261004T1150"
    for name in (".20261004T1150.json.gz.Ab12Cd", "20261004T1150.json", "rejected", "20261399T9999.json.gz"):
        assert cd.stamp_of(name) is None


# --- Ingest -------------------------------------------------------------------------


def test_ingest_moves_valid_files_into_the_store(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(minutes=10)
    src = _drop(inbox, t)
    raw = src.read_bytes()
    result = cd.ingest(inbox, store, now=NOW)
    assert result.accepted == [frame_stamp(t)]
    assert not src.exists()
    assert store.path(frame_stamp(t)).read_bytes() == raw  # stored as pushed


def test_ingest_keeps_the_node_write_time_rsync_carried(tmp_path):
    """#751: ``rsync -t`` keeps the node's mtime on the inbox file; ingest
    records it as when the frame was built, and the stored file's own mtime
    becomes the droplet's receipt time."""
    from weatherbrief.observed.storms import load_cell_frames

    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(minutes=10)
    built = t + timedelta(minutes=5, seconds=12)
    src = _drop(inbox, t)
    os.utime(src, (built.timestamp(), built.timestamp()))
    cd.ingest(inbox, store, now=NOW)
    assert cd.computed_at(frame_stamp(t)) == built
    frames = load_cell_frames(NOW, store=store)
    assert frames.computed_at == built
    assert frames.received_at == store.list()[0].received_at > built


def test_ingest_sets_bad_files_aside_and_keeps_going(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    bad_t, good_t = NOW - timedelta(minutes=15), NOW - timedelta(minutes=10)
    bad = display_doc(bad_t)
    bad["schema"] = "something-else/9"
    _drop(inbox, bad_t, bad)
    _drop(inbox, good_t)
    result = cd.ingest(inbox, store, now=NOW)
    assert result.rejected == [frame_stamp(bad_t)]
    assert result.accepted == [frame_stamp(good_t)]
    assert (inbox / "rejected" / f"{frame_stamp(bad_t)}.json.gz").exists()
    assert not store.path(frame_stamp(bad_t)).exists()
    # A second pass does not re-reject what was set aside.
    assert cd.ingest(inbox, store, now=NOW).rejected == []


def test_ingest_leaves_rsync_temp_files_alone(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    inbox.mkdir()
    tmp = inbox / ".20261004T1150.json.gz.Xy12zz"
    tmp.write_bytes(b"partial")
    assert cd.ingest(inbox, store, now=NOW).accepted == []
    assert tmp.exists()


def test_ingest_drops_files_already_past_retention(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    old = NOW - cd.RETENTION - timedelta(minutes=5)
    src = _drop(inbox, old)
    result = cd.ingest(inbox, store, now=NOW)
    assert result.expired == 1 and not src.exists() and store.list() == []


def test_a_missing_inbox_is_not_an_error(tmp_path):
    result = cd.ingest(tmp_path / "nope", cd.DisplayStore(tmp_path / "store"), now=NOW)
    assert result.accepted == [] and result.rejected == []


# --- Store: listing, retention, read ---------------------------------------------------


def _stock(store, minutes_ago):
    for m in minutes_ago:
        t = NOW - timedelta(minutes=m)
        store.write(frame_stamp(t), display_bytes(display_doc(t)))


def test_list_is_newest_first_with_received_at(tmp_path):
    store = cd.DisplayStore(tmp_path)
    _stock(store, [20, 10, 15])
    stamps = [d.stamp for d in store.list()]
    assert stamps == [frame_stamp(NOW - timedelta(minutes=m)) for m in (10, 15, 20)]
    assert all(d.received_at.tzinfo is not None for d in store.list())


def test_purge_keeps_24_hours(tmp_path):
    store = cd.DisplayStore(tmp_path)
    _stock(store, [5, 23 * 60, 24 * 60 + 5, 30 * 60])
    assert store.purge(now=NOW) == 2
    assert [d.stamp for d in store.list()] == [frame_stamp(NOW - timedelta(minutes=m)) for m in (5, 23 * 60)]


def test_purge_reclaims_old_temp_files_only(tmp_path):
    store = cd.DisplayStore(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    old, young = tmp_path / ".tmp-old", tmp_path / ".tmp-young"
    old.write_bytes(b"x")
    young.write_bytes(b"x")
    past = NOW.timestamp() - 3600
    os.utime(old, (past, past))
    os.utime(young, (NOW.timestamp(), NOW.timestamp()))
    store.purge(now=NOW)
    assert not old.exists() and young.exists()


def test_read_revalidates_and_refuses_a_bad_file(tmp_path):
    store = cd.DisplayStore(tmp_path)
    t = NOW - timedelta(minutes=10)
    store.write(frame_stamp(t), b"garbage")
    assert store.read(frame_stamp(t)) is None
    store.write(frame_stamp(t), display_bytes(display_doc(t)))
    assert store.read(frame_stamp(t))["valid_time"] == t.isoformat()
    assert store.read("20990101T0000") is None


# --- Status --------------------------------------------------------------------------


def test_status_fresh(tmp_path):
    store = cd.DisplayStore(tmp_path)
    _stock(store, [10, 15])
    st = cd.frames_status(store, now=NOW)
    assert st["enabled"] and not st["stale"] and st["unavailable_since"] is None
    assert st["newest"]["stamp"] == frame_stamp(NOW - timedelta(minutes=10))
    assert st["frames"][0]["age_minutes"] == 10.0
    assert st["stale_after_minutes"] == cd.STALE_AFTER.total_seconds() / 60


def test_status_says_unavailable_since_the_newest_overlay(tmp_path):
    store = cd.DisplayStore(tmp_path)
    minutes = int(cd.STALE_AFTER.total_seconds() // 60) + 5
    _stock(store, [minutes, minutes + 5])
    st = cd.frames_status(store, now=NOW)
    assert st["stale"]
    assert st["unavailable_since"] == (NOW - timedelta(minutes=minutes)).isoformat()


def test_status_with_nothing_received(tmp_path):
    st = cd.frames_status(cd.DisplayStore(tmp_path / "empty"), now=NOW)
    assert st["stale"] and st["newest"] is None and st["unavailable_since"] is None and st["frames"] == []


def test_disabled_status_shape_matches():
    assert set(cd.disabled_status()) == set(cd.frames_status(cd.DisplayStore("/nonexistent")))
    assert cd.disabled_status()["enabled"] is False


def test_ingest_flag(monkeypatch):
    monkeypatch.delenv(cd.CELLS_INGEST_ENV, raising=False)
    assert not cd.cells_ingest_enabled()
    for v in ("1", "true", "YES"):
        monkeypatch.setenv(cd.CELLS_INGEST_ENV, v)
        assert cd.cells_ingest_enabled()
    monkeypatch.setenv(cd.CELLS_INGEST_ENV, "0")
    assert not cd.cells_ingest_enabled()


# --- Bounding box -----------------------------------------------------------------------


def test_bbox_keeps_cells_inside_and_outlines_touching():
    t = NOW
    doc = display_doc(t, cells=[display_cell_doc("in", 50.5, 1.5), display_cell_doc("out", 45.0, 10.0)])
    out = cd.filter_bbox(doc, 49.8, 0.4, 51.2, 2.9)
    assert [c["id"] for c in out["cells"]] == ["in"]
    assert len(out["outlines"]["rain20"]) == 1 and out["outlines"]["core35"] == []
    assert out["bbox"] == [49.8, 0.4, 51.2, 2.9]
    assert len(doc["cells"]) == 2  # the cached original is untouched


def test_bbox_keeps_a_band_that_crosses_the_box_whole():
    band = [[48.0, 1.0], [52.0, 1.0], [52.0, 1.2], [48.0, 1.2], [48.0, 1.0]]
    doc = display_doc(NOW, cells=[], outlines={"rain20": [band]})
    out = cd.filter_bbox(doc, 49.8, 0.4, 51.2, 2.9)
    assert out["outlines"]["rain20"] == [band]


def test_ingest_loop_returns_at_once_when_disabled(monkeypatch):
    import asyncio

    from weatherbrief.scheduler import run_cells_ingest_loop

    monkeypatch.setenv(cd.CELLS_INGEST_ENV, "0")
    asyncio.run(asyncio.wait_for(run_cells_ingest_loop(None), timeout=5))


@pytest.mark.parametrize("mutate", [
    lambda d: d["cells"][0].pop("lat"),
    lambda d: d["cells"][0].update(lon="2.0"),
    lambda d: d["cells"].append("not a cell"),
    lambda d: d["outlines"].update(core41=[[[50.0]]]),
    lambda d: d["outlines"].update(core41=[[["a", "b"]]]),
    lambda d: d["outlines"].update(core41="nope"),
])
def test_malformed_cells_or_points_are_refused_at_ingest(mutate):
    """Review #660: one bad entry used to pass ingest and 500 every bbox request."""
    t = NOW - timedelta(minutes=10)
    doc = display_doc(t)
    mutate(doc)
    with pytest.raises(cd.InvalidDisplay):
        cd.validate(display_bytes(doc), frame_stamp(t))


def test_rejected_files_are_kept_for_a_day_only(tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "rejected").mkdir(parents=True)
    old, young = inbox / "rejected" / "a.json.gz", inbox / "rejected" / "b.json.gz"
    old.write_bytes(b"x")
    young.write_bytes(b"x")
    past = (NOW - cd.RETENTION - timedelta(minutes=1)).timestamp()
    os.utime(old, (past, past))
    os.utime(young, (NOW.timestamp(), NOW.timestamp()))
    assert cd.purge_rejected(inbox, now=NOW) == 1
    assert not old.exists() and young.exists()
    assert cd.purge_rejected(tmp_path / "missing", now=NOW) == 0


def test_a_nan_coordinate_is_refused():
    t = NOW - timedelta(minutes=10)
    doc = display_doc(t)
    doc["cells"][0]["lat"] = float("nan")
    raw = gzip.compress(json.dumps(doc).encode())  # NaN literal, as a careless writer would emit
    with pytest.raises(cd.InvalidDisplay):
        cd.validate(raw, frame_stamp(t))


def test_a_stored_stamp_is_never_replaced(tmp_path):
    """Review #660 (round 2): per-stamp responses are immutable, so ingest keeps the first copy."""
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(minutes=10)
    first = display_bytes(display_doc(t))
    _drop(inbox, t, raw=first)
    assert cd.ingest(inbox, store, now=NOW).accepted == [frame_stamp(t)]
    # Identical re-push: dropped quietly.
    src = _drop(inbox, t, raw=first)
    result = cd.ingest(inbox, store, now=NOW)
    assert result.duplicates == 1 and result.accepted == [] and result.rejected == [] and not src.exists()
    # Different bytes for the same stamp: set aside, the first copy stays.
    other = display_doc(t, cells=[])
    _drop(inbox, t, other)
    result = cd.ingest(inbox, store, now=NOW)
    assert result.rejected == [frame_stamp(t)]
    assert store.path(frame_stamp(t)).read_bytes() == first
    assert (inbox / "rejected" / f"{frame_stamp(t)}.json.gz").exists()


# --- Revisions (#666) -------------------------------------------------------------


def _drop_rev(inbox, t, revision, doc):
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{frame_stamp(t)}.r{revision}.json.gz"
    path.write_bytes(display_bytes({**doc, "revision": revision}))
    return path


def test_a_revision_is_stored_beside_the_first_copy_and_listed_instead(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(minutes=10)
    _drop(inbox, t)
    cd.ingest(inbox, store, now=NOW)
    _drop_rev(inbox, t, 1, display_doc(t, cells=[]))
    result = cd.ingest(inbox, store, now=NOW)
    assert result.accepted == [f"{frame_stamp(t)}.r1"]
    assert store.path(frame_stamp(t)).exists() and store.path(frame_stamp(t), 1).exists()
    (only,) = store.list()
    assert only.revision == 1 and only.key == f"{frame_stamp(t)}.r1"
    entry = only.entry(NOW)
    assert entry["key"] == f"{frame_stamp(t)}.r1" and entry["first_received_at"] <= entry["received_at"]
    assert store.read(frame_stamp(t), 1)["cells"] == []
    assert len(store.read(frame_stamp(t))["cells"]) == 2
    assert store.latest_revision(frame_stamp(t)) == 1


def test_a_revision_whose_content_disagrees_with_its_name_is_refused(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(minutes=10)
    path = inbox / f"{frame_stamp(t)}.r1.json.gz"
    inbox.mkdir(parents=True)
    path.write_bytes(display_bytes(display_doc(t)))  # says revision 0 (absent)
    assert cd.ingest(inbox, store, now=NOW).rejected == [f"{frame_stamp(t)}.r1"]


def test_a_stored_revision_is_never_replaced(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(minutes=10)
    _drop_rev(inbox, t, 1, display_doc(t, cells=[]))
    cd.ingest(inbox, store, now=NOW)
    first = store.path(frame_stamp(t), 1).read_bytes()
    _drop_rev(inbox, t, 1, display_doc(t))
    assert cd.ingest(inbox, store, now=NOW).rejected == [f"{frame_stamp(t)}.r1"]
    assert store.path(frame_stamp(t), 1).read_bytes() == first


def test_purge_removes_every_revision_of_an_expired_frame(tmp_path):
    inbox, store = tmp_path / "inbox", cd.DisplayStore(tmp_path / "store")
    t = NOW - timedelta(hours=23)
    _drop(inbox, t)
    _drop_rev(inbox, t, 1, display_doc(t))
    cd.ingest(inbox, store, now=NOW)
    assert store.purge(now=NOW + timedelta(hours=2)) == 2
    assert store.list() == []


def test_display_keys():
    assert cd.parse_key("20261004T1200") == ("20261004T1200", None)
    assert cd.parse_key("20261004T1200.r1") == ("20261004T1200", 1)
    assert cd.parse_key("20261004T1200.r0") == ("20261004T1200", 0)
    for bad in ("20261004T1200.r", "20261004T1200.rx", "x.r1", "20261004T1200.r1.json",
                "20261004T1200.r01"):
        with pytest.raises(ValueError):
            cd.parse_key(bad)


def test_one_file_name_per_revision():
    assert cd.name_of("20261004T1200.json.gz") == ("20261004T1200", 0)
    assert cd.name_of("20261004T1200.r1.json.gz") == ("20261004T1200", 1)
    for bad in ("20261004T1200.r0.json.gz", "20261004T1200.r01.json.gz"):
        assert cd.name_of(bad) is None


# --- Suspect outlines (#702) --------------------------------------------------------


def _marked_doc():
    near = [[50.0, 1.0], [50.4, 1.2], [50.2, 1.6], [50.0, 1.0]]
    far = [[45.0, 10.0], [45.1, 10.1], [45.0, 10.2], [45.0, 10.0]]
    doc = display_doc(NOW, outlines={"rain20": [far, near, far], "core35": []})
    doc["suspect_outlines"] = {"rain20": [1, 2]}
    return doc, near


def test_suspect_outlines_validate():
    doc, _ = _marked_doc()
    data = cd.validate(display_bytes(doc), frame_stamp(NOW))
    assert cd.suspect_outlines(data) == {"rain20": {1, 2}}


@pytest.mark.parametrize("marks", [
    {"rain20": [3]}, {"rain20": [-1]}, {"rain20": ["1"]}, {"rain20": [True]},
    {"core41": [0]}, {"rain20": 1}, [1],
])
def test_a_suspect_mark_that_points_nowhere_is_refused(marks):
    doc, _ = _marked_doc()
    doc["suspect_outlines"] = marks
    with pytest.raises(cd.InvalidDisplay, match="suspect_outlines"):
        cd.validate(display_bytes(doc), frame_stamp(NOW))


def test_a_file_from_before_702_has_no_marks():
    assert cd.suspect_outlines(display_doc(NOW)) == {}


def test_bbox_carries_the_marks_with_their_rings():
    doc, near = _marked_doc()
    out = cd.filter_bbox(doc, 49.8, 0.4, 51.2, 2.9)
    assert out["outlines"]["rain20"] == [near]
    assert out["suspect_outlines"] == {"rain20": [0]}
    out = cd.filter_bbox(doc, 44.9, 9.9, 45.2, 10.3)
    assert len(out["outlines"]["rain20"]) == 2
    assert out["suspect_outlines"] == {"rain20": [1]}
    # A box holding no marked ring drops the block rather than keep stale indices.
    out = cd.filter_bbox(doc, 30.0, -20.0, 31.0, -19.0)
    assert "suspect_outlines" not in out
