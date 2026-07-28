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
    CORPUS_META_FILE,
    LABEL_FILE,
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

# Names never copied from the source pack into the corpus dir: build noise, plus
# the corpus' own committed files. The golden label.json especially MUST survive
# a re-pull, and corpus_meta.json is regenerated after the copy — so a source
# pack that happened to contain either must never overwrite the corpus copy.
_SKIP_ON_COPY = {"__pycache__", LABEL_FILE, CORPUS_META_FILE}


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


def corpus_id_for(
    route: str, target_date: str, days_out: int, fetch_date: str, *, suffix: str = ""
) -> str:
    """Stable, anonymized corpus id (same shape as the eval fixture id).

    ``suffix`` disambiguates two *different* briefings that share the same
    (route, target, days-out, fetch day) — see :func:`disambiguated_corpus_id`.
    """
    route_slug = route.replace(" -> ", "_").replace(" ", "").lower()
    base = f"{route_slug}_{target_date}_d{days_out}_{fetch_date}"
    return f"{base}_{suffix}" if suffix else base


def pack_fetch_datetime(snapshot) -> datetime | None:
    """The pack's real (sub-day) fetch time, from the earliest forecast fetch.

    ``snapshot.fetch_date`` is day-precision; the per-forecast ``fetched_at``
    carries the actual briefing time, which is what distinguishes an 07:01 run
    from an 09:38 run of the same flight on the same day.
    """
    stamps = [
        f.fetched_at for f in getattr(snapshot, "forecasts", [])
        if getattr(f, "fetched_at", None) is not None
    ]
    return min(stamps) if stamps else None


def disambiguated_corpus_id(meta: CorpusMeta, area: str) -> str:
    """``meta.corpus_id``, or a time-suffixed variant if it's already taken.

    One corpus entry per (route, target, days-out, fetch *day*) is deliberate:
    re-pulling the same scenario rebuilds ``corpus_meta.json`` while preserving
    any ``label.json``. But that key silently collided for two *different*
    briefings of one flight on one day — e.g. an 07:01 LIFR-departure briefing
    and the 09:38 re-brief of the same flight, which graded RED and AMBER
    respectively. The second ingest overwrote the first and still reported it as
    "new", losing a labelled pack with no warning.

    So: same pack (identical content hash) keeps the bare id and overwrites
    itself as before; a genuinely different briefing gets ``_t<HHMM>``. The
    incumbent keeps the bare id — existing ids, committed corpus dirs and their
    labels stay valid, which a global switch to sub-day precision would break.

    Ids are checked across BOTH areas, not just the target one: ``find_pack``
    searches staging before corpus, so a staging pack sharing an id with an
    already-promoted pack would silently shadow it in the workbench.
    """
    holder = _id_holder(meta.corpus_id)
    if holder is None or not meta.source or holder[1] in ("", meta.source):
        return meta.corpus_id  # free, unreadable, or the same scenario again

    stamp = _parse_iso(meta.fetch_timestamp)
    suffixes = [f"t{stamp:%H%M}", f"t{stamp:%H%M%S}"] if stamp else []
    suffixes.append(f"t{meta.source.split(':')[-1][:6]}")
    for suffix in suffixes:
        candidate = f"{meta.corpus_id}_{suffix}"
        other = _id_holder(candidate)
        if other is None or other[1] in ("", meta.source):
            return candidate
    raise ValueError(
        f"cannot place pack {meta.source} — {meta.corpus_id} and every "
        f"disambiguated id are held by different packs"
    )


def _id_holder(corpus_id: str) -> tuple[str, str] | None:
    """``(area, source)`` of whichever area holds ``corpus_id``, or None.

    Staging is searched first, matching ``find_pack``'s precedence.
    """
    for area in ("staging", "corpus"):
        meta_path = pack_path(corpus_id, area) / CORPUS_META_FILE
        if not meta_path.exists():
            continue
        try:
            return area, json.loads(meta_path.read_text()).get("source", "")
        except (OSError, json.JSONDecodeError):
            return area, ""
    return None


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


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
    # One corpus entry per (route, target, days-out, fetch *day*). Day precision
    # is intentional: re-pulling the same scenario rebuilds corpus_meta.json but
    # preserves any label.json (it's in _SKIP_ON_COPY). Sub-day fetch precision
    # would fragment the corpus without adding a meaningfully different scenario.
    corpus_id = corpus_id_for(route, snapshot.target_date, snapshot.days_out,
                              snapshot.fetch_date)

    if snapshot.departure_time and isinstance(snapshot.departure_time, datetime):
        departure_time = snapshot.departure_time.isoformat()
    else:
        departure_time = f"{snapshot.target_date}T09:00:00+00:00"

    # Cruise/ceiling: the real flight values live in the prod advisory baseline
    # (the snapshot doesn't carry them). Without this corpus_meta would default
    # to 8000/18000 and the resolver would synthesize the eval Flight at that
    # bogus cruise — the briefing cross-section would then draw the cruise line
    # at the wrong altitude, inconsistent with the advisories (computed at the
    # real cruise). Fall back to the snapshot, then the safe defaults.
    cruise_altitude_ft = (
        (advisories.cruise_altitude_ft if advisories and advisories.cruise_altitude_ft else None)
        or getattr(snapshot, "cruise_altitude_ft", None)
        or 8000
    )
    flight_ceiling_ft = (
        (advisories.flight_ceiling_ft if advisories and advisories.flight_ceiling_ft else None)
        or 18000
    )
    # Record the pack's real fetch time, not midnight: the resolver ignores it
    # for identity, but it is what tells two same-day briefings of one flight
    # apart (see disambiguated_corpus_id). Falls back to midnight when the
    # forecasts carry no fetched_at.
    fetched_at = pack_fetch_datetime(snapshot)
    fetch_timestamp = (
        fetched_at.isoformat() if fetched_at
        else f"{snapshot.fetch_date}T00:00:00+00:00"
    )

    return CorpusMeta(
        corpus_id=corpus_id,
        route=route,
        waypoints=waypoints,
        target_date=snapshot.target_date,
        fetch_date=snapshot.fetch_date,
        departure_time=departure_time,
        fetch_timestamp=fetch_timestamp,
        days_out=snapshot.days_out,
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        assessment=assessment,
        assessment_reason=digest.get("assessment_reason"),
        situations=situations,
        faithful=faithful,
        source=f"pack:{pack_content_hash(pack_dir)}",
        notes=notes,
    )


def _copy_artifacts(src: Path, dest: Path) -> None:
    """Copy pack artifacts into the corpus dir.

    The corpus' committed files (label.json, corpus_meta.json) are in
    ``_SKIP_ON_COPY``, so a re-pull never overwrites a golden label even if the
    source path overlapped a corpus dir.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in _SKIP_ON_COPY:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


# Heavy artifact gzipped in the committed corpus (~10× smaller). Read
# transparently by ``load_cross_sections`` via the .json.gz fallback.
def compact_corpus_pack(dest: Path) -> None:
    """Gzip ``cross_section.json`` -> ``cross_section.json.gz`` to shrink the
    committed master. No-op if already compacted or absent."""
    import gzip

    cs = dest / "cross_section.json"
    if not cs.exists():
        return
    gz = Path(str(cs) + ".gz")
    gz.write_bytes(gzip.compress(cs.read_bytes()))
    cs.unlink()


def ingest_pack(
    pack_dir: Path, *, notes: str = "", copy: bool = True, area: str = "staging"
) -> CorpusPack | None:
    """Build + persist a corpus entry from a pack dir. Preserves any label.

    New briefings land in ``staging`` by default — they are triaged/labelled
    there, then promoted into ``corpus``. Returns the CorpusPack, or None if the
    pack isn't labelable.
    """
    meta = build_corpus_meta(pack_dir, notes=notes)
    if meta is None:
        return None
    # Already promoted (or already staged) elsewhere? Re-ingesting would create a
    # second copy that shadows the labelled one, since find_pack spans both areas.
    held = _id_holder(meta.corpus_id)
    if held and held[0] != area and held[1] == meta.source and meta.source:
        logger.info(
            "pack %s already present in %s — skipping re-ingest into %s",
            meta.corpus_id, held[0], area,
        )
        return CorpusPack(
            corpus_id=meta.corpus_id,
            meta=meta,
            label=load_label(meta.corpus_id, held[0]),
            area=held[0],
        )

    # Never clobber a different briefing that happens to share the natural key.
    placed_id = disambiguated_corpus_id(meta, area)
    if placed_id != meta.corpus_id:
        logger.info(
            "corpus id %s is held by a different pack; storing this one as %s",
            meta.corpus_id, placed_id,
        )
        meta = meta.model_copy(update={"corpus_id": placed_id})
    dest = pack_path(meta.corpus_id, area)
    if copy:
        _copy_artifacts(pack_dir, dest)
        compact_corpus_pack(dest)  # gzip the heavy cross_section master
    save_corpus_meta(meta, area)  # written after copy so it isn't clobbered
    # Gitignored provenance breadcrumb: where this pack came from, so a later
    # sync can re-pull heavy artifacts (cross_section, ...) from prod if the
    # local source was a partial copy. Not committed (carries the user path).
    try:
        (dest / "_source.json").write_text(
            json.dumps({"source_pack_dir": str(pack_dir)}) + "\n", encoding="utf-8"
        )
    except OSError:
        pass
    return CorpusPack(
        corpus_id=meta.corpus_id,
        meta=meta,
        label=load_label(meta.corpus_id, area),
        area=area,
    )
