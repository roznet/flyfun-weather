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


def _pdf_first_page_data_uri(path: Path) -> str | None:
    """Render the first page of a PDF as a PNG data URI.

    Uses PyMuPDF (fitz) if available, otherwise returns None.
    """
    if not path.exists():
        return None
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        page = doc[0]
        # Render at 2x for crisp output
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        png_data = pix.tobytes("png")
        doc.close()
        b64 = base64.b64encode(png_data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except ImportError:
        logger.debug("PyMuPDF not available; skipping GRAMET PDF conversion for report")
        return None
    except Exception:
        logger.warning("Failed to convert GRAMET PDF to PNG", exc_info=True)
        return None


def _generate_waypoint_skewt(
    forecasts_data: dict, target_dt, icao: str, model: str, output_path: Path,
) -> None:
    """Generate a Skew-T PNG on-demand from forecast data."""
    try:
        from weatherbrief.digest.skewt import generate_skewt
        from weatherbrief.models import WaypointForecast

        for wf_data in forecasts_data.get("forecasts", []):
            if wf_data.get("waypoint", {}).get("icao") == icao and wf_data.get("model") == model:
                wf = WaypointForecast.model_validate(wf_data)
                hourly = wf.at_time(target_dt)
                if hourly and hourly.pressure_levels:
                    generate_skewt(hourly, icao, model, output_path)
                return
    except Exception:
        logger.warning("On-demand Skew-T generation failed for %s/%s", icao, model, exc_info=True)


def _build_template_context(
    pack_dir: Path,
    flight: Flight,
    pack: BriefingPackMeta,
) -> dict:
    """Assemble the Jinja2 template context from on-disk artifacts."""
    # Digest (structured JSON)
    digest = _load_json(pack_dir / "digest.json")

    # Load split files (briefing.json + forecasts.json), fallback to snapshot.json
    from weatherbrief.tasks.artifacts import load_briefing, load_forecasts

    briefing = load_briefing(pack_dir)
    forecasts_data = load_forecasts(pack_dir)

    # GRAMET image — try PNG first (embeddable in HTML <img>), then convert PDF
    gramet_uri = _image_data_uri(pack_dir / "gramet.png")
    if not gramet_uri:
        gramet_uri = _pdf_first_page_data_uri(pack_dir / "gramet.pdf")

    # Parse target time for on-demand Skew-T generation
    target_dt = None
    if briefing or forecasts_data:
        try:
            from weatherbrief.api.packs import _parse_target_time
            target_dt = _parse_target_time(briefing or forecasts_data)
        except (ValueError, KeyError):
            pass

    # Skew-T images (ECMWF only) — one per waypoint, generated on-demand
    skewt_images: list[dict] = []
    if briefing and "route" in briefing:
        for wp in briefing["route"].get("waypoints", []):
            icao = wp["icao"]
            skewt_path = pack_dir / "skewt" / f"{icao}_ecmwf.png"
            if not skewt_path.exists() and forecasts_data and target_dt:
                _generate_waypoint_skewt(forecasts_data, target_dt, icao, "ecmwf", skewt_path)
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

    # Model comparison data (from briefing analyses)
    comparison_waypoints: list[dict] = []
    if briefing:
        for analysis in briefing.get("analyses", []):
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

    # Route advisories (advisory results + catalog + airport conditions)
    advisories_data = _load_json(pack_dir / "route_advisories.json")
    advisories: list[dict] = []
    catalog: dict[str, dict] = {}
    airport_conditions: dict | None = None
    if advisories_data:
        catalog = {e["id"]: e for e in advisories_data.get("catalog", [])}
        for adv in advisories_data.get("advisories", []):
            entry = catalog.get(adv["advisory_id"], {})
            advisories.append({
                "name": entry.get("name", adv["advisory_id"]),
                "category": entry.get("category", ""),
                "aggregate_status": adv["aggregate_status"],
                "aggregate_detail": adv.get("aggregate_detail", ""),
                "per_model": adv.get("per_model", []),
            })
        airport_conditions = advisories_data.get("airport_conditions")

    # Route observations (METAR/TAF) + SIGMETs (from D-0 flights)
    route_observations = None
    route_sigmets = None
    # Observed conditions (#574): radar, lightning and satellite cloud tops.
    # The PDF carries the deterministic summary and, required by the source
    # licences, each frame's own attribution — read from the frame itself
    # rather than stamped from a constant, because the producer varies.
    observed_conditions = None
    if briefing:
        route_observations = briefing.get("route_observations")
        route_sigmets = briefing.get("route_sigmets")
        observed_conditions = briefing.get("observed_conditions")

    return {
        "flight": flight,
        "pack": pack,
        "digest": digest,
        "gramet_uri": gramet_uri,
        "skewt_images": skewt_images,
        "route_str": route_str,
        "alt_str": alt_str,
        "comparison_waypoints": comparison_waypoints,
        "advisories": advisories,
        "airport_conditions": airport_conditions,
        "route_observations": route_observations,
        "route_sigmets": route_sigmets,
        "observed_conditions": observed_conditions,
        "observed_attributions": _observed_attributions(observed_conditions),
    }


def _observed_attributions(observed: dict | None) -> list[str]:
    """Distinct attribution lines across the observed fields present.

    De-duplicated because the two OPERA products usually come from the same
    producer under the same licence, and repeating the line four times in a
    footer helps nobody. Kept as the frame's own ``text`` verbatim — the whole
    point of reading provenance per frame is not to paraphrase it.
    """
    if not observed:
        return []
    lines: list[str] = []
    for key in ("reflectivity", "rain_rate", "cloud_tops", "lightning"):
        field = observed.get(key)
        if not field:
            continue
        text = (field.get("attribution") or {}).get("text")
        if text and text not in lines:
            lines.append(text)
    return lines


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
