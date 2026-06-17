"""On-disk corpus of pulled packs + golden labels.

Each corpus entry lives at ``<EVAL_CORPUS_DIR>/<corpus_id>/`` and holds:

* the copied pack artifacts (``briefing.json``, ``forecasts.json``,
  ``route_advisories.json``, ``digest.json``, ``digest_context.txt``,
  sounding/chart sidecars, ...) — gitignored, re-pullable from prod;
* ``corpus_meta.json`` — the small, anonymized descriptor the virtual-flight
  resolver rebuilds a Flight + BriefingPackMeta from (committed);
* ``label.json`` — the SME's golden labels (committed). Absent until labelled.

Only ``corpus_meta.json`` + ``label.json`` are committed; see ``.gitignore``.
The heavy artifacts are reproducible via ``scripts/pull_eval_corpus.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from weatherbrief.eval_workbench.config import eval_corpus_dir, eval_flight_id
from weatherbrief.eval_workbench.situations import SITUATION_VOCAB

CORPUS_META_FILE = "corpus_meta.json"
LABEL_FILE = "label.json"

# The guidance presets a golden label carries an assessment for.
GUIDANCES: tuple[str, ...] = ("conservative", "balanced", "tolerant")


class CorpusMeta(BaseModel):
    """Anonymized descriptor for one corpus pack (committed).

    Carries exactly what the resolver needs to synthesize a Flight +
    BriefingPackMeta, plus the situation tags for coverage. No user identity is
    stored — ``source`` is a free-text provenance breadcrumb (e.g. a content
    hash), never a real user id.
    """

    corpus_id: str
    route: str  # "EGTF -> LFAT"
    waypoints: list[str] = Field(default_factory=list)
    target_date: str  # YYYY-MM-DD
    fetch_date: str  # YYYY-MM-DD
    departure_time: str  # ISO8601 (aware UTC)
    fetch_timestamp: str  # ISO8601 (aware UTC) — the pack's identity
    days_out: int
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    assessment: str | None = None  # GREEN/AMBER/RED the model produced
    assessment_reason: str | None = None
    situations: list[str] = Field(default_factory=list)
    faithful: bool = True  # persisted (byte-faithful) context vs reconstructed
    source: str = ""  # anonymized provenance breadcrumb
    notes: str = ""  # optional curator note (why this pack is interesting)


class CorpusLabel(BaseModel):
    """Golden labels assigned by the SME (committed)."""

    assessments: dict[str, str] = Field(default_factory=dict)  # guidance -> G/A/R
    rationale: str = ""
    notes: str = ""
    labeled_by: str = ""
    labeled_at: str = ""

    @property
    def is_complete(self) -> bool:
        """True when every guidance preset has an assessment."""
        return all(self.assessments.get(g) for g in GUIDANCES)


class CorpusPack(BaseModel):
    """A corpus entry: its descriptor + current label state."""

    corpus_id: str
    meta: CorpusMeta
    label: CorpusLabel | None = None

    @property
    def flight_id(self) -> str:
        return eval_flight_id(self.corpus_id)

    @property
    def is_labeled(self) -> bool:
        return self.label is not None and bool(self.label.assessments)


# --- path helpers -----------------------------------------------------------

def corpus_root() -> Path:
    return eval_corpus_dir()


def pack_path(corpus_id: str) -> Path:
    return corpus_root() / corpus_id


def _meta_path(corpus_id: str) -> Path:
    return pack_path(corpus_id) / CORPUS_META_FILE


def _label_path(corpus_id: str) -> Path:
    return pack_path(corpus_id) / LABEL_FILE


# --- read -------------------------------------------------------------------

def corpus_exists(corpus_id: str) -> bool:
    return _meta_path(corpus_id).exists()


def load_corpus_meta(corpus_id: str) -> CorpusMeta:
    """Load a corpus pack descriptor. Raises FileNotFoundError if missing."""
    path = _meta_path(corpus_id)
    if not path.exists():
        raise FileNotFoundError(f"No corpus pack: {corpus_id}")
    return CorpusMeta.model_validate_json(path.read_text(encoding="utf-8"))


def load_label(corpus_id: str) -> CorpusLabel | None:
    """Load the golden label, or None if the pack is unlabelled."""
    path = _label_path(corpus_id)
    if not path.exists():
        return None
    return CorpusLabel.model_validate_json(path.read_text(encoding="utf-8"))


def load_pack(corpus_id: str) -> CorpusPack:
    return CorpusPack(
        corpus_id=corpus_id,
        meta=load_corpus_meta(corpus_id),
        label=load_label(corpus_id),
    )


def list_corpus() -> list[CorpusPack]:
    """All corpus packs, sorted by corpus_id. Skips dirs without a descriptor."""
    root = corpus_root()
    if not root.exists():
        return []
    out: list[CorpusPack] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / CORPUS_META_FILE).exists():
            out.append(load_pack(child.name))
    return out


# --- write ------------------------------------------------------------------

def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def save_corpus_meta(meta: CorpusMeta) -> None:
    _write_json(_meta_path(meta.corpus_id), meta.model_dump())


def save_label(corpus_id: str, label: CorpusLabel) -> Path:
    """Persist the golden label for a corpus pack. Returns the file path."""
    if not corpus_exists(corpus_id):
        raise FileNotFoundError(f"No corpus pack: {corpus_id}")
    path = _label_path(corpus_id)
    _write_json(path, label.model_dump())
    return path


# --- coverage ---------------------------------------------------------------

def coverage_report(packs: list[CorpusPack] | None = None) -> list[dict]:
    """Per-situation coverage over the matrix vocab.

    Returns one row per SITUATION_VOCAB cell with how many corpus packs carry
    that tag and how many of those are golden-labelled — the checklist that
    turns "come up with golden labels" into "fill the empty cells".
    """
    if packs is None:
        packs = list_corpus()
    rows: list[dict] = []
    for cell in SITUATION_VOCAB:
        tagged = [p for p in packs if cell in p.meta.situations]
        labeled = [p for p in tagged if p.is_labeled]
        rows.append({
            "situation": cell,
            "total": len(tagged),
            "labeled": len(labeled),
            "unlabeled": len(tagged) - len(labeled),
            "corpus_ids": [p.corpus_id for p in tagged],
        })
    return rows
