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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from weatherbrief.api.admin import require_admin
from weatherbrief.eval_workbench import corpus
from weatherbrief.eval_workbench.config import eval_flight_id, eval_workbench_enabled

router = APIRouter(prefix="/eval", tags=["eval-workbench"])


def require_workbench() -> None:
    """Guard: 404 when the workbench is disabled (defence in depth)."""
    if not eval_workbench_enabled():
        raise HTTPException(status_code=404, detail="Not found")


# --- response / request models ----------------------------------------------

class LabelModel(BaseModel):
    assessments: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    notes: str = ""
    labeled_by: str = ""
    labeled_at: str = ""


class CorpusPackSummary(BaseModel):
    corpus_id: str
    flight_id: str  # eval-<corpus_id>; open /flight.html?id=<flight_id>
    route: str
    target_date: str
    fetch_timestamp: str
    days_out: int
    assessment: str | None  # what the model produced (not ground truth)
    situations: list[str]
    faithful: bool
    is_labeled: bool
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
    labeled_by: str = ""


def _to_summary(pack: corpus.CorpusPack) -> CorpusPackSummary:
    return CorpusPackSummary(
        corpus_id=pack.corpus_id,
        flight_id=eval_flight_id(pack.corpus_id),
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
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """List every corpus pack with its situation tags + label status."""
    return [_to_summary(p) for p in corpus.list_corpus()]


@router.get("/coverage", response_model=list[CoverageRow])
def get_coverage(
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Per-situation coverage grid: total vs golden-labelled per matrix cell."""
    return [CoverageRow(**row) for row in corpus.coverage_report()]


@router.get("/packs/{corpus_id}", response_model=CorpusPackSummary)
def get_corpus_pack(
    corpus_id: str,
    _admin: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Single corpus pack: descriptor + current golden label (for the panel)."""
    if not corpus.corpus_exists(corpus_id):
        raise HTTPException(status_code=404, detail=f"No corpus pack: {corpus_id}")
    return _to_summary(corpus.load_pack(corpus_id))


@router.post("/packs/{corpus_id}/label", response_model=LabelModel)
def save_corpus_label(
    corpus_id: str,
    body: LabelRequest,
    admin_id: str = Depends(require_admin),
    _gate: None = Depends(require_workbench),
):
    """Write the SME's golden label for a corpus pack to ``label.json``."""
    if not corpus.corpus_exists(corpus_id):
        raise HTTPException(status_code=404, detail=f"No corpus pack: {corpus_id}")

    from datetime import datetime, timezone

    label = corpus.CorpusLabel(
        assessments={k: v.upper() for k, v in body.assessments.items() if v},
        rationale=body.rationale.strip(),
        notes=body.notes.strip(),
        labeled_by=body.labeled_by.strip() or admin_id,
        labeled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    corpus.save_label(corpus_id, label)
    return LabelModel(**label.model_dump())
