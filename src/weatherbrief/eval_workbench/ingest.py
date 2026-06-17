"""Build corpus packs from on-disk briefing packs (script-side, not hot-path).

This module turns a production/dev pack directory into a corpus entry: it loads
the snapshot, reconstructs (or reads the persisted) LLM context to tag
situations, derives the anonymized ``CorpusMeta``, and copies the pack
artifacts into the corpus directory. ``scripts/pull_eval_corpus.py`` and
``scripts/export_eval_candidates.py`` are thin CLIs over these helpers.

Heavier imports (``build_digest_context``) are done lazily so importing this
module (and the package) stays cheap for the API/resolver path and unit tests.
``extract_digest_eval.py`` reuses ``load_dwd_translated`` + ``load_pack_context``
so the eval fixtures and the labelling corpus tag context identically.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path

from weatherbrief.eval_workbench.corpus import (
    CorpusMeta,
    CorpusPack,
    load_label,
    pack_path,
    save_corpus_meta,
)
from weatherbrief.eval_workbench.situations import (
    classify_situations,
    extract_advisory_summary,
    load_snapshot_from_pack,
)
from weatherbrief.models import RouteAdvisoriesManifest

logger = logging.getLogger(__name__)

# Pack artifacts that are pure derived images / bulky and not needed to render
# the briefing view from the corpus. Skipped on copy to keep corpus dirs lean.
_SKIP_ON_COPY = {"__pycache__"}


def load_dwd_translated(pack_dir: Path) -> list | None:
    """Reconstruct ``dwd_translated`` from a saved ``dwd_overview.json``.

    Returns ``[(DWDDayBlock, english), ...]`` so a reconstructed context can
    splice the DWD section back in, or ``None`` if the pack has no overview.
    """
    overview_path = pack_dir / "dwd_overview.json"
    if not overview_path.exists():
        return None
    try:
        from weatherbrief.fetch.dwd_text import DWDDayBlock

        overview = json.loads(overview_path.read_text(encoding="utf-8"))
        out = []
        for entry in overview.get("entries", []):
            iso = entry.get("date_iso")
            block = DWDDayBlock(
                day_name_de=entry.get("day_name_de", ""),
                date_iso=date.fromisoformat(iso) if iso else None,
                text=entry.get("text_de", "") or "",
                source=entry.get("source", "") or "",
            )
            out.append((block, entry.get("text_en", "") or ""))
        return out or None
    except Exception:
        logger.debug("load_dwd_translated failed for %s", pack_dir, exc_info=True)
        return None


def load_pack_context(
    pack_dir: Path, snapshot, advisories, *, flight_rules: str = "vfr_ifr"
) -> tuple[str, bool, str]:
    """Return ``(context, faithful, source)`` for a pack.

    Prefers the persisted ``digest_context.txt`` (byte-faithful to what the LLM
    saw). Falls back to best-effort reconstruction for older packs, splicing the
    DWD overview back in when available — those are tagged ``faithful=False``.
    """
    persisted = pack_dir / "digest_context.txt"
    if persisted.exists():
        return persisted.read_text(encoding="utf-8"), True, "persisted"

    from weatherbrief.digest.prompt_builder import build_digest_context

    if snapshot.departure_time and isinstance(snapshot.departure_time, datetime):
        target_time = snapshot.departure_time
    else:
        target_time = datetime.fromisoformat(f"{snapshot.target_date}T09:00:00")

    context = build_digest_context(
        snapshot, target_time,
        route_advisories=advisories,
        dwd_translated=load_dwd_translated(pack_dir),
        flight_rules=flight_rules,
    )
    return context, False, "reconstructed"


def pack_content_hash(pack_dir: Path) -> str:
    """Short content hash of the briefing payload — anonymized provenance."""
    briefing = pack_dir / "briefing.json"
    data = briefing.read_bytes() if briefing.exists() else pack_dir.name.encode()
    return hashlib.sha1(data).hexdigest()[:12]


def corpus_id_for(route: str, target_date: str, days_out: int, fetch_date: str) -> str:
    """Stable, anonymized corpus id (same shape as the eval fixture id)."""
    route_slug = route.replace(" -> ", "_").replace(" ", "").lower()
    return f"{route_slug}_{target_date}_d{days_out}_{fetch_date}"


def build_corpus_meta(pack_dir: Path, *, notes: str = "") -> CorpusMeta | None:
    """Derive a CorpusMeta from a pack dir, or None if it isn't labelable.

    Returns None for packs without snapshot data and for long-range outlook
    packs (no GREEN/AMBER/RED assessment — a different eval track).
    """
    snapshot = load_snapshot_from_pack(pack_dir)
    if snapshot is None:
        return None

    digest_path = pack_dir / "digest.json"
    if not digest_path.exists():
        return None
    digest = json.loads(digest_path.read_text())
    assessment = digest.get("assessment")
    if "outlook" in digest or assessment not in ("GREEN", "AMBER", "RED"):
        return None  # long-range outlook — out of scope for G/A/R labelling

    adv_path = pack_dir / "route_advisories.json"
    advisories = None
    if adv_path.exists():
        advisories = RouteAdvisoriesManifest.model_validate(
            json.loads(adv_path.read_text())
        )

    context, faithful, _ = load_pack_context(pack_dir, snapshot, advisories)
    adv_summary = extract_advisory_summary(adv_path)
    situations = classify_situations(
        snapshot, adv_summary, context, snapshot.days_out
    )

    route = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    waypoints = [wp.icao for wp in snapshot.route.waypoints]
    corpus_id = corpus_id_for(route, snapshot.target_date, snapshot.days_out,
                              snapshot.fetch_date)

    if snapshot.departure_time and isinstance(snapshot.departure_time, datetime):
        departure_time = snapshot.departure_time.isoformat()
    else:
        departure_time = f"{snapshot.target_date}T09:00:00+00:00"
    # Corpus is keyed by corpus_id; the resolver ignores ts for disambiguation,
    # so a date-precision fetch_timestamp is sufficient identity for the view.
    fetch_timestamp = f"{snapshot.fetch_date}T00:00:00+00:00"

    return CorpusMeta(
        corpus_id=corpus_id,
        route=route,
        waypoints=waypoints,
        target_date=snapshot.target_date,
        fetch_date=snapshot.fetch_date,
        departure_time=departure_time,
        fetch_timestamp=fetch_timestamp,
        days_out=snapshot.days_out,
        cruise_altitude_ft=getattr(snapshot, "cruise_altitude_ft", 8000) or 8000,
        assessment=assessment,
        assessment_reason=digest.get("assessment_reason"),
        situations=situations,
        faithful=faithful,
        source=f"pack:{pack_content_hash(pack_dir)}",
        notes=notes,
    )


def _copy_artifacts(src: Path, dest: Path) -> None:
    """Copy pack artifacts into the corpus dir, preserving an existing label."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in _SKIP_ON_COPY:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def ingest_pack(pack_dir: Path, *, notes: str = "", copy: bool = True) -> CorpusPack | None:
    """Build + persist a corpus entry from a pack dir. Preserves any label.

    Returns the CorpusPack, or None if the pack isn't labelable.
    """
    meta = build_corpus_meta(pack_dir, notes=notes)
    if meta is None:
        return None
    dest = pack_path(meta.corpus_id)
    if copy:
        _copy_artifacts(pack_dir, dest)
    save_corpus_meta(meta)  # written after copy so it isn't clobbered
    return CorpusPack(
        corpus_id=meta.corpus_id, meta=meta, label=load_label(meta.corpus_id)
    )
