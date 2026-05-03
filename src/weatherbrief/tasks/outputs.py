"""Output tasks — GRAMET, Skew-T, and LLM digest generation.

Extracted from ``pipeline.py`` (lines 683-827).  Each function returns an
independent result dataclass instead of mutating ``BriefingResult``.
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.digest.exceptions import classify_llm_exception
from weatherbrief.models import (
    Diagnostic,
    DigestCode,
    ForecastSnapshot,
    GrametCode,
    SkewtCode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GrametResult:
    path: Path | None = None
    fetched: bool = False
    failed: bool = False
    diagnostic: Diagnostic | None = None


@dataclass
class SkewtResult:
    paths: list[Path] = field(default_factory=list)
    diagnostic: Diagnostic | None = None


@dataclass
class DigestResult:
    path: Path | None = None
    text: str | None = None
    digest: object | None = None  # WeatherDigest
    llm_model: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    diagnostic: Diagnostic | None = None


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
    autorouter_token: str | None = None,
    user_id: str | None = None,
) -> GrametResult:
    """Fetch GRAMET cross-section PDF from the Autorouter API."""
    if not autorouter_token:
        # No credentials is an expected user state, not a pipeline event —
        # don't emit a diagnostic. The frontend GRAMET tab is the right
        # place to prompt the user to connect Autorouter.
        logger.debug("Skipping GRAMET: no autorouter token available")
        return GrametResult()

    try:
        from euro_aip.briefing.sources.autorouter_gramet import AutorouterGrametSource
        from euro_aip.utils.autorouter_credentials import AutorouterCredentialManager

        cache_dir = str(Path.home() / ".cache" / "weatherbrief")
        if user_id and data_dir:
            cache_dir = str(data_dir / ".cache" / "autorouter" / user_id)

        cred_manager = AutorouterCredentialManager(cache_dir)
        cred_manager.set_token(autorouter_token)

        gramet_client = AutorouterGrametSource(cred_manager)
        icao_codes = [wp.icao for wp in route.waypoints]
        duration_hours = route.flight_duration_hours or 2.0

        data = gramet_client.fetch_gramet(
            waypoints=icao_codes,
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
            # Internal bug — no actionable user message; persist as info
            # so it lands in fetch_meta.json for debugging without
            # triggering a banner.
            return GrametResult(diagnostic=Diagnostic.create(
                level="info",
                stage="gramet",
                code=GrametCode.GRAMET_NO_OUTPUT_DIR,
                message="GRAMET skipped — internal misconfiguration.",
                detail="run_gramet called without pack_dir or data_dir",
            ))

        out_path.write_bytes(data)
        logger.info("GRAMET saved: %s", out_path)
        return GrametResult(path=out_path, fetched=True)

    except ImportError as exc:
        # Server-side dependency missing; user can't act on this. Keep
        # info-level so it doesn't generate banner noise on every refresh
        # if euro_aip ever drops out of the deploy.
        logger.warning("GRAMET fetch requires euro_aip package")
        return GrametResult(diagnostic=Diagnostic.create(
            level="info",
            stage="gramet",
            code=GrametCode.GRAMET_NOT_AVAILABLE,
            message="GRAMET skipped — server is missing the euro_aip dependency.",
            detail=f"{type(exc).__name__}: {exc}",
        ))
    except Exception as exc:
        logger.warning("GRAMET fetch failed: %s", exc, exc_info=True)
        return GrametResult(failed=True, diagnostic=Diagnostic.create(
            level="warn",
            stage="gramet",
            code=GrametCode.GRAMET_FETCH_FAILED,
            message="GRAMET cross-section unavailable — fetch from Autorouter failed. Try refreshing again.",
            detail=traceback.format_exc(),
        ))


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
            # Internal bug — no actionable user message.
            return SkewtResult(diagnostic=Diagnostic.create(
                level="info",
                stage="skewt",
                code=SkewtCode.SKEWT_NO_OUTPUT_DIR,
                message="Skew-T skipped — internal misconfiguration.",
                detail="run_skewt called without pack_dir or data_dir",
            ))

        paths = generate_all_skewts(snapshot, target_time, out_dir)
        result_paths = [Path(p) for p in paths]
        for p in result_paths:
            logger.info("Skew-T saved: %s", p)
        return SkewtResult(paths=result_paths)

    except ImportError as exc:
        # Server-side dependency missing; user can't act on this.
        logger.warning("Skew-T generation requires metpy, numpy, matplotlib")
        return SkewtResult(diagnostic=Diagnostic.create(
            level="info",
            stage="skewt",
            code=SkewtCode.SKEWT_METPY_NOT_AVAILABLE,
            message="Skew-T skipped — server is missing the metpy dependency.",
            detail=f"{type(exc).__name__}: {exc}",
        ))
    except Exception as exc:
        logger.warning("Skew-T generation failed: %s", exc, exc_info=True)
        return SkewtResult(diagnostic=Diagnostic.create(
            level="warn",
            stage="skewt",
            code=SkewtCode.SKEWT_GENERATION_FAILED,
            message="Skew-T diagrams unavailable — generation failed. Try refreshing again.",
            detail=traceback.format_exc(),
        ))


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

        if digest_result.get("diagnostic") is not None:
            return DigestResult(diagnostic=digest_result["diagnostic"])

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
        # briefer_node classifies LLM-call failures; this outer except
        # catches everything *outside* the LLM call (config load, context
        # build, file-write errors, etc.). The classifier handles both
        # cases — anthropic.* exceptions get specific codes, anything else
        # falls through to DIGEST_UNKNOWN.
        diagnostic = classify_llm_exception(exc)
        logger.warning(
            "LLM digest generation failed (code=%s, error_id=%s)",
            diagnostic.code, diagnostic.error_id, exc_info=True,
        )
        return DigestResult(diagnostic=diagnostic)


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
