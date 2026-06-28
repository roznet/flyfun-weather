"""Slim airports SQLite download for native clients (iOS autocomplete).

The iOS app does fast, offline ICAO autocomplete against a *local* copy of the
airport table (via RZFlight's ``KnownAirports``). Rather than bundling a SQLite
file into the app — which would churn every AIRAC cycle and bloat the repo — the
client downloads a slimmed-down copy here and refreshes it only when the source
nav DB changes (ETag / ``If-None-Match``). With no local copy yet (first launch
or offline) the client simply has an empty autocomplete; nothing breaks.

The slim DB keeps only the ``airports`` + ``runways`` tables (what
``KnownAirports`` reads), dropping procedures / AIP / waypoints / change-log
tables — ~3.5 MB instead of the full ~33 MB nav DB.

No auth: airport data is public, descriptive reference data (same stance as the
data-sources catalogue). The build is cached on disk next to the nav DB and
rebuilt only when the nav DB's mtime advances.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

router = APIRouter(prefix="/nav", tags=["nav"])

# Serialise concurrent rebuilds (the freshness loop or multiple clients could
# race the first build after an AIRAC update).
_build_lock = threading.Lock()


def _slim_path(nav_db_path: str) -> Path:
    return Path(str(nav_db_path) + ".airports_slim.db")


def _etag_path(nav_db_path: str) -> Path:
    return Path(str(nav_db_path) + ".airports_slim.etag")


def _build_slim_db(nav_db_path: str, out_path: Path) -> None:
    """Copy just airports + runways into a fresh SQLite at ``out_path``."""
    tmp = Path(str(out_path) + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(str(tmp))
    try:
        con.execute("ATTACH DATABASE ? AS src", (nav_db_path,))
        con.execute("CREATE TABLE airports AS SELECT * FROM src.airports")
        con.execute("CREATE TABLE runways AS SELECT * FROM src.runways")
        # KnownAirports looks runways up by airport_icao; index keeps that O(log n).
        con.execute("CREATE INDEX idx_runways_airport_icao ON runways(airport_icao)")
        con.commit()
        con.execute("DETACH DATABASE src")
    finally:
        con.close()
    tmp.replace(out_path)


def _content_etag(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def ensure_slim_db(nav_db_path: str) -> tuple[Path, str]:
    """Return ``(slim_path, etag)``, rebuilding when the nav DB is newer.

    Exposed (not underscore-prefixed) so tests and any future warm-up loop can
    trigger the build without going through the HTTP layer.
    """
    slim_path = _slim_path(nav_db_path)
    etag_path = _etag_path(nav_db_path)
    nav_mtime = os.path.getmtime(nav_db_path)
    is_fresh = slim_path.exists() and os.path.getmtime(slim_path) >= nav_mtime
    if not is_fresh:
        with _build_lock:
            # Re-check under the lock — another request may have just built it.
            is_fresh = slim_path.exists() and os.path.getmtime(slim_path) >= nav_mtime
            if not is_fresh:
                _build_slim_db(nav_db_path, slim_path)
                etag = _content_etag(slim_path)
                etag_path.write_text(etag)
                return slim_path, etag
    if etag_path.exists():
        return slim_path, etag_path.read_text().strip()
    etag = _content_etag(slim_path)
    etag_path.write_text(etag)
    return slim_path, etag


@router.get("/airports-db")
def download_airports_db(request: Request):
    """Stream the slim airports SQLite, honoring ``If-None-Match`` (ETag)."""
    nav_db_path = getattr(request.app.state, "db_path", "") or ""
    if not nav_db_path or not os.path.exists(nav_db_path):
        raise HTTPException(status_code=503, detail="Airport database unavailable")

    slim_path, etag = ensure_slim_db(nav_db_path)
    quoted = f'"{etag}"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() in (etag, quoted):
        return Response(status_code=304, headers={"ETag": quoted, "Cache-Control": "no-cache"})

    return FileResponse(
        str(slim_path),
        media_type="application/octet-stream",
        filename="airports.db",
        headers={"ETag": quoted, "Cache-Control": "no-cache"},
    )
