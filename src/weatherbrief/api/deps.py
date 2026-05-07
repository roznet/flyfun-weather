"""Shared FastAPI dependency functions for the API package.

Extracted to avoid duplication between modules that need the same
app-state values (the `Request`-keyed accessors). Add new accessors
here when more than one router needs them — single-use accessors can
stay private to their module.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request


def airports_db(request: Request) -> str:
    """Path to the euro_aip airports SQLite database."""
    return request.app.state.db_path


def data_dir(request: Request) -> Path:
    """Shared data directory (DATA_DIR env var). Houses GRIB cache,
    pack snapshots, and other per-deployment artifacts."""
    return request.app.state.data_dir
