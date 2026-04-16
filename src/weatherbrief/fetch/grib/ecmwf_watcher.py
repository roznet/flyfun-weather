"""ECMWF delivery completeness watcher.

Periodically scans the ECMWF delivery directory and writes sentinel files
when a forecast run is complete (all expected files have arrived).

The expected file manifest is defined in ``delivery_config.json`` inside
the ECMWF data directory — co-located with the data so it can be updated
without redeploying the application.

Sentinel files:
    .ready_{YYYYMMDD}_{HH}z           — run is complete
    .ready_{YYYYMMDD}_{HH}z.partial   — timed out, using partial delivery
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.fetch.grib.ecmwf_fetch import (
    ECMWFFileInfo,
    ecmwf_grib_dir,
    parse_ecmwf_filename,
    scan_ecmwf_files,
)

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "delivery_config.json"

# Files to never delete during purge (config, sentinels handled separately).
_PURGE_SKIP_SUFFIXES = frozenset({".json", ".log", ".md", ".txt"})


@dataclass
class StreamConfig:
    """Expected delivery manifest for one ECMWF stream."""

    cycles: list[int]
    steps: list[int]
    parts: list[str]

    @property
    def expected_file_count(self) -> int:
        return len(self.steps) * len(self.parts)


@dataclass
class DeliveryConfig:
    """Full ECMWF delivery configuration loaded from delivery_config.json."""

    streams: dict[str, StreamConfig]
    publish_delay_hours: float
    completeness_timeout_hours: float

    @classmethod
    def load(cls, data_dir: Path | None = None) -> DeliveryConfig | None:
        """Load delivery config from the ECMWF data directory.

        Returns None if the config file doesn't exist (graceful degradation).
        """
        config_path = (data_dir or ecmwf_grib_dir()) / _CONFIG_FILENAME
        if not config_path.exists():
            logger.debug(
                "ECMWF delivery config not found: %s — "
                "completeness checking disabled",
                config_path,
            )
            return None

        try:
            raw = json.loads(config_path.read_text())
            streams = {}
            for name, spec in raw["streams"].items():
                streams[name] = StreamConfig(
                    cycles=spec["cycles"],
                    steps=spec["steps"],
                    parts=spec["parts"],
                )
            return cls(
                streams=streams,
                publish_delay_hours=raw.get("publish_delay_hours", 8),
                completeness_timeout_hours=raw.get("completeness_timeout_hours", 2),
            )
        except (json.JSONDecodeError, KeyError):
            logger.error(
                "Failed to parse ECMWF delivery config: %s",
                config_path,
                exc_info=True,
            )
            return None


def _sentinel_name(base_time: datetime) -> str:
    """Sentinel filename for a given run."""
    return f".ready_{base_time:%Y%m%d}_{base_time:%H}z"


def _sentinel_path(data_dir: Path, base_time: datetime) -> Path:
    return data_dir / _sentinel_name(base_time)


def is_run_ready(data_dir: Path | None = None, *, base_time: datetime) -> bool:
    """Check whether a sentinel file exists for the given run.

    Returns True if either .ready_* or .ready_*.partial exists.
    """
    d = data_dir or ecmwf_grib_dir()
    sentinel = _sentinel_path(d, base_time)
    return sentinel.exists() or sentinel.with_suffix(".partial").exists()


def is_run_partial(data_dir: Path | None = None, *, base_time: datetime) -> bool:
    """Check whether the run was marked as partial (timed-out)."""
    d = data_dir or ecmwf_grib_dir()
    return _sentinel_path(d, base_time).with_suffix(".partial").exists()


def _expected_publish_time(
    base_time: datetime, config: DeliveryConfig,
) -> datetime:
    """Wall-clock time when a run is expected to be fully delivered."""
    return base_time + timedelta(hours=config.publish_delay_hours)


def _timeout_time(
    base_time: datetime, config: DeliveryConfig,
) -> datetime:
    """Wall-clock time after which we accept a partial delivery."""
    return _expected_publish_time(base_time, config) + timedelta(
        hours=config.completeness_timeout_hours,
    )


def check_ecmwf_completeness(
    data_dir: Path | None = None,
) -> list[datetime]:
    """Scan the ECMWF directory and write sentinels for complete runs.

    Returns:
        List of base_times for newly-ready runs (complete or timed-out partial).
    """
    d = data_dir or ecmwf_grib_dir()
    config = DeliveryConfig.load(d)
    if config is None:
        return []

    files = scan_ecmwf_files(d, operational_only=True)
    if not files:
        return []

    # Group files by (base_time, stream)
    runs: dict[tuple[datetime, str], list[ECMWFFileInfo]] = {}
    for f in files:
        key = (f.base_time, f.stream)
        runs.setdefault(key, []).append(f)

    now = datetime.now(timezone.utc)
    newly_ready: list[datetime] = []

    for (base_time, stream), run_files in runs.items():
        # Skip if already has a sentinel
        if is_run_ready(d, base_time=base_time):
            continue

        stream_config = config.streams.get(stream)
        if stream_config is None:
            logger.debug(
                "ECMWF stream %r not in delivery config, skipping", stream,
            )
            continue

        expected = stream_config.expected_file_count
        actual = len(run_files)

        if actual >= expected:
            # Complete — write sentinel
            sentinel = _sentinel_path(d, base_time)
            sentinel.write_text(
                f"complete\n"
                f"base_time={base_time.isoformat()}\n"
                f"stream={stream}\n"
                f"files={actual}/{expected}\n"
                f"detected={now.isoformat()}\n"
            )
            logger.info(
                "ECMWF run %s %s complete (%d/%d files) — sentinel written",
                base_time.isoformat(), stream, actual, expected,
            )
            newly_ready.append(base_time)

        elif now >= _timeout_time(base_time, config):
            # Timed out — write partial sentinel
            sentinel = _sentinel_path(d, base_time).with_suffix(".partial")
            sentinel.write_text(
                f"partial\n"
                f"base_time={base_time.isoformat()}\n"
                f"stream={stream}\n"
                f"files={actual}/{expected}\n"
                f"detected={now.isoformat()}\n"
                f"timeout_after_hours="
                f"{config.publish_delay_hours + config.completeness_timeout_hours}\n"
            )
            logger.warning(
                "ECMWF run %s %s timed out with %d/%d files — "
                "partial sentinel written",
                base_time.isoformat(), stream, actual, expected,
            )
            newly_ready.append(base_time)

        else:
            logger.debug(
                "ECMWF run %s %s: %d/%d files, waiting",
                base_time.isoformat(), stream, actual, expected,
            )

    return newly_ready


def purge_old_ecmwf_deliveries(
    data_dir: Path | None = None,
    max_age_hours: float = 72,
) -> int:
    """Delete ECMWF delivery files and sentinels older than max_age_hours.

    Only deletes:
    - Files that successfully parse as ECMWF filenames
    - Sentinel files (.ready_*)
    - Associated .idx files

    Skips delivery_config.json and other non-ECMWF files.

    Returns:
        Number of files removed.
    """
    d = data_dir or ecmwf_grib_dir()
    if not d.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0

    for path in list(d.iterdir()):
        if not path.is_file():
            continue

        name = path.name

        # Sentinel files — check age from content or mtime
        if name.startswith(".ready_"):
            age_s = time.time() - path.stat().st_mtime
            if age_s > max_age_hours * 3600:
                path.unlink(missing_ok=True)
                removed += 1
                logger.debug("Purged old sentinel: %s", name)
            continue

        # Skip config and metadata files
        if any(name.endswith(s) for s in _PURGE_SKIP_SUFFIXES):
            continue
        if name.startswith("."):
            continue

        # .idx companion files — delete if matching GRIB file would be old
        if name.endswith(".idx"):
            # Try parsing the base name (without .idx and the hash part)
            # These are generated by our code, safe to delete by age
            age_s = time.time() - path.stat().st_mtime
            if age_s > max_age_hours * 3600:
                path.unlink(missing_ok=True)
                removed += 1
                logger.debug("Purged old idx: %s", name)
            continue

        # ECMWF data files — only delete if they parse and are old
        info = parse_ecmwf_filename(path)
        if info is None:
            continue

        if info.base_time < cutoff:
            path.unlink(missing_ok=True)
            removed += 1

    if removed:
        logger.info(
            "Purged %d old ECMWF files (older than %dh) from %s",
            removed, int(max_age_hours), d,
        )
    return removed
