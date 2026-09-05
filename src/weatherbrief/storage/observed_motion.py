"""Generation-fenced, atomic observed-motion publication for one pack identity.

Every snapshot writer and pack deleter must use this module. The parent directory
owns the stable lock and durable counter; neither may be removed with a pack.
Computation belongs outside these short filesystem transactions.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from weatherbrief.models.observed_motion import ObservedMotion

MAX_REVISION = 9007199254740991
_IDENTITY_FIELDS = ("route", "target_date", "departure_time", "route_geometry_id", "planned_timing_id")


class MotionPublicationError(RuntimeError):
    """Publication cannot safely identify, order, or persist the requested pack."""


@dataclass(frozen=True)
class MotionPublicationToken:
    pack_dir: Path
    generation: str
    revision: int


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MotionPublicationError("Duplicate JSON key in persisted state")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise MotionPublicationError(f"Non-finite JSON number: {value}")


def _read_json(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    value = json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise MotionPublicationError(f"Expected JSON object: {path.name}")
    return value


def _read_snapshot(pack: Path, state: dict | None) -> dict | None:
    current = _read_json(pack / "briefing.json")
    if state is not None and state["snapshot_file"] == "briefing.json":
        return current
    return current if current is not None else _read_json(pack / "snapshot.json")


def _atomic_json(path: Path, value: dict) -> None:
    """Flush a sibling temporary file before replace; readers see whole JSON."""
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _identity(snapshot: dict) -> dict:
    if not isinstance(snapshot.get("route"), dict) or not snapshot.get("target_date"):
        raise MotionPublicationError("Snapshot must identify its route and target date")
    return {key: snapshot[key] for key in _IDENTITY_FIELDS if key in snapshot}


def _revision(value: object, *, zero: bool = False) -> int:
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_REVISION:
        raise MotionPublicationError("Invalid or exhausted safe-integer revision")
    return value


def _snapshot_revision(snapshot: dict | None) -> int:
    motion = (snapshot or {}).get("observed_motion")
    if motion is None:
        return 0
    if not isinstance(motion, dict):
        raise MotionPublicationError("Invalid existing motion envelope")
    if (not isinstance(motion.get("route_geometry_id"), str) or not motion["route_geometry_id"]
            or "planned_timing_id" not in motion
            or (motion["planned_timing_id"] is not None
                and (not isinstance(motion["planned_timing_id"], str) or not motion["planned_timing_id"]))):
        raise MotionPublicationError("Invalid existing motion identity")
    for key in ("route_geometry_id", "planned_timing_id"):
        if key in snapshot and snapshot[key] != motion[key]:
            raise MotionPublicationError("Existing motion contradicts snapshot identity")
    return _revision(motion.get("revision"))


def _directory_identity(pack: Path) -> list[int]:
    stat = pack.stat()
    if not pack.is_dir():
        raise MotionPublicationError("Pack is not a directory")
    return [stat.st_dev, stat.st_ino]


@contextmanager
def _locked(pack_dir: Path):
    try:
        pack = Path(pack_dir).resolve()
        if pack == pack.parent or not pack.parent.is_dir():
            raise MotionPublicationError("Pack parent must already exist")
        key = hashlib.sha256(str(pack).encode()).hexdigest()
        control = pack.parent / f"observed-motion-state-{key}.json"
        lock = pack.parent / f"observed-motion-lock-{key}.lock"
        with lock.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                state = _read_json(control)
                stream.seek(0)
                if state is None and stream.read():
                    raise MotionPublicationError("Publication high-water state was lost; identity cannot be reused")
                if state is not None:
                    if type(state.get("version")) is not int or state["version"] != 1 or state.get("pack_dir") != str(pack):
                        raise MotionPublicationError("Invalid publication control identity")
                    _revision(state.get("high_water"), zero=True)
                    if (not isinstance(state.get("generation"), str) or not state["generation"]
                            or type(state.get("active")) is not bool
                            or "pending_revision" not in state
                            or "directory_identity" not in state
                            or "snapshot_identity" not in state
                            or state.get("snapshot_file") not in {"briefing.json", "snapshot.json"}):
                        raise MotionPublicationError("Corrupt publication lifecycle state")
                    pending = state["pending_revision"]
                    if pending is not None:
                        if (_revision(pending) != state["high_water"] or not state["active"]
                                or state["snapshot_identity"] is not None):
                            raise MotionPublicationError("Invalid pending creation owner")
                    directory_identity = state["directory_identity"]
                    if (not isinstance(directory_identity, list) or len(directory_identity) != 2
                            or any(type(value) is not int or value < 0 for value in directory_identity)
                            or (state["snapshot_identity"] is not None
                                and not isinstance(state["snapshot_identity"], dict))):
                        raise MotionPublicationError("Invalid stored pack identity")
                    if state["active"] and pending is None and state["snapshot_identity"] is None:
                        raise MotionPublicationError("Active pack lost its snapshot identity")

                def save(updated: dict) -> None:
                    _atomic_json(control, updated)
                    stream.seek(0)
                    if not stream.read():
                        stream.write(b"initialized\n")
                        stream.flush()
                        os.fsync(stream.fileno())

                yield pack, state, save
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError, TypeError) as exc:
        raise MotionPublicationError("Motion publication storage failure") from exc


def _new_state(pack: Path, snapshot: dict | None, high_water: int = 0) -> dict:
    return {
        "version": 1, "pack_dir": str(pack), "generation": uuid.uuid4().hex,
        "high_water": max(high_water, _snapshot_revision(snapshot)), "active": True,
        "pending_revision": None, "directory_identity": _directory_identity(pack),
        "snapshot_identity": _identity(snapshot) if snapshot is not None else None,
        "snapshot_file": "snapshot.json" if snapshot is not None and not (pack / "briefing.json").exists() else "briefing.json",
    }


def _check_current(pack: Path, state: dict, snapshot: dict | None) -> bool:
    if not state["active"] or not pack.is_dir():
        raise MotionPublicationError("Pack generation was deleted")
    if _directory_identity(pack) != state["directory_identity"]:
        raise MotionPublicationError("Pack directory was replaced outside its lifecycle")
    if snapshot is None:
        if state["pending_revision"] is None:
            raise MotionPublicationError("Current snapshot was removed")
    elif state["snapshot_identity"] is not None and _identity(snapshot) != state["snapshot_identity"]:
        raise MotionPublicationError("Snapshot route/timing identity changed")
    if _snapshot_revision(snapshot) > state["high_water"]:
        raise MotionPublicationError("Snapshot revision contradicts durable high-water")
    recovered = False
    if snapshot is not None and state["pending_revision"] is not None:
        # The snapshot may have committed just before a crash updating control.
        if _snapshot_revision(snapshot) != state["pending_revision"]:
            raise MotionPublicationError("Initial snapshot does not belong to pending owner")
        state["snapshot_identity"] = _identity(snapshot)
        state["pending_revision"] = None
        recovered = True
    if snapshot is not None and (pack / "briefing.json").exists() and state["snapshot_file"] != "briefing.json":
        state["snapshot_file"] = "briefing.json"
        recovered = True
    return recovered


def _check_motion_identity(snapshot: dict, motion: dict) -> None:
    for key in ("route_geometry_id", "planned_timing_id"):
        if key in snapshot and snapshot[key] != motion[key]:
            raise MotionPublicationError("Motion contradicts declared snapshot identity")
        existing = snapshot.get("observed_motion")
        if existing is not None and existing[key] != motion[key]:
            raise MotionPublicationError("Motion route/timing identity changed within a pack")


def _validated_motion(motion: ObservedMotion) -> dict:
    from weatherbrief.models.observed_motion import ObservedMotion

    if not isinstance(motion, ObservedMotion):
        raise MotionPublicationError("Current publication requires a validated ObservedMotion")
    # model_copy/update and mutation can bypass validation on a model instance.
    return ObservedMotion.model_validate(motion.model_dump(mode="python")).model_dump(mode="json")


def reserve_motion_revision(pack_dir: Path, *, allow_create: bool = False) -> MotionPublicationToken:
    """Reserve before computation; only a full writer may authorize creation."""
    with _locked(pack_dir) as (pack, state, save):
        snapshot = _read_snapshot(pack, state)
        if state is None or not state["active"]:
            if state is not None and pack.exists():
                raise MotionPublicationError("Deleted pack directory still exists")
            if snapshot is None:
                if not allow_create:
                    raise MotionPublicationError("Refresh cannot create a pack")
                pack.mkdir(exist_ok=True)
            state = _new_state(pack, snapshot, state["high_water"] if state else 0)
        else:
            _check_current(pack, state, snapshot)
        if snapshot is None and not allow_create:
            raise MotionPublicationError("Refresh cannot acquire pending full-writer creation")
        revision = _revision(state["high_water"] + 1)
        state["high_water"] = revision
        if snapshot is None:
            state["pending_revision"] = revision
        save(state)
        return MotionPublicationToken(pack, state["generation"], revision)


def delete_motion_pack(pack_dir: Path) -> None:
    """Fence all outstanding tokens, retaining the counter and stable lock."""
    with _locked(pack_dir) as (pack, state, save):
        snapshot = _read_snapshot(pack, state)
        if state is None:
            if not pack.exists():
                return
            state = _new_state(pack, snapshot)
        elif state["active"]:
            _check_current(pack, state, snapshot)
        state.update(active=False, generation=uuid.uuid4().hex, pending_revision=None)
        save(state)
        if pack.exists():
            shutil.rmtree(pack)


def publish_motion_snapshot(
    pack_dir: Path, token: MotionPublicationToken, motion: ObservedMotion,
    *, refreshed_fields: dict, initial_snapshot: dict | None = None,
) -> dict:
    """Publish only the latest attempt of the current generation."""
    with _locked(pack_dir) as (pack, state, save):
        if (not isinstance(token, MotionPublicationToken) or token.pack_dir != pack
                or state is None or token.generation != state["generation"]):
            raise MotionPublicationError("Publication token does not belong to current pack generation")
        current = _read_snapshot(pack, state)
        recovered = _check_current(pack, state, current)
        _revision(token.revision)
        if token.revision < state["high_water"]:
            if current is None:
                raise MotionPublicationError("Superseded publication has no current snapshot")
            if recovered:
                save(state)
            return current
        if token.revision != state["high_water"]:
            raise MotionPublicationError("Publication revision was not reserved")
        if current is None:
            if state["pending_revision"] != token.revision or not isinstance(initial_snapshot, dict):
                raise MotionPublicationError("Initial publication requires its owning full-writer snapshot")
            from weatherbrief.models import ForecastSnapshot

            # Validate completeness without model-dumping away unknown JSON.
            ForecastSnapshot.model_validate(initial_snapshot)
            base = initial_snapshot
        else:
            base = current
        identity = _identity(base)
        if not isinstance(refreshed_fields, dict) or "observed_motion" in refreshed_fields:
            raise MotionPublicationError("Refreshed fields cannot supply a motion envelope")
        merged = {**base, **refreshed_fields}
        if _identity(merged) != identity:
            raise MotionPublicationError("Refresh cannot change the reserved route/timing identity")
        body = _validated_motion(motion)
        if body["revision"] != token.revision:
            raise MotionPublicationError("Motion revision does not match reserved token")
        _snapshot_revision(base)
        _check_motion_identity(base, body)
        if current is not None and _snapshot_revision(current) == token.revision:
            if current["observed_motion"] != body:
                raise MotionPublicationError("Conflicting motion content at the same revision")
            if recovered:
                save(state)
            return current
        merged["observed_motion"] = body
        _atomic_json(pack / "briefing.json", merged)
        if current is None or recovered or state["snapshot_file"] != "briefing.json":
            state.update(snapshot_identity=identity, pending_revision=None, snapshot_file="briefing.json")
            save(state)
        return merged


def write_snapshot_atomic(pack_dir: Path, snapshot: dict) -> dict:
    """Merge an existing pack's full/legacy fields without resurrecting a pack.

    Initial full writers must reserve with ``allow_create=True`` and publish
    their complete initial snapshot with that token instead.
    """
    with _locked(pack_dir) as (pack, state, save):
        current = _read_snapshot(pack, state)
        if current is None:
            raise MotionPublicationError("Untokened writer cannot create a snapshot")
        if state is None:
            state = _new_state(pack, current)
            save(state)
        recovered = _check_current(pack, state, current)
        if not isinstance(snapshot, dict):
            raise MotionPublicationError("Snapshot update must be a JSON object")
        merged = {**current, **snapshot}
        if _identity(merged) != _identity(current):
            raise MotionPublicationError("Untokened writer cannot change pack identity")
        incoming = snapshot.get("observed_motion")
        if incoming is not None:
            incoming_revision = _snapshot_revision(snapshot)
            _check_motion_identity(current, incoming)
            current_revision = _snapshot_revision(current)
            if incoming_revision > current_revision:
                raise MotionPublicationError("New motion requires its reserved publication token")
            if incoming_revision == current_revision and incoming != current["observed_motion"]:
                raise MotionPublicationError("Conflicting motion content at the same revision")
        if "observed_motion" in current:
            merged["observed_motion"] = current["observed_motion"]
        _atomic_json(pack / "briefing.json", merged)
        if recovered or state["snapshot_file"] != "briefing.json":
            state["snapshot_file"] = "briefing.json"
            save(state)
        return merged
