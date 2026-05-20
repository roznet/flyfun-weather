"""Shared pack_dir I/O for pipeline task artifacts.

Each save function writes artifacts to a flat pack directory.
Each load function reads and validates artifacts back into Pydantic models.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.models import (
    Diagnostic,
    ElevationProfile,
    RouteAdvisoriesManifest,
    RouteAnalysesManifest,
    RouteCrossSection,
    RoutePoint,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_fetch_artifacts(
    pack_dir: Path,
    cross_sections: list[RouteCrossSection],
    elevation_profile: ElevationProfile | None,
    route_points: list[RoutePoint],
    *,
    models_fetched: list[str] | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> None:
    """Persist fetch-stage artifacts to *pack_dir*.

    Writes ``fetch_meta.json`` with the fetch-stage diagnostics. The pipeline
    rewrites this file at the end of execute_briefing with the full merged
    set (digest/gramet/skewt diagnostics included) via :func:`write_pack_meta`.
    """
    pack_dir.mkdir(parents=True, exist_ok=True)

    if cross_sections:
        cs_path = pack_dir / "cross_section.json"
        # Compact JSON — this file is large (multi-MB) and machine-read only
        cs_data = [cs.model_dump(mode="json") for cs in cross_sections]
        cs_path.write_text(json.dumps({"cross_sections": cs_data}))

    if elevation_profile:
        ep_path = pack_dir / "elevation_profile.json"
        ep_path.write_text(elevation_profile.model_dump_json(indent=2))

    if route_points:
        rp_path = pack_dir / "route_points.json"
        rp_data = [rp.model_dump(mode="json") for rp in route_points]
        rp_path.write_text(json.dumps(rp_data, indent=2))

    write_pack_meta(
        pack_dir,
        models_fetched=models_fetched or [],
        diagnostics=diagnostics or [],
    )


def write_pack_meta(
    pack_dir: Path,
    *,
    models_fetched: list[str],
    diagnostics: list[Diagnostic],
    fetched_at: datetime | None = None,
) -> None:
    """Write or rewrite ``fetch_meta.json`` with the given diagnostics.

    Called once by ``save_fetch_artifacts`` after fetch completes, then again
    by the pipeline at end-of-run with diagnostics merged across all stages.
    Keeping the file name stable avoids consumer churn — its contents are
    additive, not breaking.
    """
    meta = {
        "fetched_at": (fetched_at or datetime.now(timezone.utc)).isoformat(),
        "models_fetched": models_fetched,
        "diagnostics": [d.model_dump(mode="json") for d in diagnostics],
    }
    meta_path = pack_dir / "fetch_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))


def save_analysis_artifacts(
    pack_dir: Path,
    snapshot,  # ForecastSnapshot
    route_analyses_manifest: RouteAnalysesManifest | None,
) -> None:
    """Persist analysis-stage artifacts to *pack_dir*.

    Writes two files:
    - **briefing.json** — route, analyses, observations, metadata (no forecasts)
    - **forecasts.json** — route, metadata, raw forecasts (no analyses)
    """
    pack_dir.mkdir(parents=True, exist_ok=True)

    # briefing.json: everything except cross_sections and forecasts
    briefing_path = pack_dir / "briefing.json"
    briefing_path.write_text(
        snapshot.model_dump_json(
            indent=2, exclude={"cross_sections", "forecasts"},
        )
    )

    # forecasts.json: route + metadata + forecasts only (compact — large file)
    forecasts_path = pack_dir / "forecasts.json"
    forecasts_path.write_text(
        snapshot.model_dump_json(
            include={"route", "target_date", "fetch_date", "days_out", "forecasts"},
        )
    )

    if route_analyses_manifest:
        ra_path = pack_dir / "route_analyses.json"
        # Compact JSON — large file with per-point sounding data
        ra_path.write_text(
            route_analyses_manifest.model_dump_json(
                exclude={"analyses": {"__all__": {"sounding": {"__all__": {
                    "derived_levels",
                }}}}},
            )
        )


def save_advisory_artifacts(
    pack_dir: Path,
    manifest: RouteAdvisoriesManifest,
) -> None:
    """Persist advisory-stage artifacts to *pack_dir*."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    adv_path = pack_dir / "route_advisories.json"
    adv_path.write_text(manifest.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_cross_sections(pack_dir: Path) -> list[RouteCrossSection]:
    """Load cross-sections from ``cross_section.json``."""
    cs_path = pack_dir / "cross_section.json"
    if not cs_path.exists():
        return []
    data = json.loads(cs_path.read_text())
    return [
        RouteCrossSection.model_validate(item)
        for item in data.get("cross_sections", [])
    ]


def load_elevation_profile(pack_dir: Path) -> ElevationProfile | None:
    """Load elevation profile, returning *None* if missing."""
    ep_path = pack_dir / "elevation_profile.json"
    if not ep_path.exists():
        return None
    return ElevationProfile.model_validate_json(ep_path.read_text())


def load_route_points(pack_dir: Path) -> list[RoutePoint] | None:
    """Load route points, returning *None* if missing."""
    rp_path = pack_dir / "route_points.json"
    if not rp_path.exists():
        return None
    data = json.loads(rp_path.read_text())
    return [RoutePoint.model_validate(item) for item in data]


def load_route_analyses(pack_dir: Path) -> RouteAnalysesManifest:
    """Load route analyses manifest.  Raises if file is missing."""
    ra_path = pack_dir / "route_analyses.json"
    return RouteAnalysesManifest.model_validate_json(ra_path.read_text())


def load_fetch_meta(pack_dir: Path) -> dict | None:
    """Load fetch metadata, returning *None* if missing."""
    meta_path = pack_dir / "fetch_meta.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text())


def _load_json_with_fallback(
    pack_dir: Path, name: str, fallback: str = "snapshot.json",
) -> dict | None:
    """Load a JSON file, falling back to snapshot.json for old packs."""
    path = pack_dir / name
    if path.exists():
        return json.loads(path.read_text())
    fb_path = pack_dir / fallback
    if fb_path.exists():
        return json.loads(fb_path.read_text())
    return None


def load_briefing(pack_dir: Path) -> dict | None:
    """Load briefing.json (route + analyses + observations), fallback to snapshot.json."""
    return _load_json_with_fallback(pack_dir, "briefing.json")


def load_forecasts(pack_dir: Path) -> dict | None:
    """Load forecasts.json (route + metadata + raw forecasts), fallback to snapshot.json."""
    return _load_json_with_fallback(pack_dir, "forecasts.json")


def parse_target_time(snapshot_data: dict) -> datetime:
    """Extract the flight target datetime from snapshot/briefing JSON.

    Returns a timezone-aware UTC datetime.  Old packs that stored naive
    timestamps are promoted to aware UTC so comparisons against both old
    (naive ``HourlyForecast.time``) and new (aware) data work correctly.
    """
    # Prefer departure_time (new packs)
    dt_str = snapshot_data.get("departure_time")
    if dt_str:
        dt = datetime.fromisoformat(dt_str)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    # Fallback: first analysis target_time
    analyses = snapshot_data.get("analyses", [])
    if analyses and "target_time" in analyses[0]:
        dt = datetime.fromisoformat(analyses[0]["target_time"])
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    target_date = snapshot_data.get("target_date", "")
    year, month, day = (int(x) for x in target_date.split("-"))
    return datetime(year, month, day, 9, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Alt advisory artifacts
# ---------------------------------------------------------------------------

def save_alt_advisory_artifacts(
    pack_dir: Path,
    manifest: RouteAdvisoriesManifest,
) -> None:
    """Persist alt-departure advisory artifacts to *pack_dir*."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    adv_path = pack_dir / "route_advisories_alt.json"
    adv_path.write_text(manifest.model_dump_json(indent=2))


def load_alt_advisory_artifacts(pack_dir: Path) -> RouteAdvisoriesManifest | None:
    """Load alt-departure advisories, returning *None* if missing."""
    adv_path = pack_dir / "route_advisories_alt.json"
    if not adv_path.exists():
        return None
    return RouteAdvisoriesManifest.model_validate_json(adv_path.read_text())
