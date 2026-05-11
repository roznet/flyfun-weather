"""Open-Meteo model metadata — freshness checking and next-update estimation."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# --- Data model ---


@dataclass
class ModelMetadata:
    """Parsed from Open-Meteo ``meta.json``."""

    model: str
    last_init_time: int  # Unix timestamp of the model init (run) time
    last_availability_time: int  # Unix timestamp when data became available
    update_interval_seconds: int
    # Last hourly timestamp actually served by the API for the current run.
    # Often shorter than the static config horizon: e.g. ARPEGE advertises
    # 6 days but a given run may deliver only ~4 days. 0 if not published.
    data_end_time: int = 0


# --- Metadata URLs ---

META_URLS: dict[str, str] = {
    "gfs": "https://api.open-meteo.com/data/ncep_gfs025/static/meta.json",
    "ecmwf": "https://api.open-meteo.com/data/ecmwf_ifs025/static/meta.json",
    "icon": "https://api.open-meteo.com/data/dwd_icon/static/meta.json",
    "ukmo": "https://api.open-meteo.com/data/ukmo_global_deterministic_10km/static/meta.json",
    "meteofrance": "https://api.open-meteo.com/data/meteofrance_arpege_world025/static/meta.json",
}

# DWD text forecasts don't have a metadata API.
# Approximate UTC hours when new data typically becomes available (padded ~30 min).
ASSUMED_UPDATE_HOURS: dict[str, list[int]] = {
    "dwd_short_range": [5, 17],  # actual ~04:30, ~16:30
    "dwd_medium_range": [11],  # actual ~10:30
    "nws_afd": [4, 10, 16, 22],  # ~4x daily, varies by WFO
}


# --- Fetch ---


def _parse_meta(model: str, data: dict) -> ModelMetadata:
    """Parse an Open-Meteo meta.json response into ModelMetadata."""
    return ModelMetadata(
        model=model,
        last_init_time=int(data["last_run_initialisation_time"]),
        last_availability_time=int(data["last_run_availability_time"]),
        update_interval_seconds=int(data["update_interval_seconds"]),
        data_end_time=int(data.get("data_end_time", 0) or 0),
    )


_META_MAX_RETRIES = 3
_META_RETRY_BACKOFF = [5, 10]  # seconds between retries


def fetch_model_metadata(
    models: list[str] | None = None,
    timeout: float = 5,
) -> dict[str, ModelMetadata]:
    """Fetch metadata for the given models in parallel.

    Retries up to 3 times per model on transient errors (502, timeouts,
    connection resets).  Returns a dict keyed by model name.  Failed
    fetches are silently omitted — the caller should treat a missing
    model as stale.
    """
    if models is None:
        models = list(META_URLS.keys())

    urls = {m: META_URLS[m] for m in models if m in META_URLS}
    if not urls:
        return {}

    result: dict[str, ModelMetadata] = {}

    def _fetch_one(model: str, url: str) -> tuple[str, ModelMetadata | None]:
        for attempt in range(_META_MAX_RETRIES):
            try:
                resp = httpx.get(url, timeout=timeout)
                resp.raise_for_status()
                return model, _parse_meta(model, resp.json())
            except Exception as exc:
                if attempt < _META_MAX_RETRIES - 1:
                    wait = _META_RETRY_BACKOFF[attempt]
                    logger.warning(
                        "Failed to fetch metadata for %s (attempt %d/%d): %s, "
                        "retrying in %ds",
                        model, attempt + 1, _META_MAX_RETRIES, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Failed to fetch metadata for %s after %d attempts: %s",
                        model, _META_MAX_RETRIES, exc,
                    )
        return model, None

    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = {pool.submit(_fetch_one, m, u): m for m, u in urls.items()}
        for future in as_completed(futures):
            model, meta = future.result()
            if meta is not None:
                result[model] = meta

    return result


# --- Freshness check ---


def check_freshness(
    stored_init_times: dict[str, int],
    current_metadata: dict[str, ModelMetadata],
) -> tuple[bool, list[str]]:
    """Compare stored init times against live metadata.

    Returns ``(is_fresh, stale_models)``.  A model is stale when the
    live ``last_init_time`` is newer than what was stored.  Missing
    stored times are treated as stale.  If we have no live metadata to
    compare against, we can't confirm freshness so we return not-fresh.
    """
    if not current_metadata:
        return (False, [])

    stale: list[str] = []
    for model, meta in current_metadata.items():
        stored = stored_init_times.get(model)
        if stored is None or meta.last_init_time > stored:
            stale.append(model)
    return (len(stale) == 0, stale)


# --- Next expected update ---


def compute_next_update(
    metadata: dict[str, ModelMetadata],
) -> tuple[datetime | None, str | None]:
    """Estimate the earliest next model update across all sources.

    For Open-Meteo models, uses ``last_availability_time + update_interval_seconds``.
    Also checks the assumed DWD text-forecast schedule.

    Returns ``(next_time, model_name)`` or ``(None, None)`` if unknown.
    """
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, str]] = []

    # Open-Meteo models
    for model, meta in metadata.items():
        next_ts = meta.last_availability_time + meta.update_interval_seconds
        next_dt = datetime.fromtimestamp(next_ts, tz=timezone.utc)
        if next_dt > now:
            candidates.append((next_dt, model))

    # DWD assumed schedule
    for source, hours in ASSUMED_UPDATE_HOURS.items():
        for h in hours:
            candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if candidate <= now:
                # next occurrence is tomorrow
                candidate += timedelta(days=1)
            candidates.append((candidate, source))

    if not candidates:
        return None, None

    candidates.sort(key=lambda c: c[0])
    return candidates[0]
