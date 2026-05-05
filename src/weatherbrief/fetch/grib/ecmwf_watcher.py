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
class CycleConfig:
    """Expected delivery manifest for one ECMWF init-hour cycle."""

    steps: list[int]
    parts: list[str]

    @property
    def expected_file_count(self) -> int:
        return len(self.steps) * len(self.parts)


@dataclass
class DeliveryConfig:
    """Full ECMWF delivery configuration loaded from delivery_config.json.

    Keyed by init hour (0, 6, 12, 18) rather than stream name because the
    IFS Cycle 50r1 rollout (12-May-2026) merges scda/fc into oper/fc — all
    four cycles then arrive with the same stream label, so stream-name
    keying would flag 06/18z as perpetually partial against the 168h
    oper expectation.
    """

    cycles: dict[int, CycleConfig]
    publish_delay_hours: float
    completeness_timeout_hours: float

    @classmethod
    def load(cls, data_dir: Path | None = None) -> DeliveryConfig | None:
        """Load delivery config from the ECMWF data directory.

        Accepts two schema shapes for deploy-ordering safety:
          - New: top-level "cycles" keyed by init hour (preferred).
          - Legacy: top-level "streams" keyed by stream name with an inner
            "cycles" list — expanded into one CycleConfig per init hour.

        Returns None if the config file doesn't exist or can't be parsed
        (graceful degradation — completeness checking just disables).
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
            cycles = cls._parse_cycles(raw)
            return cls(
                cycles=cycles,
                publish_delay_hours=raw.get("publish_delay_hours", 8),
                completeness_timeout_hours=raw.get("completeness_timeout_hours", 2),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.error(
                "Failed to parse ECMWF delivery config: %s",
                config_path,
                exc_info=True,
            )
            return None

    @staticmethod
    def _parse_cycles(raw: dict) -> dict[int, CycleConfig]:
        if "cycles" in raw:
            return {
                int(hour): CycleConfig(steps=spec["steps"], parts=spec["parts"])
                for hour, spec in raw["cycles"].items()
            }
        if "streams" in raw:
            logger.info(
                "delivery_config.json: legacy 'streams' schema — update to "
                "'cycles' keyed by init hour before IFS 50r1 (12-May-2026).",
            )
            out: dict[int, CycleConfig] = {}
            for spec in raw["streams"].values():
                cfg = CycleConfig(steps=spec["steps"], parts=spec["parts"])
                for hour in spec["cycles"]:
                    out[int(hour)] = cfg
            return out
        raise KeyError("delivery_config.json must define either 'cycles' or 'streams'")


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


def _parse_sentinel_base_time(name: str) -> datetime | None:
    """Parse the base_time from a ``.ready_YYYYMMDD_HHz[.partial]`` filename."""
    if not name.startswith(".ready_"):
        return None
    # ".ready_YYYYMMDD_HHz" or ".ready_YYYYMMDD_HHz.partial"
    body = name[len(".ready_"):]
    if body.endswith(".partial"):
        body = body[: -len(".partial")]
    # Now body is "YYYYMMDD_HHz"
    if not body.endswith("z") or len(body) != len("YYYYMMDD_HHz"):
        return None
    try:
        return datetime.strptime(body, "%Y%m%d_%Hz").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_latest_ready(data_dir: Path | None = None) -> datetime | None:
    """Return the most recent ECMWF run with a readiness sentinel, or None.

    Used by the freshness loop's dynamic check for ``ecmwf:direct``.  Reads
    only filenames in the delivery directory — no network, no GRIB parsing.
    Counts both ``.ready_*z`` (complete) and ``.ready_*z.partial`` (timed-out)
    sentinels: a partial run is still useful data the briefing pipeline can
    consume, so its arrival should also bump freshness.
    """
    result = get_latest_ready_with_mtime(data_dir)
    return result[0] if result is not None else None


def get_latest_ready_with_mtime(
    data_dir: Path | None = None,
) -> tuple[datetime, datetime] | None:
    """Like :func:`get_latest_ready` but also returns the sentinel's mtime.

    The mtime approximates "when the run finished landing locally" — the
    closest analogue we have to a publish wallclock for direct ECMWF, since
    ECMWF themselves don't expose a server-side availability time the way
    Open-Meteo's ``meta.json`` does.  Used by the freshness ``Observation``
    so the popover can show *something* under "Published" rather than "—".

    Returns ``(init, sentinel_mtime)`` aware UTC, or ``None`` if no
    sentinel exists.
    """
    d = data_dir or ecmwf_grib_dir()
    if not d.exists():
        return None
    latest: datetime | None = None
    latest_mtime: datetime | None = None
    for path in d.iterdir():
        bt = _parse_sentinel_base_time(path.name)
        if bt is None:
            continue
        if latest is None or bt > latest:
            latest = bt
            try:
                latest_mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc,
                )
            except OSError:
                latest_mtime = None
    if latest is None:
        return None
    return latest, latest_mtime if latest_mtime is not None else latest


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

    # Group files by base_time (init hour determines the expected manifest
    # post-50r1; stream is informational only).
    runs: dict[datetime, list[ECMWFFileInfo]] = {}
    for f in files:
        runs.setdefault(f.base_time, []).append(f)

    now = datetime.now(timezone.utc)
    newly_ready: list[datetime] = []

    for base_time, run_files in runs.items():
        # Skip if already has a sentinel
        if is_run_ready(d, base_time=base_time):
            continue

        cycle_config = config.cycles.get(base_time.hour)
        if cycle_config is None:
            logger.debug(
                "ECMWF init hour %d not in delivery config, skipping",
                base_time.hour,
            )
            continue

        expected = cycle_config.expected_file_count
        actual = len(run_files)
        stream = run_files[0].stream  # informational; may mix post-50r1

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
