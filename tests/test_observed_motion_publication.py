"""Publication races exercise real files; no shared pack data or application boot."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest


@pytest.fixture
def publication():
    return importlib.import_module("weatherbrief.storage.observed_motion")


@pytest.fixture
def pack(tmp_path):
    directory = tmp_path / "pack"
    directory.mkdir()
    (directory / "briefing.json").write_text(json.dumps({
        "route": {"name": "route", "waypoints": [
            {"icao": "AAAA", "name": "Origin", "lat": 51.0, "lon": -1.0},
            {"icao": "BBBB", "name": "Destination", "lat": 52.0, "lon": 0.0},
        ], "flight_duration_hours": 1},
        "target_date": "2026-09-05",
        "fetch_date": "2026-09-05",
        "days_out": 0,
        "departure_time": "2026-09-05T12:00:00Z",
        "unknown_root": {"retain": [1, 2, 3]},
    }))
    return directory


def test_revision_reservation_is_durable_and_monotonic(publication, pack):
    first = publication.reserve_motion_revision(pack)
    second = publication.reserve_motion_revision(pack)
    assert first.revision == 1
    assert second.revision == 2
    assert first.generation == second.generation
    assert first.pack_dir == pack.resolve()
    assert json.loads((pack / "briefing.json").read_text())["unknown_root"] == {
        "retain": [1, 2, 3],
    }


def test_refresh_cannot_create_missing_pack(publication, tmp_path):
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("writer", ["artifacts", "legacy"])
def test_full_wrapper_first_creation_and_deleted_pack_fence(publication, pack, tmp_path, writer):
    from weatherbrief.models import ForecastSnapshot
    from weatherbrief.storage.snapshots import save_snapshot
    from weatherbrief.tasks.artifacts import save_analysis_artifacts

    snapshot = ForecastSnapshot.model_validate(json.loads((pack / "briefing.json").read_text()))
    target = tmp_path / "fresh" if writer == "artifacts" else tmp_path / "forecasts/2026-09-05/d-0_2026-09-05"
    def save():
        if writer == "artifacts":
            save_analysis_artifacts(target, snapshot, None)
        else:
            save_snapshot(snapshot, tmp_path)
    save()
    assert json.loads((target / "briefing.json").read_text())["observed_motion"] is None
    old = publication.reserve_motion_revision(target)
    publication.delete_motion_pack(target)
    with pytest.raises(publication.MotionPublicationError):
        save()
    assert not target.exists()
    # Explicit recreation remains owned by the full-writer reservation; late
    # tokens cannot enter this generation, and the durable counter advances.
    recreated = publication.reserve_motion_revision(target, allow_create=True)
    assert recreated.revision > old.revision
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(target, old, None, refreshed_fields={})


@pytest.mark.parametrize("writer", ["artifacts", "legacy"])
def test_full_wrapper_null_write_preserves_newer_motion_and_raw_fields(publication, pack, tmp_path, writer):
    from weatherbrief.models import ForecastSnapshot
    from weatherbrief.models.observed_motion import empty_motion
    from weatherbrief.storage.snapshots import save_snapshot
    from weatherbrief.tasks.artifacts import save_analysis_artifacts

    snapshot = ForecastSnapshot.model_validate(json.loads((pack / "briefing.json").read_text()))
    target = tmp_path / "fresh" if writer == "artifacts" else tmp_path / "forecasts/2026-09-05/d-0_2026-09-05"
    if writer == "artifacts":
        save_analysis_artifacts(target, snapshot, None)
    else:
        save_snapshot(snapshot, tmp_path)
    token = publication.reserve_motion_revision(target)
    motion = empty_motion(route_geometry_id="route", planned_timing_id=None, cutoff_at=datetime(2026, 9, 5, 12, tzinfo=timezone.utc), revision=token.revision, status="unavailable", reason_codes=["missing_source"])
    publication.publish_motion_snapshot(target, token, motion, refreshed_fields={"unknown_root": {"keep": True}})
    if writer == "artifacts":
        save_analysis_artifacts(target, snapshot, None)
    else:
        save_snapshot(snapshot, tmp_path)
    stored = json.loads((target / "briefing.json").read_text())
    assert stored["observed_motion"]["revision"] == token.revision
    assert stored["unknown_root"] == {"keep": True}


@pytest.mark.parametrize("writer", ["artifacts", "legacy"])
def test_full_wrapper_delayed_serialization_cannot_cross_deletion(publication, pack, tmp_path, monkeypatch, writer):
    from weatherbrief.models import ForecastSnapshot
    from weatherbrief.storage.snapshots import save_snapshot
    from weatherbrief.tasks.artifacts import save_analysis_artifacts

    snapshot = ForecastSnapshot.model_validate(json.loads((pack / "briefing.json").read_text()))
    target = tmp_path / "race" if writer == "artifacts" else tmp_path / "forecasts/2026-09-05/d-0_2026-09-05"
    def save():
        if writer == "artifacts":
            save_analysis_artifacts(target, snapshot, None)
        else:
            save_snapshot(snapshot, tmp_path)
    save()
    old_revision = publication.reserve_motion_revision(target).revision
    serialization_started, deletion_started = Event(), Event()
    original_dump = ForecastSnapshot.model_dump
    def delayed_dump(self, **kwargs):
        serialization_started.set()
        assert deletion_started.wait(5)
        return original_dump(self, **kwargs)
    monkeypatch.setattr(ForecastSnapshot, "model_dump", delayed_dump)
    def delete():
        assert serialization_started.wait(5)
        deletion_started.set()
        publication.delete_motion_pack(target)
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer_future = executor.submit(save)
        deleter_future = executor.submit(delete)
        # Valid serialization and deletion are both completed operations, not a
        # swallowed missing-directory error or a resurrected partial artifact set.
        writer_future.result(timeout=5)
        deleter_future.result(timeout=5)
    assert not target.exists()
    recreated = publication.reserve_motion_revision(target, allow_create=True)
    assert recreated.revision > old_revision


@pytest.mark.parametrize("writer", ["artifacts", "legacy"])
def test_full_wrapper_first_briefing_uses_atomic_publication(publication, pack, tmp_path, monkeypatch, writer):
    from weatherbrief.models import ForecastSnapshot
    from weatherbrief.storage.snapshots import save_snapshot
    from weatherbrief.tasks.artifacts import save_analysis_artifacts

    snapshot = ForecastSnapshot.model_validate(json.loads((pack / "briefing.json").read_text()))
    target = tmp_path / "atomic" if writer == "artifacts" else tmp_path / "forecasts/2026-09-05/d-0_2026-09-05"
    original = publication.os.replace
    def failed_replace(source, destination):
        if str(destination).endswith("/briefing.json"):
            raise OSError("injected publication replacement failure")
        return original(source, destination)
    monkeypatch.setattr(publication.os, "replace", failed_replace)
    with pytest.raises(publication.MotionPublicationError):
        if writer == "artifacts":
            save_analysis_artifacts(target, snapshot, None)
        else:
            save_snapshot(snapshot, tmp_path)
    assert not (target / "briefing.json").exists()
    assert not target.exists()


def test_deletion_preserves_high_water_and_fences_old_generation(publication, tmp_path):
    pack = tmp_path / "pack"
    first = publication.reserve_motion_revision(pack, allow_create=True)
    publication.delete_motion_pack(pack)
    second = publication.reserve_motion_revision(pack, allow_create=True)
    assert second.revision > first.revision
    assert second.generation != first.generation
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(
            pack, first, None, refreshed_fields={}, initial_snapshot={},
        )


def test_concurrent_reservations_use_one_stable_lock(publication, pack):
    barrier = Barrier(8)

    def reserve(_):
        barrier.wait(timeout=5)
        return publication.reserve_motion_revision(pack).revision

    with ThreadPoolExecutor(max_workers=8) as executor:
        revisions = list(executor.map(reserve, range(8)))
    assert sorted(revisions) == list(range(1, 9))
    locks = list(pack.parent.glob("observed-motion-lock-*.lock"))
    assert len(locks) == 1
    inode = locks[0].stat().st_ino
    publication.reserve_motion_revision(pack)
    publication.delete_motion_pack(pack)
    publication.reserve_motion_revision(pack, allow_create=True)
    assert locks[0].stat().st_ino == inode


def test_lost_control_refuses_counter_reset(publication, pack):
    publication.reserve_motion_revision(pack)
    control, = pack.parent.glob("observed-motion-state-*.json")
    control.unlink()
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack, allow_create=True)


@pytest.mark.parametrize("corrupt", ["{", "[]", '{"high_water": 0}'])
def test_corrupt_control_refuses_counter_reset(publication, pack, corrupt):
    publication.reserve_motion_revision(pack)
    control, = pack.parent.glob("observed-motion-state-*.json")
    control.write_text(corrupt)
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack, allow_create=True)


def test_corrupt_snapshot_refuses_initialization(publication, pack):
    (pack / "briefing.json").write_text("{")
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack, allow_create=True)


def test_safe_integer_exhaustion_is_not_wraparound(publication, pack):
    publication.reserve_motion_revision(pack)
    control, = pack.parent.glob("observed-motion-state-*.json")
    state = json.loads(control.read_text())
    state["high_water"] = 9007199254740991
    control.write_text(json.dumps(state))
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack)
    assert json.loads(control.read_text())["high_water"] == 9007199254740991


def empty(revision, *, status="unavailable", route="geometry-a", timing="timing-a"):
    from weatherbrief.models.observed_motion import empty_motion

    return empty_motion(
        route_geometry_id=route, planned_timing_id=timing,
        cutoff_at=datetime(2026, 9, 5, 12, tzinfo=timezone.utc), revision=revision,
        status=status, reason_codes=["disabled" if status == "disabled" else "source_missing"],
    )


def read(pack):
    return json.loads((pack / "briefing.json").read_text())


def test_superseded_body_is_not_inspected_or_returned(publication, pack):
    old = publication.reserve_motion_revision(pack)
    publication.reserve_motion_revision(pack)
    result = publication.publish_motion_snapshot(pack, old, None, refreshed_fields={"unknown_root": "old"})
    assert result == read(pack)
    assert result["unknown_root"] == {"retain": [1, 2, 3]}


def test_superseded_pending_creation_has_no_snapshot_to_return(publication, tmp_path):
    pack = tmp_path / "pack"
    old = publication.reserve_motion_revision(pack, allow_create=True)
    publication.reserve_motion_revision(pack, allow_create=True)
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, old, None, refreshed_fields={}, initial_snapshot={})
    assert not (pack / "briefing.json").exists()


def test_legacy_writer_merges_unknown_fields_atomically(publication, pack):
    original = read(pack)
    result = publication.write_snapshot_atomic(pack, {"analyses": ["new"]})
    assert result == {**original, "analyses": ["new"]}
    assert read(pack) == result


def test_legacy_writer_cannot_create_or_resurrect(publication, pack, tmp_path):
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(tmp_path / "missing", {})
    publication.delete_motion_pack(pack)
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(pack, {"analyses": ["old"]})
    assert not pack.exists()


def test_wrong_path_token_is_rejected_before_body(publication, pack, tmp_path):
    token = publication.reserve_motion_revision(pack)
    other = tmp_path / "other"
    other.mkdir()
    (other / "briefing.json").write_text((pack / "briefing.json").read_text())
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(other, token, None, refreshed_fields={})


def test_changed_route_fences_computation(publication, pack):
    token = publication.reserve_motion_revision(pack)
    changed = read(pack)
    changed["route"]["waypoints"][0]["lon"] = 5
    (pack / "briefing.json").write_text(json.dumps(changed))
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, None, refreshed_fields={})


def test_legacy_writer_refuses_identity_contradiction(publication, pack):
    original = read(pack)
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(pack, {"departure_time": "2026-09-05T13:00:00Z"})
    assert read(pack) == original


def test_failed_atomic_replace_preserves_previous_json(publication, pack, monkeypatch):
    publication.reserve_motion_revision(pack)
    previous = (pack / "briefing.json").read_bytes()
    replace = publication.os.replace

    def disk_failure(source, destination):
        if destination == pack / "briefing.json":
            raise OSError("injected disk replacement failure")
        return replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", disk_failure)
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(pack, {"analyses": ["new"]})
    assert (pack / "briefing.json").read_bytes() == previous
    assert not list(pack.glob(".briefing.json.*"))


def test_snapshot_readers_never_observe_partial_json(publication, pack):
    barrier = Barrier(2)

    def writer():
        barrier.wait(timeout=5)
        for index in range(60):
            publication.write_snapshot_atomic(pack, {"large": [index] * 500})

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(writer)
        barrier.wait(timeout=5)
        reads = 0
        while not future.done():
            snapshot = read(pack)
            assert snapshot["unknown_root"] == {"retain": [1, 2, 3]}
            if "large" in snapshot:
                assert len(snapshot["large"]) == 500
                assert len(set(snapshot["large"])) == 1
            reads += 1
        future.result(timeout=5)
    assert reads > 0


def test_latest_failure_publishes_and_older_attempt_cannot_restore_it(publication, pack):
    old = publication.reserve_motion_revision(pack)
    newest = publication.reserve_motion_revision(pack)
    result = publication.publish_motion_snapshot(
        pack, newest, empty(newest.revision), refreshed_fields={"observations": "fresh"},
    )
    assert result["observed_motion"]["revision"] == 2
    assert result["observed_motion"]["status"] == "unavailable"
    assert result["unknown_root"] == {"retain": [1, 2, 3]}
    assert result["observations"] == "fresh"
    assert publication.publish_motion_snapshot(
        pack, old, None, refreshed_fields={"observations": "stale"},
    ) == result
    assert read(pack) == result


def test_same_revision_identical_is_idempotent_conflict_is_error(publication, pack):
    token = publication.reserve_motion_revision(pack)
    motion = empty(token.revision)
    first = publication.publish_motion_snapshot(pack, token, motion, refreshed_fields={})
    assert publication.publish_motion_snapshot(pack, token, motion, refreshed_fields={}) == first
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, empty(token.revision, status="disabled"), refreshed_fields={})
    assert read(pack) == first


def test_explicit_initial_full_writer_publishes_complete_base(publication, pack, tmp_path):
    base = read(pack)
    new_pack = tmp_path / "new"
    token = publication.reserve_motion_revision(new_pack, allow_create=True)
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(new_pack)
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(new_pack, token, empty(token.revision), refreshed_fields={})
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(new_pack, token, empty(token.revision), refreshed_fields={}, initial_snapshot={})
    result = publication.publish_motion_snapshot(
        new_pack, token, empty(token.revision), refreshed_fields={"observations": "fresh"}, initial_snapshot=base,
    )
    assert result["route"] == base["route"]
    assert result["unknown_root"] == {"retain": [1, 2, 3]}
    assert read(new_pack) == result
    assert publication.reserve_motion_revision(new_pack).revision == 2


def test_legacy_full_writer_cannot_restore_old_motion_or_strip_unknown(publication, pack):
    first_token = publication.reserve_motion_revision(pack)
    first = publication.publish_motion_snapshot(pack, first_token, empty(1), refreshed_fields={})
    new_token = publication.reserve_motion_revision(pack)
    current = publication.publish_motion_snapshot(pack, new_token, empty(2, status="disabled"), refreshed_fields={})
    stale = {**first, "analyses": ["updated analysis"]}
    stale.pop("unknown_root")
    result = publication.write_snapshot_atomic(pack, stale)
    assert result["observed_motion"] == current["observed_motion"]
    assert result["unknown_root"] == {"retain": [1, 2, 3]}
    assert result["analyses"] == ["updated analysis"]
    assert publication.write_snapshot_atomic(pack, {"observed_motion": None})["observed_motion"] == current["observed_motion"]


def test_full_writer_rejects_same_revision_conflict_and_unreserved_motion(publication, pack):
    token = publication.reserve_motion_revision(pack)
    current = publication.publish_motion_snapshot(pack, token, empty(1), refreshed_fields={})
    for value in [empty(1, status="disabled"), empty(2)]:
        with pytest.raises(publication.MotionPublicationError):
            publication.write_snapshot_atomic(pack, {"observed_motion": value.model_dump(mode="json")})
    assert read(pack) == current


@pytest.mark.parametrize("changed", ["route", "timing", "revision"])
def test_current_publication_rejects_envelope_identity_and_revision_mismatch(publication, pack, changed):
    initial = publication.reserve_motion_revision(pack)
    publication.publish_motion_snapshot(pack, initial, empty(1), refreshed_fields={})
    token = publication.reserve_motion_revision(pack)
    kwargs = {"route": "other"} if changed == "route" else {"timing": None} if changed == "timing" else {}
    invalid = empty(3 if changed == "revision" else 2, **kwargs)
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, invalid, refreshed_fields={})
    assert read(pack)["observed_motion"]["revision"] == 1


def test_refreshed_fields_cannot_smuggle_motion_or_identity(publication, pack):
    token = publication.reserve_motion_revision(pack)
    for fields in [{"observed_motion": None}, {"departure_time": "other"}]:
        with pytest.raises(publication.MotionPublicationError):
            publication.publish_motion_snapshot(pack, token, empty(1), refreshed_fields=fields)
    assert "observed_motion" not in read(pack)


def test_existing_motion_initializes_counter_without_reset(publication, pack):
    raw = read(pack)
    raw["observed_motion"] = empty(41).model_dump(mode="json")
    (pack / "briefing.json").write_text(json.dumps(raw))
    assert publication.reserve_motion_revision(pack).revision == 42


def test_legacy_snapshot_can_be_updated_without_losing_unknown_data(publication, pack):
    (pack / "briefing.json").rename(pack / "snapshot.json")
    token = publication.reserve_motion_revision(pack)
    result = publication.publish_motion_snapshot(pack, token, empty(1), refreshed_fields={})
    assert result["unknown_root"] == {"retain": [1, 2, 3]}
    assert read(pack) == result


def test_external_directory_replacement_fences_old_token(publication, pack, tmp_path):
    token = publication.reserve_motion_revision(pack)
    original = read(pack)
    pack.rename(tmp_path / "old-pack")
    pack.mkdir()
    (pack / "briefing.json").write_text(json.dumps(original))
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, None, refreshed_fields={})
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack, allow_create=True)


def test_concurrent_publications_cannot_replace_newer_attempt(publication, pack):
    old = publication.reserve_motion_revision(pack)
    new = publication.reserve_motion_revision(pack)
    motions = [empty(old.revision), empty(new.revision, status="disabled")]
    barrier = Barrier(2)

    def publish(index):
        barrier.wait(timeout=5)
        return publication.publish_motion_snapshot(pack, [old, new][index], motions[index], refreshed_fields={})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))
    assert read(pack)["observed_motion"]["revision"] == 2
    assert read(pack)["observed_motion"]["status"] == "disabled"
    assert results[0].get("observed_motion", {}).get("revision") != 1


@pytest.mark.parametrize("field,value", [
    ("generation", ""), ("pending_revision", "1"),
    ("pending_revision", 9007199254740991), ("directory_identity", "not-an-inode"),
    ("snapshot_identity", []), ("snapshot_identity", None), ("active", 1), ("version", True),
])
def test_invalid_lifecycle_state_is_not_repaired(publication, pack, field, value):
    publication.reserve_motion_revision(pack)
    control, = pack.parent.glob("observed-motion-state-*.json")
    state = json.loads(control.read_text())
    state[field] = value
    control.write_text(json.dumps(state))
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack)


def test_duplicate_json_keys_are_corruption_not_last_value_wins(publication, pack):
    publication.reserve_motion_revision(pack)
    control, = pack.parent.glob("observed-motion-state-*.json")
    content = control.read_text()
    control.write_text(content[:-1] + ', "high_water": 0}')
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack)


def _reserve_in_process(pack, barrier):
    from weatherbrief.storage.observed_motion import reserve_motion_revision

    barrier.wait(timeout=5)
    for _ in range(10):
        reserve_motion_revision(pack)


def test_processes_share_durable_reservation_lock(publication, pack):
    import multiprocessing

    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(3)
    processes = [context.Process(target=_reserve_in_process, args=(pack, barrier)) for _ in range(3)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    assert publication.reserve_motion_revision(pack).revision == 31


def test_removed_modern_snapshot_cannot_fall_back_to_stale_legacy(publication, pack):
    (pack / "snapshot.json").write_text((pack / "briefing.json").read_text())
    publication.reserve_motion_revision(pack)
    (pack / "briefing.json").unlink()
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack, allow_create=True)
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(pack, {"analyses": []})


def test_invalid_snapshot_identity_cannot_bootstrap_control(publication, pack):
    (pack / "briefing.json").write_text('{"observations": []}')
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack)


@pytest.mark.parametrize("envelope", [
    {"revision": 1},
    {"revision": True, "route_geometry_id": "a", "planned_timing_id": None},
    {"revision": 1, "route_geometry_id": "", "planned_timing_id": None},
    {"revision": 1, "route_geometry_id": "a", "planned_timing_id": 7},
])
def test_existing_invalid_motion_identity_cannot_initialize(publication, pack, envelope):
    raw = read(pack)
    raw["observed_motion"] = envelope
    (pack / "briefing.json").write_text(json.dumps(raw))
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack)


def test_new_failure_removes_previously_published_accepted_geometry(publication, pack):
    from observed.motion_fixtures import available_motion_dict
    from weatherbrief.models.observed_motion import ObservedMotion

    token = publication.reserve_motion_revision(pack)
    raw = available_motion_dict()
    raw.update(route_geometry_id="geometry-a", planned_timing_id="timing-a")
    success = ObservedMotion.model_validate(raw)
    publication.publish_motion_snapshot(pack, token, success, refreshed_fields={})
    assert read(pack)["observed_motion"]["features"]
    newer = publication.reserve_motion_revision(pack)
    result = publication.publish_motion_snapshot(pack, newer, empty(2), refreshed_fields={})
    assert result["observed_motion"]["features"] == []
    assert result["observed_motion"]["projection_times"] == []
    assert publication.publish_motion_snapshot(pack, token, success, refreshed_fields={}) == result


def test_current_none_is_error_not_preserve_old_motion(publication, pack):
    token = publication.reserve_motion_revision(pack)
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, None, refreshed_fields={})
    assert "observed_motion" not in read(pack)


def test_initial_state_write_crash_recovers_from_complete_atomic_snapshot(publication, pack, tmp_path, monkeypatch):
    base = read(pack)
    new_pack = tmp_path / "new"
    token = publication.reserve_motion_revision(new_pack, allow_create=True)
    motion = empty(1)
    replace = publication.os.replace

    def control_failure(source, destination):
        if destination.name.startswith("observed-motion-state-"):
            raise OSError("control write interrupted after snapshot committed")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(publication.os, "replace", control_failure)
        with pytest.raises(publication.MotionPublicationError):
            publication.publish_motion_snapshot(new_pack, token, motion, refreshed_fields={}, initial_snapshot=base)
    assert read(new_pack)["observed_motion"]["revision"] == 1
    token2 = publication.reserve_motion_revision(new_pack)
    assert token2.revision == 2
    publication.publish_motion_snapshot(new_pack, token2, empty(2), refreshed_fields={})
    changed = read(new_pack)
    changed["departure_time"] = "2026-09-05T13:00:00Z"
    (new_pack / "briefing.json").write_text(json.dumps(changed))
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(new_pack)


def test_motion_dto_is_revalidated_after_mutation(publication, pack):
    token = publication.reserve_motion_revision(pack)
    motion = empty(1).model_copy(update={"revision": 0})
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, motion, refreshed_fields={})


def test_first_motion_must_match_declared_snapshot_identity(publication, pack):
    base = read(pack)
    base["route_geometry_id"] = "snapshot-geometry"
    (pack / "briefing.json").write_text(json.dumps(base))
    token = publication.reserve_motion_revision(pack)
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, token, empty(1), refreshed_fields={})


def test_full_writer_cannot_publish_even_a_reserved_uncommitted_motion(publication, pack):
    token = publication.reserve_motion_revision(pack)
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(pack, {"observed_motion": empty(token.revision).model_dump(mode="json")})


def test_unknown_motion_fields_survive_legacy_full_writes(publication, pack):
    from weatherbrief.models.observed_motion import ObservedMotion

    token = publication.reserve_motion_revision(pack)
    raw = empty(token.revision).model_dump(mode="json")
    raw["future_extension"] = {"opaque_key": [1, 2, 3]}
    motion = ObservedMotion.model_validate(raw)
    publication.publish_motion_snapshot(pack, token, motion, refreshed_fields={})
    result = publication.write_snapshot_atomic(pack, {"analyses": ["new"]})
    assert result["observed_motion"]["future_extension"] == {"opaque_key": [1, 2, 3]}


def test_forged_unreserved_token_cannot_publish(publication, pack):
    from dataclasses import replace

    token = publication.reserve_motion_revision(pack)
    unreserved = replace(token, revision=token.revision + 1)
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(pack, unreserved, empty(unreserved.revision), refreshed_fields={})
    assert "observed_motion" not in read(pack)


def test_old_full_writer_with_conflicting_identity_is_rejected(publication, pack):
    first = publication.reserve_motion_revision(pack)
    publication.publish_motion_snapshot(pack, first, empty(1), refreshed_fields={})
    newest = publication.reserve_motion_revision(pack)
    publication.publish_motion_snapshot(pack, newest, empty(2), refreshed_fields={})
    old_wrong_route = empty(1, route="different-geometry").model_dump(mode="json")
    with pytest.raises(publication.MotionPublicationError):
        publication.write_snapshot_atomic(pack, {"observed_motion": old_wrong_route})
    assert read(pack)["observed_motion"]["revision"] == 2


def test_existing_root_and_motion_identity_contradiction_refuses_bootstrap(publication, pack):
    raw = read(pack)
    raw["route_geometry_id"] = "snapshot-route"
    raw["observed_motion"] = empty(1).model_dump(mode="json")
    (pack / "briefing.json").write_text(json.dumps(raw))
    with pytest.raises(publication.MotionPublicationError):
        publication.reserve_motion_revision(pack)


def test_first_publication_requires_complete_forecast_snapshot(publication, pack, tmp_path):
    new_pack = tmp_path / "new"
    token = publication.reserve_motion_revision(new_pack, allow_create=True)
    incomplete = read(pack)
    incomplete.pop("fetch_date")
    with pytest.raises(publication.MotionPublicationError):
        publication.publish_motion_snapshot(new_pack, token, empty(1), refreshed_fields={}, initial_snapshot=incomplete)
    assert not (new_pack / "briefing.json").exists()


@pytest.mark.parametrize("writer", ["tokened", "full"])
@pytest.mark.parametrize("number,boolean", [(1, True), (0, False), (1.0, True)])
def test_same_revision_nested_boolean_number_collision_is_conflict(publication, pack, writer, number, boolean):
    from weatherbrief.models.observed_motion import ObservedMotion

    token = publication.reserve_motion_revision(pack)
    raw = empty(token.revision).model_dump(mode="json")
    raw["future_extension"] = {"nested": [{"value": number}]}
    current = publication.publish_motion_snapshot(
        pack, token, ObservedMotion.model_validate(raw), refreshed_fields={},
    )
    previous_bytes = (pack / "briefing.json").read_bytes()
    conflicting = {**raw, "future_extension": {"nested": [{"value": boolean}]}}
    with pytest.raises(publication.MotionPublicationError, match="Conflicting motion content"):
        if writer == "tokened":
            publication.publish_motion_snapshot(
                pack, token, ObservedMotion.model_validate(conflicting), refreshed_fields={},
            )
        else:
            publication.write_snapshot_atomic(pack, {"observed_motion": conflicting, "analyses": ["stale"]})
    assert (pack / "briefing.json").read_bytes() == previous_bytes
    assert read(pack) == current


@pytest.mark.parametrize("writer", ["tokened", "full"])
def test_same_revision_nested_object_key_order_is_idempotent(publication, pack, writer):
    from weatherbrief.models.observed_motion import ObservedMotion

    token = publication.reserve_motion_revision(pack)
    raw = empty(token.revision).model_dump(mode="json")
    raw["future_extension"] = {"nested": [{"number": 1, "boolean": True}], "keep": None}
    current = publication.publish_motion_snapshot(
        pack, token, ObservedMotion.model_validate(raw), refreshed_fields={},
    )
    reordered = dict(reversed(list(raw.items())))
    reordered["future_extension"] = {"keep": None, "nested": [{"boolean": True, "number": 1}]}
    if writer == "tokened":
        result = publication.publish_motion_snapshot(
            pack, token, ObservedMotion.model_validate(reordered), refreshed_fields={},
        )
    else:
        result = publication.write_snapshot_atomic(pack, {"observed_motion": reordered})
    assert result == current
    assert read(pack) == current
