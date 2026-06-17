"""Virtual-flight resolver: rebuild a Flight + BriefingPackMeta from a corpus pack.

Three core read paths (``_load_flight_or_404``, ``load_pack_meta``,
``list_packs``) call into here when, and only when, the workbench is enabled and
the flight id is in the ``eval-`` namespace. Everything downstream — the
briefing view, advisories, digest, skew-T, etc. — then serves the corpus pack
through the *existing* endpoints with no per-endpoint changes, because
``_get_pack_dir`` resolves the directory from the synthesized meta's
``artifact_path``.

No database is touched: the Flight and BriefingPackMeta are built purely from
the committed ``corpus_meta.json`` descriptor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from weatherbrief.eval_workbench.config import corpus_id_from_flight_id, eval_flight_id
from weatherbrief.eval_workbench.corpus import CorpusMeta, load_corpus_meta, pack_path
from weatherbrief.models import BriefingPackMeta, Flight

# Synthetic owner for corpus flights. Never a real user; ownership checks are
# bypassed for eval flights (the workbench is admin-gated and dev-only).
EVAL_USER_ID = "eval"


def _parse_dt(value: str) -> datetime:
    """Parse an ISO timestamp, defaulting to aware UTC if naive."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def synthesize_flight(meta: CorpusMeta) -> Flight:
    """Build a Flight from a corpus descriptor (no DB)."""
    return Flight(
        id=eval_flight_id(meta.corpus_id),
        user_id=EVAL_USER_ID,
        route_name=meta.route,
        waypoints=list(meta.waypoints),
        departure_time=_parse_dt(meta.departure_time),
        cruise_altitude_ft=meta.cruise_altitude_ft,
        flight_ceiling_ft=meta.flight_ceiling_ft,
        private=False,
        created_at=datetime.now(timezone.utc),
    )


def synthesize_pack_meta(meta: CorpusMeta) -> BriefingPackMeta:
    """Build a BriefingPackMeta pointing at the on-disk corpus pack dir."""
    return BriefingPackMeta(
        flight_id=eval_flight_id(meta.corpus_id),
        fetch_timestamp=_parse_dt(meta.fetch_timestamp),
        days_out=meta.days_out,
        has_digest=meta.assessment is not None,
        llm_digest_requested=True,
        assessment=meta.assessment,
        assessment_reason=meta.assessment_reason,
        artifact_path=str(pack_path(meta.corpus_id).resolve()),
    )


def resolve_eval_flight(flight_id: str) -> Flight:
    """``_load_flight_or_404`` hook. Raises FileNotFoundError if unknown."""
    meta = load_corpus_meta(corpus_id_from_flight_id(flight_id))
    return synthesize_flight(meta)


def resolve_eval_pack_meta(flight_id: str, fetch_timestamp=None) -> BriefingPackMeta:
    """``load_pack_meta`` hook.

    There is exactly one pack per corpus id, so ``fetch_timestamp`` is accepted
    for signature parity but not used to disambiguate. Raises FileNotFoundError
    if the corpus id is unknown.
    """
    meta = load_corpus_meta(corpus_id_from_flight_id(flight_id))
    return synthesize_pack_meta(meta)


def resolve_eval_pack_list(flight_id: str) -> list[BriefingPackMeta]:
    """``list_packs`` hook. One-element list (or empty if unknown)."""
    try:
        return [resolve_eval_pack_meta(flight_id)]
    except FileNotFoundError:
        return []
