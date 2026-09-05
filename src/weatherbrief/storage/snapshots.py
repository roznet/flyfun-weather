"""JSON snapshot save/load/list for forecast data."""

from __future__ import annotations

import json
import os
from pathlib import Path

from weatherbrief.models import ForecastSnapshot

DEFAULT_DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))


def _snapshot_dir(
    target_date: str, days_out: int, fetch_date: str, data_dir: Path
) -> Path:
    """Build snapshot directory path: data/forecasts/{target_date}/d-{N}_{fetch_date}/"""
    return data_dir / "forecasts" / target_date / f"d-{days_out}_{fetch_date}"


def save_snapshot(
    snapshot: ForecastSnapshot, data_dir: Path | None = None
) -> Path:
    """Save a forecast snapshot as split files (briefing.json + forecasts.json).

    Cross-section data is saved separately via :func:`save_cross_section`.
    Returns the path to briefing.json.
    """
    data_dir = data_dir or DEFAULT_DATA_DIR
    out_dir = _snapshot_dir(
        snapshot.target_date, snapshot.days_out, snapshot.fetch_date, data_dir
    )
    from weatherbrief.storage.observed_motion import full_snapshot_writer

    with full_snapshot_writer(out_dir) as write:
        briefing_path = out_dir / "briefing.json"
        briefing = snapshot.model_dump(mode="json", exclude={"cross_sections", "forecasts"})
        write(briefing, snapshot.model_dump(mode="json"))

        # forecasts.json: route + metadata + forecasts only
        forecasts_path = out_dir / "forecasts.json"
        forecasts_path.write_text(
            snapshot.model_dump_json(
                indent=2,
                include={"route", "target_date", "fetch_date", "days_out", "departure_time", "forecasts"},
            )
        )

        return briefing_path


def save_cross_section(
    snapshot: ForecastSnapshot, data_dir: Path | None = None
) -> Path:
    """Save cross-section data to a separate JSON file alongside the snapshot."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    out_dir = _snapshot_dir(
        snapshot.target_date, snapshot.days_out, snapshot.fetch_date, data_dir
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "cross_section.json"
    out_path.write_text(
        snapshot.model_dump_json(indent=2, include={"cross_sections"})
    )
    return out_path


def load_snapshot(
    target_date: str,
    days_out: int,
    fetch_date: str,
    data_dir: Path | None = None,
) -> ForecastSnapshot:
    """Load a snapshot from split files (briefing.json + forecasts.json).

    Falls back to legacy snapshot.json if new files don't exist.
    """
    data_dir = data_dir or DEFAULT_DATA_DIR
    snap_dir = _snapshot_dir(target_date, days_out, fetch_date, data_dir)

    briefing_path = snap_dir / "briefing.json"
    forecasts_path = snap_dir / "forecasts.json"
    legacy_path = snap_dir / "snapshot.json"

    if briefing_path.exists() and forecasts_path.exists():
        briefing = json.loads(briefing_path.read_text())
        forecasts = json.loads(forecasts_path.read_text())
        briefing["forecasts"] = forecasts.get("forecasts", [])
        return ForecastSnapshot.model_validate(briefing)

    # Legacy fallback
    raw = json.loads(legacy_path.read_text())
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
        if not d.is_dir():
            continue
        # Prefer briefing.json, fall back to legacy snapshot.json
        if (d / "briefing.json").exists():
            snap_file = "briefing.json"
        elif (d / "snapshot.json").exists():
            snap_file = "snapshot.json"
        else:
            continue
        parts = d.name.split("_", 1)
        days_out = parts[0] if parts else d.name
        fetch_date = parts[1] if len(parts) > 1 else ""
        snapshots.append({
            "days_out": days_out,
            "fetch_date": fetch_date,
            "path": str(d / snap_file),
        })

    return snapshots
