"""JSON snapshot save/load/list for forecast data."""

from __future__ import annotations

import json
from pathlib import Path

from weatherbrief.models import ForecastSnapshot

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


def _snapshot_dir(
    target_date: str, days_out: int, fetch_date: str, data_dir: Path
) -> Path:
    """Build snapshot directory path: data/forecasts/{target_date}/d-{N}_{fetch_date}/"""
    return data_dir / "forecasts" / target_date / f"d-{days_out}_{fetch_date}"


def save_snapshot(
    snapshot: ForecastSnapshot, data_dir: Path | None = None
) -> Path:
    """Save a forecast snapshot to JSON. Returns the path written."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    out_dir = _snapshot_dir(
        snapshot.target_date, snapshot.days_out, snapshot.fetch_date, data_dir
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "snapshot.json"
    out_path.write_text(snapshot.model_dump_json(indent=2))
    return out_path


def load_snapshot(
    target_date: str,
    days_out: int,
    fetch_date: str,
    data_dir: Path | None = None,
) -> ForecastSnapshot:
    """Load a snapshot from JSON."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    snap_dir = _snapshot_dir(target_date, days_out, fetch_date, data_dir)
    snap_path = snap_dir / "snapshot.json"

    raw = json.loads(snap_path.read_text())
    return ForecastSnapshot.model_validate(raw)


def list_snapshots(
    target_date: str, data_dir: Path | None = None
) -> list[dict[str, str]]:
    """List available snapshots for a target date.

    Returns list of dicts with 'days_out', 'fetch_date', 'path'.
    """
    data_dir = data_dir or DEFAULT_DATA_DIR
    target_dir = data_dir / "forecasts" / target_date

    if not target_dir.exists():
        return []

    snapshots = []
    for d in sorted(target_dir.iterdir()):
        if d.is_dir() and (d / "snapshot.json").exists():
            parts = d.name.split("_", 1)
            days_out = parts[0] if parts else d.name
            fetch_date = parts[1] if len(parts) > 1 else ""
            snapshots.append({
                "days_out": days_out,
                "fetch_date": fetch_date,
                "path": str(d / "snapshot.json"),
            })

    return snapshots
