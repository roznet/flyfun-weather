"""Flight-independent surface-chart endpoints for the Synoptic Forecast map.

The maps page is flight-independent, but the existing chart endpoints are
flight-scoped (``/packs/{ts}/dwd-chart/...``). These endpoints serve the same
shared cache keyed by ``(run_cycle, chart_id)`` for the *latest* cycle, so the
Synoptic Forecast tab can use a DWD or Met Office chart as the basemap under
the Hewson gridded overlay + front polylines.

  GET /api/synoptic-charts/manifest
      → the sources the caller may see (DWD always; Met Office gated), each
        with its latest cycle, issuance, attribution, and per-chart metadata
        (offset, chart_type, native size, valid time).

  GET /api/synoptic-charts/{source}/{run_cycle}/{chart_id}
      → the chart bytes (immutable cache).

Auth mirrors the Hewson map endpoints: any authenticated user. The Synoptic
Forecast tab is hidden client-side unless the user enables it; Met Office is
additionally admin-gated until ``METOFFICE_CHARTS_PUBLIC=1``.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from weatherbrief.api._chart_serving import (
    SOURCES,
    build_source_manifest,
    get_source_or_404,
    serve_chart_bytes,
    source_allowed,
)

router = APIRouter(prefix="/synoptic-charts", tags=["synoptic-charts"])


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "data"))


@router.get("/manifest")
def get_manifest(
    request: Request,
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List the chart sources the caller may use as a synoptic basemap.

    Only sources the caller is allowed to see are included (Met Office is
    omitted for non-admins until the public flag is set), and only sources with
    at least one cached chart in their latest cycle.
    """
    data_dir = _data_dir()
    sources = []
    for spec in SOURCES.values():
        if not source_allowed(spec, request, db):
            continue
        manifest = build_source_manifest(data_dir, spec)
        if manifest is not None:
            sources.append(manifest)
    return {"sources": sources}


@router.get("/{source}/{run_cycle}/{chart_id}")
def get_chart(
    source: str,
    run_cycle: str,
    chart_id: str,
    request: Request,
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Serve chart bytes from the shared cache (immutable)."""
    spec = get_source_or_404(source)
    if not source_allowed(spec, request, db):
        # 404 (not 403) so a non-admin can't probe which sources exist.
        raise HTTPException(status_code=404, detail="Unknown chart source")
    return serve_chart_bytes(_data_dir(), spec, run_cycle, chart_id)
