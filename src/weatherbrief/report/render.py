"""Render self-contained HTML and PDF briefing reports."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from jinja2 import Environment, PackageLoader

from weatherbrief.models import BriefingPackMeta, Flight

logger = logging.getLogger(__name__)


def _load_json(path: Path) -> dict | None:
    """Load a JSON file, returning None if missing."""
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _image_data_uri(path: Path) -> str | None:
    """Read an image file and return a base64 data URI, or None if missing."""
    if not path.exists():
        return None
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else f"image/{suffix.lstrip('.')}"
    return f"data:{mime};base64,{b64}"


def _build_template_context(
    pack_dir: Path,
    flight: Flight,
    pack: BriefingPackMeta,
) -> dict:
    """Assemble the Jinja2 template context from on-disk artifacts."""
    # Digest (structured JSON)
    digest = _load_json(pack_dir / "digest.json")

    # Snapshot (for model comparison + waypoint list)
    snapshot = _load_json(pack_dir / "snapshot.json")

    # GRAMET image
    gramet_uri = _image_data_uri(pack_dir / "gramet.png")

    # Skew-T images (ECMWF only) — one per waypoint
    skewt_images: list[dict] = []
    if snapshot and "route" in snapshot:
        for wp in snapshot["route"].get("waypoints", []):
            icao = wp["icao"]
            skewt_path = pack_dir / "skewt" / f"{icao}_ecmwf.png"
            uri = _image_data_uri(skewt_path)
            skewt_images.append({"icao": icao, "name": wp.get("name", icao), "uri": uri})

    # Route string
    if flight.waypoints:
        route_str = " → ".join(flight.waypoints)
    else:
        route_str = flight.route_name.replace("_", " → ").upper()

    # Altitude display
    alt_ft = flight.cruise_altitude_ft
    alt_str = f"FL{alt_ft // 100:03d}" if alt_ft >= 10000 else f"{alt_ft} ft"

    # Model comparison data (from snapshot analyses)
    comparison_waypoints: list[dict] = []
    if snapshot:
        for analysis in snapshot.get("analyses", []):
            divergences = analysis.get("model_divergence", [])
            if not divergences:
                continue
            models = list(divergences[0].get("model_values", {}).keys()) if divergences else []
            comparison_waypoints.append({
                "icao": analysis["waypoint"]["icao"],
                "name": analysis["waypoint"].get("name", ""),
                "models": models,
                "divergences": divergences,
            })

    return {
        "flight": flight,
        "pack": pack,
        "digest": digest,
        "gramet_uri": gramet_uri,
        "skewt_images": skewt_images,
        "route_str": route_str,
        "alt_str": alt_str,
        "comparison_waypoints": comparison_waypoints,
    }


def _get_template_env() -> Environment:
    """Create Jinja2 environment pointing to the templates/ subdir."""
    return Environment(
        loader=PackageLoader("weatherbrief.report", "templates"),
        autoescape=True,
    )


def render_html(
    pack_dir: Path,
    flight: Flight,
    pack: BriefingPackMeta,
) -> str:
    """Render a self-contained HTML briefing report.

    Loads artifacts from pack_dir, encodes images as base64 data URIs,
    renders the Jinja2 template.
    """
    env = _get_template_env()
    template = env.get_template("briefing.html")
    ctx = _build_template_context(pack_dir, flight, pack)
    return template.render(**ctx)


def render_pdf(
    pack_dir: Path,
    flight: Flight,
    pack: BriefingPackMeta,
) -> bytes:
    """Render PDF from HTML via WeasyPrint."""
    import weasyprint

    html = render_html(pack_dir, flight, pack)
    return weasyprint.HTML(string=html).write_pdf()
