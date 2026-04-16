"""Raw grid data cache for iterative frontal detection development.

Stores Open-Meteo grid responses to disk keyed by (model, init_time).
CLI-only — the scheduled pipeline always fetches fresh data.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/frontal_cache")


def _cache_key(model: str, init_time: str) -> str:
    """Cache key from model name and init time (e.g. 'ecmwf_2026041500')."""
    return f"{model}_{init_time}"


def save_raw_response(
    model: str,
    init_time: str,
    raw_data: dict,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> Path:
    """Save raw Open-Meteo grid response to disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(model, init_time)}.json"
    path.write_text(json.dumps(raw_data))
    logger.info("Cached grid data: %s", path.name)
    return path


def load_raw_response(
    model: str,
    init_time: str,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> dict | None:
    """Load cached response, or None if not cached."""
    path = cache_dir / f"{_cache_key(model, init_time)}.json"
    if path.exists():
        logger.info("Using cached grid data: %s", path.name)
        return json.loads(path.read_text())
    return None


def clear_cache(cache_dir: Path = _DEFAULT_CACHE_DIR) -> int:
    """Delete all cached grid data files. Returns count deleted."""
    if not cache_dir.exists():
        return 0
    files = list(cache_dir.glob("*.json"))
    for f in files:
        f.unlink()
    logger.info("Cleared %d cached files from %s", len(files), cache_dir)
    return len(files)
