"""Flight and BriefingPack storage — file-based persistence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR


def _flights_dir(data_dir: Path) -> Path:
    return data_dir / "flights"


def _flight_dir(flight_id: str, data_dir: Path) -> Path:
    return _flights_dir(data_dir) / flight_id


def _packs_dir(flight_id: str, data_dir: Path) -> Path:
    return _flight_dir(flight_id, data_dir) / "packs"


# --- Flight CRUD ---


def save_flight(flight: Flight, data_dir: Path | None = None) -> Path:
    """Save a flight config to disk. Returns the path written."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    flight_dir = _flight_dir(flight.id, data_dir)
    flight_dir.mkdir(parents=True, exist_ok=True)

    flight_path = flight_dir / "flight.json"
    flight_path.write_text(flight.model_dump_json(indent=2))

    # Update the flight registry
    _update_registry(flight, data_dir)

    return flight_path


def load_flight(flight_id: str, data_dir: Path | None = None) -> Flight:
    """Load a flight by ID. Raises FileNotFoundError if not found."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    flight_path = _flight_dir(flight_id, data_dir) / "flight.json"

    raw = json.loads(flight_path.read_text())
    return Flight.model_validate(raw)


def list_flights(data_dir: Path | None = None) -> list[Flight]:
    """List all saved flights, sorted by creation date (newest first)."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    flights_dir = _flights_dir(data_dir)

    if not flights_dir.exists():
        return []

    flights = []
    for d in flights_dir.iterdir():
        flight_path = d / "flight.json"
        if d.is_dir() and flight_path.exists():
            raw = json.loads(flight_path.read_text())
            flights.append(Flight.model_validate(raw))

    flights.sort(key=lambda f: f.created_at, reverse=True)
    return flights


def delete_flight(flight_id: str, data_dir: Path | None = None) -> None:
    """Delete a flight and all its packs. Raises FileNotFoundError if not found."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    flight_dir = _flight_dir(flight_id, data_dir)

    if not flight_dir.exists():
        raise FileNotFoundError(f"Flight not found: {flight_id}")

    # Remove all files recursively
    _rmtree(flight_dir)

    # Update registry
    _remove_from_registry(flight_id, data_dir)


# --- BriefingPack operations ---


def save_pack_meta(meta: BriefingPackMeta, data_dir: Path | None = None) -> Path:
    """Save briefing pack metadata. Returns the path written."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    pack_dir = pack_dir_for(meta.flight_id, meta.fetch_timestamp, data_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)

    pack_path = pack_dir / "pack.json"
    pack_path.write_text(meta.model_dump_json(indent=2))
    return pack_path


def load_pack_meta(
    flight_id: str, fetch_timestamp: str, data_dir: Path | None = None
) -> BriefingPackMeta:
    """Load pack metadata. Raises FileNotFoundError if not found."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    pack_path = pack_dir_for(flight_id, fetch_timestamp, data_dir) / "pack.json"

    raw = json.loads(pack_path.read_text())
    return BriefingPackMeta.model_validate(raw)


def list_packs(
    flight_id: str, data_dir: Path | None = None
) -> list[BriefingPackMeta]:
    """List all packs for a flight, sorted by fetch timestamp (newest first)."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    packs_dir = _packs_dir(flight_id, data_dir)

    if not packs_dir.exists():
        return []

    packs = []
    for d in sorted(packs_dir.iterdir(), reverse=True):
        pack_path = d / "pack.json"
        if d.is_dir() and pack_path.exists():
            raw = json.loads(pack_path.read_text())
            packs.append(BriefingPackMeta.model_validate(raw))

    return packs


def pack_dir_for(
    flight_id: str, fetch_timestamp: str, data_dir: Path | None = None
) -> Path:
    """Get the directory path for a specific pack. Useful for saving artifacts."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    # Sanitize timestamp for use as directory name
    safe_ts = fetch_timestamp.replace(":", "-").replace("+", "p")
    return _packs_dir(flight_id, data_dir) / safe_ts


# --- Registry (lightweight index of all flights) ---


def _registry_path(data_dir: Path) -> Path:
    return data_dir / "flights.json"


def _update_registry(flight: Flight, data_dir: Path) -> None:
    """Add or update a flight in the registry."""
    registry = _load_registry(data_dir)

    # Replace existing or append
    registry = [f for f in registry if f["id"] != flight.id]
    registry.append(json.loads(flight.model_dump_json()))

    _save_registry(registry, data_dir)


def _remove_from_registry(flight_id: str, data_dir: Path) -> None:
    """Remove a flight from the registry."""
    registry = _load_registry(data_dir)
    registry = [f for f in registry if f["id"] != flight_id]
    _save_registry(registry, data_dir)


def _load_registry(data_dir: Path) -> list[dict]:
    path = _registry_path(data_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _save_registry(registry: list[dict], data_dir: Path) -> None:
    path = _registry_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2))


# --- Utilities ---


def _rmtree(path: Path) -> None:
    """Recursively remove a directory tree."""
    if path.exists():
        shutil.rmtree(path)
