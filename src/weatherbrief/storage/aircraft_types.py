"""Static ICAO aircraft type data — loaded from JSON config, cached in memory."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "icao_aircraft_types.json"

_cached_types: list[dict] | None = None


def _types_path() -> Path:
    override = os.environ.get("AIRCRAFT_TYPES_PATH")
    if override:
        return Path(override)
    return _DEFAULT_PATH


def load_aircraft_types() -> list[dict]:
    """Load all ICAO aircraft types from JSON. Cached after first load."""
    global _cached_types
    if _cached_types is not None:
        return _cached_types

    path = _types_path()
    try:
        data = json.loads(path.read_text())
        _cached_types = data.get("types", [])
    except Exception:
        logger.warning("Failed to load aircraft types from %s", path, exc_info=True)
        _cached_types = []

    return _cached_types


def search_aircraft_types(query: str, limit: int = 20) -> list[dict]:
    """Search aircraft types by ICAO code, manufacturer, or model name.

    Returns up to ``limit`` matches, sorted with ICAO prefix matches first.
    """
    types = load_aircraft_types()
    q = query.upper().strip()
    if not q:
        return types[:limit]

    exact: list[dict] = []
    prefix: list[dict] = []
    contains: list[dict] = []

    for t in types:
        icao = (t.get("icao") or "").upper()
        mfr = (t.get("manufacturer") or "").upper()
        model = (t.get("model") or "").upper()

        if icao == q:
            exact.append(t)
        elif icao.startswith(q):
            prefix.append(t)
        elif q in icao or q in mfr or q in model:
            contains.append(t)

    return (exact + prefix + contains)[:limit]


def get_aircraft_type(icao_code: str) -> dict | None:
    """Look up a single ICAO type by code. Returns None if not found."""
    for t in load_aircraft_types():
        if t.get("icao", "").upper() == icao_code.upper():
            return t
    return None


def invalidate_cache() -> None:
    """Clear cached types, forcing reload on next access."""
    global _cached_types
    _cached_types = None
