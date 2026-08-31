"""Shared serving logic for the DWD + Met Office surface-chart caches.

Both the flight-scoped endpoints (``/packs/{ts}/dwd-chart/...``) and the new
flight-independent synoptic endpoints (``/api/synoptic-charts/...``) resolve a
``(run_cycle, chart_id)`` to bytes in the same shared on-disk cache. This module
holds the source registry, input validation (path-traversal defence), the
Met Office admin/flag gate, and the bytes-serving + manifest helpers so neither
caller duplicates them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from weatherbrief.fetch import dwd_charts, meteofrance_charts, metoffice_charts
from weatherbrief.fetch.chart_cache import parse_run_cycle_dt

# Cache key format: e.g. "2026-05-19T06Z". Validating against this (not just
# exists-on-disk) defends the path join against traversal.
RUN_CYCLE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}Z$")

# Bytes for a given (run_cycle, chart_id) never change once cached.
_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


@dataclass(frozen=True)
class ChartSourceSpec:
    slug: str
    label: str
    module: ModuleType
    media_type: str
    attribution_html: str
    # Met Office reuse is gated until authorisation; DWD is open.
    admin_gated: bool
    # Whether this source may back the flight-independent Synoptic Forecast
    # basemap. False for Météo-France: its licence permits redistribution only
    # to users operating in French airspace, and the maps page has no route to
    # test that against, so it is served only from flight-scoped endpoints.
    synoptic_basemap: bool = True
    # Licence-restricted bytes must not sit in a shared cache where they could
    # be handed to a user who is not entitled to them.
    private_cache: bool = False


SOURCES: dict[str, ChartSourceSpec] = {
    "dwd": ChartSourceSpec(
        slug="dwd",
        label="DWD",
        module=dwd_charts,
        media_type="image/png",
        attribution_html=(
            'Source: <a href="https://www.dwd.de/" target="_blank" '
            'rel="noopener">Deutscher Wetterdienst (DWD)</a>, CC BY 4.0'
        ),
        admin_gated=False,
    ),
    "metoffice": ChartSourceSpec(
        slug="metoffice",
        label="Met Office",
        module=metoffice_charts,
        media_type="image/gif",
        attribution_html=(
            'Source: <a href="https://www.metoffice.gov.uk/" target="_blank" '
            'rel="noopener">Met Office</a> · © Crown copyright'
        ),
        admin_gated=True,
    ),
    "meteofrance": ChartSourceSpec(
        slug="meteofrance",
        label="Météo-France",
        module=meteofrance_charts,
        media_type="image/png",
        attribution_html=(
            'Source: <a href="https://aviation.meteo.fr/" target="_blank" '
            'rel="noopener">Météo-France</a> — AEROWEB'
        ),
        # Not admin-gated: the gate is per-route (French airspace), applied at
        # the flight-scoped endpoint where a route exists, not per-user.
        admin_gated=False,
        synoptic_basemap=False,
        private_cache=True,
    ),
}


def metoffice_charts_allowed(request: Request, db: Session) -> bool:
    """Met Office charts are admin-only until ``METOFFICE_CHARTS_PUBLIC=1``.

    Returns True if the public flag is set, or the caller is an admin (dev
    mode counts as admin). The caller must already be authenticated.
    """
    if metoffice_charts.public_enabled():
        return True
    try:
        from weatherbrief.api.admin import require_admin

        require_admin(request, db=db)
        return True
    except HTTPException:
        return False


def source_allowed(spec: ChartSourceSpec, request: Request, db: Session) -> bool:
    """Whether the caller may see this source on the *flight-independent* map.

    Route-gated sources can never answer here: the maps page has no route, so
    there is nothing to check a licence against and the honest answer is no.
    """
    if not spec.synoptic_basemap:
        return False
    if not spec.admin_gated:
        return True
    return metoffice_charts_allowed(request, db)


def get_source_or_404(source: str) -> ChartSourceSpec:
    spec = SOURCES.get(source)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown chart source: {source!r}")
    return spec


def serve_chart_bytes(
    data_dir: Path,
    spec: ChartSourceSpec,
    run_cycle: str,
    chart_id: str,
    *,
    immutable: bool = True,
) -> Response:
    """Validate inputs, resolve the cached path, return a FileResponse.

    Validates ``run_cycle`` against the cache-key regex (path-traversal guard)
    and ``chart_id`` against the source's allowlist. 410 when the bytes have
    been evicted (or a run legitimately omitted that offset), 404 on bad cycle.
    """
    module = spec.module
    if chart_id not in module.CHART_IDS:
        raise HTTPException(status_code=400, detail="Invalid chart id")
    if not RUN_CYCLE_RE.match(run_cycle):
        raise HTTPException(status_code=400, detail="Invalid run cycle")

    path = module.resolve_chart_path(data_dir, run_cycle, chart_id)
    if path is None:
        raise HTTPException(status_code=410, detail="Chart not available")

    if spec.private_cache:
        # Never "public": a shared/CDN cache must not be able to replay these
        # bytes to a viewer the licence does not cover.
        cache_control = "private, max-age=3600"
    else:
        cache_control = _IMMUTABLE_CACHE_CONTROL if immutable else "public, max-age=3600"
    return FileResponse(
        path,
        media_type=spec.media_type,
        headers={"Cache-Control": cache_control},
    )


def build_source_manifest(data_dir: Path, spec: ChartSourceSpec) -> dict[str, Any] | None:
    """Manifest for one source's latest cached cycle, or None if nothing cached.

    Lists every chart-id present on disk for the latest cycle, each with its
    forecast offset, chart_type (which calibration / native size it uses),
    native pixel size, and valid time (run_cycle + offset).
    """
    module = spec.module
    cycles = module.list_cycles(data_dir)
    if not cycles:
        return None
    run_cycle = cycles[-1]
    issued = parse_run_cycle_dt(run_cycle)

    # Source issuance time = the analysis chart's last_modified. Resolved
    # outside the loop so it doesn't depend on "ana" being first in CHART_IDS.
    ana_meta = module.chart_meta(data_dir, run_cycle, "ana") or {}
    issued_at: str | None = ana_meta.get("last_modified")

    charts: list[dict[str, Any]] = []
    for chart_id in module.CHART_IDS:
        if module.resolve_chart_path(data_dir, run_cycle, chart_id) is None:
            continue
        chart_type = module.chart_type_for(chart_id)
        offset_h = module.FORECAST_OFFSETS_H.get(chart_id, 0)
        native = module.CHART_NATIVE_SIZE.get(chart_type)
        valid_time = None
        if issued is not None:
            valid_time = (issued + timedelta(hours=offset_h)).isoformat().replace("+00:00", "Z")
        charts.append({
            "id": chart_id,
            "offset_h": offset_h,
            "chart_type": chart_type,
            "native_size": list(native) if native else None,
            "valid_time": valid_time,
        })

    if not charts:
        return None

    return {
        "slug": spec.slug,
        "label": spec.label,
        "run_cycle": run_cycle,
        "issued_at": issued_at,
        "attribution_html": spec.attribution_html,
        "charts": charts,
    }
