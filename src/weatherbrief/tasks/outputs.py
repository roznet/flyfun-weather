"""Output tasks — GRAMET, Skew-T, and LLM digest generation.

Extracted from ``pipeline.py`` (lines 683-827).  Each function returns an
independent result dataclass instead of mutating ``BriefingResult``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.models import ForecastSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GrametResult:
    path: Path | None = None
    fetched: bool = False
    failed: bool = False
    error: str | None = None


@dataclass
class SkewtResult:
    paths: list[Path] = field(default_factory=list)
    error: str | None = None


@dataclass
class DigestResult:
    path: Path | None = None
    text: str | None = None
    digest: object | None = None  # WeatherDigest
    llm_model: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# GRAMET
# ---------------------------------------------------------------------------

def run_gramet(
    route,  # RouteConfig
    departure_time: datetime,
    pack_dir: Path | None = None,
    data_dir: Path | None = None,
    days_out: int = 0,
    fetch_date: str = "",
    autorouter_credentials: tuple[str, str] | None = None,
    user_id: str | None = None,
) -> GrametResult:
    """Fetch GRAMET cross-section PDF from the Autorouter API."""
    try:
        from weatherbrief.fetch.gramet import AutorouterGramet
        icao_codes = [wp.icao for wp in route.waypoints]
        duration_hours = route.flight_duration_hours or 2.0

        kwargs: dict = {}
        if autorouter_credentials:
            kwargs["username"], kwargs["password"] = autorouter_credentials
            if user_id and data_dir:
                kwargs["cache_dir"] = str(data_dir / ".cache" / "autorouter" / user_id)
        gramet_client = AutorouterGramet(**kwargs)
        data = gramet_client.fetch_gramet(
            icao_codes=icao_codes,
            altitude_ft=route.cruise_altitude_ft,
            departure_time=departure_time,
            duration_hours=duration_hours,
            fmt="pdf",
        )

        if pack_dir:
            out_path = pack_dir / "gramet.pdf"
        elif data_dir:
            target_date_str = departure_time.strftime("%Y-%m-%d")
            out_dir = data_dir / "gramet" / target_date_str / f"d-{days_out}_{fetch_date}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "gramet.pdf"
        else:
            return GrametResult(error="No output directory specified")

        out_path.write_bytes(data)
        logger.info("GRAMET saved: %s", out_path)
        return GrametResult(path=out_path, fetched=True)

    except ImportError:
        logger.warning("GRAMET fetch requires euro_aip with autorouter credentials")
        return GrametResult(error="GRAMET: euro_aip not available")
    except Exception as exc:
        logger.warning("GRAMET fetch failed: %s", exc, exc_info=True)
        return GrametResult(failed=True, error=f"GRAMET: {exc}")


# ---------------------------------------------------------------------------
# Skew-T
# ---------------------------------------------------------------------------

def run_skewt(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    pack_dir: Path | None = None,
    data_dir: Path | None = None,
) -> SkewtResult:
    """Generate Skew-T plots for all waypoints."""
    try:
        from weatherbrief.digest.skewt import generate_all_skewts

        if pack_dir:
            out_dir = pack_dir / "skewt"
        elif data_dir:
            out_dir = (
                data_dir / "skewt" / snapshot.target_date
                / f"d-{snapshot.days_out}_{snapshot.fetch_date}"
            )
        else:
            return SkewtResult(error="No output directory specified")

        paths = generate_all_skewts(snapshot, target_time, out_dir)
        result_paths = [Path(p) for p in paths]
        for p in result_paths:
            logger.info("Skew-T saved: %s", p)
        return SkewtResult(paths=result_paths)

    except ImportError:
        logger.warning("Skew-T generation requires metpy, numpy, matplotlib")
        return SkewtResult(error="Skew-T: metpy not available")
    except Exception as exc:
        logger.warning("Skew-T generation failed: %s", exc, exc_info=True)
        return SkewtResult(error=f"Skew-T: {exc}")


# ---------------------------------------------------------------------------
# LLM digest
# ---------------------------------------------------------------------------

def run_llm_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    digest_config_name: str | None = None,
    pack_dir: Path | None = None,
    data_dir: Path | None = None,
    route_advisories=None,  # RouteAdvisoriesManifest | None
    flight_rules: str | None = None,
    previous_digest=None,  # WeatherDigest | None
    locale: str | None = None,
    profile_id: int | None = None,
    profile_name: str | None = None,
    guidance_key: str | None = None,
) -> DigestResult:
    """Generate LLM-powered weather digest."""
    try:
        from weatherbrief.digest.llm_config import load_digest_config
        from weatherbrief.digest.llm_digest import run_digest

        config = load_digest_config(digest_config_name)
        logger.info("LLM digest: %s/%s", config.llm.provider, config.llm.model)

        digest_result = run_digest(
            snapshot, target_time, config,
            previous_digest=previous_digest,
            route_advisories=route_advisories,
            flight_rules=flight_rules,
            locale=locale,
            guidance_key=guidance_key,
        )

        if digest_result.get("error"):
            return DigestResult(error=f"LLM digest: {digest_result['error']}")

        digest_obj = digest_result.get("digest")
        llm_model = f"{config.llm.provider}:{config.llm.model}"
        llm_input_tokens = digest_result.get("llm_input_tokens")
        llm_output_tokens = digest_result.get("llm_output_tokens")

        # Save markdown + structured JSON digest
        digest_path: Path | None = None
        if pack_dir:
            md_path = pack_dir / "digest.md"
            json_path = pack_dir / "digest.json"
        elif data_dir:
            out_dir = (
                data_dir / "digests" / snapshot.target_date
                / f"d-{snapshot.days_out}_{snapshot.fetch_date}"
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            md_path = out_dir / "digest.md"
            json_path = out_dir / "digest.json"
        else:
            # No output dir — return result without saving
            return DigestResult(
                text=digest_result["digest_text"],
                digest=digest_obj,
                llm_model=llm_model,
                llm_input_tokens=llm_input_tokens,
                llm_output_tokens=llm_output_tokens,
            )

        md_path.write_text(digest_result["digest_text"])
        if digest_obj is not None:
            digest_data = digest_obj.model_dump()
            # Include profile metadata for tracking which profile was active
            if profile_id is not None:
                digest_data["profile_id"] = profile_id
            if profile_name is not None:
                digest_data["profile_name"] = profile_name
            if guidance_key is not None:
                digest_data["digest_guidance"] = guidance_key
            json_path.write_text(json.dumps(digest_data, indent=2))
        digest_path = md_path
        logger.info("LLM digest saved: %s", md_path)

        # Save translated DWD overview if available
        dwd_translated = digest_result.get("dwd_translated")
        if dwd_translated and pack_dir:
            _save_dwd_overview(pack_dir, dwd_translated)

        return DigestResult(
            path=digest_path,
            text=digest_result["digest_text"],
            digest=digest_obj,
            llm_model=llm_model,
            llm_input_tokens=llm_input_tokens,
            llm_output_tokens=llm_output_tokens,
        )

    except Exception as exc:
        logger.warning("LLM digest generation failed: %s", exc, exc_info=True)
        return DigestResult(error=f"LLM digest: {exc}")


def _save_dwd_overview(pack_dir: Path, translated: list) -> None:
    """Save translated DWD overview to pack directory for frontend display."""
    try:
        entries = []
        for block, english in translated:
            entries.append({
                "day_name_de": block.day_name_de,
                "date_iso": block.date_iso.isoformat() if block.date_iso else None,
                "source": block.source,
                "text_en": english,
                "text_de": block.text,
            })
        overview = {
            "source": "DWD Synoptic Overview",
            "coverage": "Germany / Central Europe",
            "entries": entries,
        }
        path = pack_dir / "dwd_overview.json"
        path.write_text(json.dumps(overview, ensure_ascii=False, indent=2))
        logger.info("DWD overview saved: %s", path)
    except Exception:
        logger.warning("Failed to save DWD overview", exc_info=True)
