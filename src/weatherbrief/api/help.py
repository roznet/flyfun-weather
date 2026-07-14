"""Unified help-catalog endpoint — the single source of truth for (i) popups.

Serves the same help content the web app renders in its (i) info popups so the
iOS/iPad app can cache it and render popups offline without hand-duplicating any
text. Two sections in one versioned, ETag-cacheable payload:

- ``metrics``  — parsed verbatim from ``web/ts/data/metrics-catalog.json`` (the
  web bundle's only copy). English-only; see the ``lang`` note below.
- ``maps``     — parsed verbatim from ``web/ts/data/map-metrics-catalog.json``
  (the forecast-map colour scales, thresholds, labels and legends). Same
  single-source pattern: the web bundle imports the JSON directly, iOS fetches
  it here and caches it, so a threshold change is one JSON edit both clients
  pick up (issue #419, B2). Language-independent (colours/numbers), but still
  folded into the version hash so any edit re-validates the ETag.
- ``advisories`` — the existing advisory catalog (``get_catalog()``), the same
  data already served at ``/user/preferences/advisories/catalog``.

``lang`` contract: the param is accepted and threaded through now so no client
or endpoint change is needed once content is translated. Today both the metrics
catalog and the advisory ``catalog_entry()`` strings are English-only (the web
app renders that catalog content raw — only popup *chrome* goes through ``t()``),
so every ``lang`` currently returns English. ``lang`` is folded into the version
hash so a client that switches language re-fetches and gets the localized payload
the moment translations land. Localizing the content itself is a separate
follow-up (issue #311).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, Response

from weatherbrief.analysis.advisories import get_catalog
from weatherbrief.models import AdvisoryCatalogEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/help", tags=["help"])

# Languages the web app localizes into; any other value falls back to English.
SUPPORTED_LANGS = ("en", "fr", "de", "es")

# Repo-root-relative path to the metrics catalog. ``help.py`` lives at
# ``src/weatherbrief/api/help.py``; ``parents[3]`` is the repo root — the same
# resolution ``app.py`` uses to locate ``web/`` for static serving. The full
# ``web/`` tree (including ``ts/data/``) is copied into the Docker image
# (Dockerfile ``COPY web/ web/``), so this path resolves in dev and prod alike.
_METRICS_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "web" / "ts" / "data" / "metrics-catalog.json"
)
_MAP_METRICS_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "web" / "ts" / "data" / "map-metrics-catalog.json"
)

# Per-language memoized (body bytes, version) — the catalog is static per process.
_payload_cache: dict[str, tuple[bytes, str]] = {}
_metrics_cache: dict[str, Any] | None = None
_map_metrics_cache: dict[str, Any] | None = None


def _load_metrics_catalog() -> dict[str, Any]:
    """Load and memoize the metrics catalog JSON (English, served as-is)."""
    global _metrics_cache
    if _metrics_cache is None:
        if not _METRICS_CATALOG_PATH.exists():
            raise RuntimeError(
                f"metrics catalog not found at {_METRICS_CATALOG_PATH} — "
                "expected web/ts/data/metrics-catalog.json to be present"
            )
        with _METRICS_CATALOG_PATH.open("r", encoding="utf-8") as fh:
            _metrics_cache = json.load(fh)
    return _metrics_cache


def _load_map_metrics_catalog() -> dict[str, Any]:
    """Load and memoize the forecast-map metrics catalog JSON (served as-is)."""
    global _map_metrics_cache
    if _map_metrics_cache is None:
        if not _MAP_METRICS_CATALOG_PATH.exists():
            raise RuntimeError(
                f"map metrics catalog not found at {_MAP_METRICS_CATALOG_PATH} — "
                "expected web/ts/data/map-metrics-catalog.json to be present"
            )
        with _MAP_METRICS_CATALOG_PATH.open("r", encoding="utf-8") as fh:
            _map_metrics_cache = json.load(fh)
    return _map_metrics_cache


def _localize_advisories(
    entries: list[AdvisoryCatalogEntry], lang: str
) -> list[dict[str, Any]]:
    """Return advisory catalog entries as dicts, localized to ``lang``.

    Single seam for advisory localization. Today the catalog is English-only, so
    this is the identity mapping regardless of ``lang``; when translations land
    only this function changes (callers and the endpoint stay put).
    """
    return [entry.model_dump() for entry in entries]


def _build_payload(lang: str) -> tuple[bytes, str]:
    """Build (canonical JSON body bytes, version hash) for ``lang``, memoized.

    The version is a content hash over the localized core (metrics + advisories +
    lang). It is folded into the body as ``version`` and drives the ETag, so a
    matching ``If-None-Match`` is byte-for-byte safe.
    """
    cached = _payload_cache.get(lang)
    if cached is not None:
        return cached

    core = {
        "lang": lang,
        "metrics": _load_metrics_catalog(),
        "maps": _load_map_metrics_catalog(),
        "advisories": _localize_advisories(get_catalog(), lang),
    }
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    payload = {
        "version": version,
        "metrics": core["metrics"],
        "maps": core["maps"],
        "advisories": core["advisories"],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    result = (body, version)
    _payload_cache[lang] = result
    return result


def _etag_matches(if_none_match: str | None, version: str) -> bool:
    """True if the client's If-None-Match covers our current version.

    Accepts a comma-separated list, weak (``W/``) prefixes, quotes, and ``*``.
    """
    if not if_none_match:
        return False
    for token in if_none_match.split(","):
        candidate = token.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        candidate = candidate.strip('"')
        if candidate == version:
            return True
    return False


@router.get("/catalog")
def get_help_catalog(
    lang: str = "en",
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """Return the merged help catalog (metrics + advisories) with an ETag.

    ``304 Not Modified`` when ``If-None-Match`` matches the current version, so
    a client that already has the catalog cached spends no bandwidth.
    """
    normalized = lang if lang in SUPPORTED_LANGS else "en"
    body, version = _build_payload(normalized)
    etag = f'"{version}"'

    if _etag_matches(if_none_match, version):
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=body,
        status_code=200,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )
