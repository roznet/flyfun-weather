"""Dev-only golden-labeling workbench API (#254).

Admin-gated endpoints backing the ``/eval.html`` workbench page: list the
corpus, report coverage, and persist golden labels. The router is only mounted
when ``WEATHERBRIEF_EVAL_WORKBENCH`` is set (see ``api/app.py``), so these
routes do not exist at all in production.

The briefing view itself is *not* served from here — it is the standard
``/flight.html?id=eval-<corpus_id>`` page, which renders the corpus pack through
the existing flight/pack endpoints via the virtual-flight resolver. This router
only adds the list/coverage/label surface that the workbench needs on top.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from weatherbrief.api.admin import require_admin
from weatherbrief.eval_workbench import corpus
from weatherbrief.eval_workbench.config import (
    AREAS,
    eval_flight_id,
    eval_workbench_enabled,
)

router = APIRouter(prefix="/eval", tags=["eval-workbench"])


def require_workbench() -> None:
    """Guard: 404 when the workbench is disabled (defence in depth)."""
    if not eval_workbench_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _validate_area(area: str) -> str:
    if area not in AREAS:
        raise HTTPException(status_code=400, detail=f"area must be one of {AREAS}")
    return area


# --- response / request models ----------------------------------------------

class LabelModel(BaseModel):
    assessments: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    notes: str = ""
    priority: int | None = None  # 1=revalidate-first .. 4=skip; None=untriaged
    labeled_by: str = ""
    labeled_at: str = ""


class CorpusPackSummary(BaseModel):
    corpus_id: str
    flight_id: str  # eval-<corpus_id>; open /flight.html?id=<flight_id>
    area: str  # "staging" | "corpus"
    route: str
    target_date: str
    fetch_timestamp: str
    days_out: int
    assessment: str | None  # what the model produced (not ground truth)
    situations: list[str]
    faithful: bool
    is_labeled: bool
    # True when route_analyses.json is present — i.e. the cross-section / skew-T
    # render and advisories can be re-run. False for packs captured after T1
    # retention stripped the derived tier (label-only: judge from advisories +
    # digest, no visual cross-section).
    full_fidelity: bool
    # Pilot post-flight debrief (ground truth): did they fly? Full record via
    # GET /eval/packs/{id}/debrief.
    debriefed: bool
    debrief_decision: str | None
    debrief_graded: bool  # has cancel-reasons or per-category outcomes
    label: LabelModel | None


class CoverageRow(BaseModel):
    situation: str
    total: int
    labeled: int
    unlabeled: int
    corpus_ids: list[str]


class LabelRequest(BaseModel):
    assessments: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    notes: str = ""
    priority: int | None = None
    labeled_by: str = ""


def _to_summary(pack: corpus.CorpusPack) -> CorpusPackSummary:
    full_fidelity = (corpus.pack_path(pack.corpus_id, pack.area) / "route_analyses.json").exists()
    return CorpusPackSummary(
        corpus_id=pack.corpus_id,
        flight_id=eval_flight_id(pack.corpus_id),
        area=pack.area,
        full_fidelity=full_fidelity,
        debriefed=pack.meta.debriefed,
        debrief_decision=pack.meta.debrief_decision,
        debrief_graded=pack.meta.debrief_graded,
        route=pack.meta.route,
        target_date=pack.meta.target_date,
        fetch_timestamp=pack.meta.fetch_timestamp,
        days_out=pack.meta.days_out,
        assessment=pack.meta.assessment,
        situations=pack.meta.situations,
        faithful=pack.meta.faithful,
        is_labeled=pack.is_labeled,
        label=LabelModel(**pack.label.model_dump()) if pack.label else None,
    )


# --- endpoints --------------------------------------------------------------

@router.get("/packs", response_model=list[CorpusPackSummary])
def list_corpus_packs(
    area: str = Query("corpus"),
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """List packs in an area ("staging" | "corpus") with tags + label status."""
    return [_to_summary(p) for p in corpus.list_corpus(_validate_area(area))]


@router.get("/coverage", response_model=list[CoverageRow])
def get_coverage(
    area: str = Query("corpus"),
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Per-situation coverage grid for an area: total vs golden-labelled."""
    packs = corpus.list_corpus(_validate_area(area))
    return [CoverageRow(**row) for row in corpus.coverage_report(packs)]


@router.get("/packs/{corpus_id}", response_model=CorpusPackSummary)
def get_corpus_pack(
    corpus_id: str,
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Single pack (searched across areas): descriptor + golden label."""
    pack = corpus.find_pack(corpus_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"No corpus pack: {corpus_id}")
    return _to_summary(pack)


@router.post("/packs/{corpus_id}/label", response_model=LabelModel)
def save_corpus_label(
    corpus_id: str,
    body: LabelRequest,
    admin_id: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Write the SME's golden label for a pack (in whichever area it lives)."""
    pack = corpus.find_pack(corpus_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"No corpus pack: {corpus_id}")

    from datetime import datetime, timezone

    from pydantic import ValidationError

    # CorpusLabel normalizes + validates assessment values (GREEN/AMBER/RED);
    # surface a bad value as 422 rather than a 500.
    try:
        label = corpus.CorpusLabel(
            assessments=body.assessments,
            rationale=body.rationale.strip(),
            notes=body.notes.strip(),
            priority=body.priority,
            labeled_by=body.labeled_by.strip() or admin_id,
            labeled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid label: {exc}")
    corpus.save_label(corpus_id, label, pack.area)
    return LabelModel(**label.model_dump())


@router.post("/packs/{corpus_id}/promote", response_model=CorpusPackSummary)
def promote_corpus_pack(
    corpus_id: str,
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Move a labelled staging pack into the curated corpus (a directory move).

    Gated on the pack carrying a golden label. Committing the promoted pack to
    the eval-set git repo is a separate, manual step the curator does when happy.
    """
    try:
        return _to_summary(corpus.promote(corpus_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:  # no golden label yet
        raise HTTPException(status_code=409, detail=str(exc))
    except FileExistsError as exc:  # already promoted
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/packs/{corpus_id}/debrief")
def get_debrief(
    corpus_id: str,
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """The pilot's debrief record for this pack's flight (ground truth), or 404."""
    import json as _json

    pack = corpus.find_pack(corpus_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"No corpus pack: {corpus_id}")
    path = corpus.pack_path(corpus_id, pack.area) / "debrief.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No debrief for this flight")
    return _json.loads(path.read_text(encoding="utf-8"))


class RecalcDiffResponse(BaseModel):
    corpus_id: str
    area: str
    had_baseline: bool
    changed_count: int
    changes: list[dict]


@router.post("/packs/{corpus_id}/recalc-diff", response_model=RecalcDiffResponse)
def recalc_advisories_diff(
    corpus_id: str,
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Re-run advisories from the pack's saved analyses and diff vs the saved
    baseline (non-destructive). Surfaces which advisories changed rating, so an
    advisory-code change can be checked against real briefings."""
    from weatherbrief.eval_workbench import rerun

    pack = corpus.find_pack(corpus_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"No corpus pack: {corpus_id}")
    pack_dir = corpus.pack_path(corpus_id, pack.area)
    try:
        result = rerun.rerun_diff(pack_dir)
    except RuntimeError as exc:
        # Most commonly a T1-stripped pack missing cross_section.json.
        raise HTTPException(status_code=422, detail=f"Cannot re-run: {exc}")
    return RecalcDiffResponse(corpus_id=corpus_id, area=pack.area, **result)
