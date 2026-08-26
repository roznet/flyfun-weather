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
import shutil
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from weatherbrief.eval_workbench.config import (
    AREAS,
    area_root,
    eval_corpus_dir,
    eval_flight_id,
)
from weatherbrief.eval_workbench.situations import SITUATION_VOCAB

CORPUS_META_FILE = "corpus_meta.json"
LABEL_FILE = "label.json"

# The guidance presets a golden label carries an assessment for.
GUIDANCES: tuple[str, ...] = ("conservative", "balanced", "tolerant")

# The only valid golden assessment values. A typo'd value (e.g. "AMBR") would
# never match LLM output in run_digest_eval, silently looking like a regression
# — so reject it at write time rather than store it.
ASSESSMENT_VALUES: frozenset[str] = frozenset({"GREEN", "AMBER", "RED"})


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
    # Pilot post-flight debrief (ground truth). Set by scripts/pull_debrief_data.py
    # from the prod flight_debriefs table; the full record is in debrief.json.
    debriefed: bool = False
    debrief_decision: str | None = None  # flown | cancelled | monitoring
    # True when the debrief carries graded detail (cancel reasons or per-category
    # consistent/better/worse outcomes) — the richest ground truth, worth labelling first.
    debrief_graded: bool = False


class CorpusLabel(BaseModel):
    """Golden labels assigned by the SME (committed)."""

    assessments: dict[str, str] = Field(default_factory=dict)  # guidance -> G/A/R
    rationale: str = ""
    notes: str = ""
    # SME curation priority, set during triage independently of the G/A/R label:
    # 1 = very interesting, revalidate first; 2 = good; 3 = normal/simple;
    # 4 = skip / not interesting. None = untriaged. Used to order the labelling
    # queue, subset regression runs, and decide what to promote into the corpus.
    priority: int | None = None
    labeled_by: str = ""
    labeled_at: str = ""

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value not in (1, 2, 3, 4):
            raise ValueError("priority must be 1-4 or null")
        return value

    @field_validator("assessments")
    @classmethod
    def _normalize_assessments(cls, value: dict[str, str]) -> dict[str, str]:
        """Upper-case, drop empties, and reject non-GREEN/AMBER/RED values."""
        out: dict[str, str] = {}
        for guidance, assessment in value.items():
            if not assessment:
                continue
            normalized = assessment.upper()
            if normalized not in ASSESSMENT_VALUES:
                raise ValueError(
                    f"invalid assessment {assessment!r} for {guidance!r}; "
                    "must be GREEN/AMBER/RED"
                )
            out[guidance] = normalized
        return out

    @property
    def is_complete(self) -> bool:
        """True when every guidance preset has an assessment."""
        return all(self.assessments.get(g) for g in GUIDANCES)


class CorpusPack(BaseModel):
    """A corpus entry: its descriptor + current label state."""

    corpus_id: str
    meta: CorpusMeta
    label: CorpusLabel | None = None
    area: str = "corpus"  # which area it currently lives in: "staging" | "corpus"

    @property
    def flight_id(self) -> str:
        return eval_flight_id(self.corpus_id)

    @property
    def is_labeled(self) -> bool:
        return self.label is not None and bool(self.label.assessments)


# --- path helpers -----------------------------------------------------------

def corpus_root(area: str = "corpus") -> Path:
    return area_root(area)


def pack_path(corpus_id: str, area: str = "corpus") -> Path:
    return area_root(area) / corpus_id


def _meta_path(corpus_id: str, area: str = "corpus") -> Path:
    return pack_path(corpus_id, area) / CORPUS_META_FILE


def _label_path(corpus_id: str, area: str = "corpus") -> Path:
    return pack_path(corpus_id, area) / LABEL_FILE


# --- read -------------------------------------------------------------------

def corpus_exists(corpus_id: str, area: str = "corpus") -> bool:
    return _meta_path(corpus_id, area).exists()


def load_corpus_meta(corpus_id: str, area: str = "corpus") -> CorpusMeta:
    """Load a corpus pack descriptor. Raises FileNotFoundError if missing."""
    path = _meta_path(corpus_id, area)
    if not path.exists():
        raise FileNotFoundError(f"No corpus pack: {corpus_id}")
    return CorpusMeta.model_validate_json(path.read_text(encoding="utf-8"))


def load_label(corpus_id: str, area: str = "corpus") -> CorpusLabel | None:
    """Load the golden label, or None if the pack is unlabelled."""
    path = _label_path(corpus_id, area)
    if not path.exists():
        return None
    return CorpusLabel.model_validate_json(path.read_text(encoding="utf-8"))


def load_pack(corpus_id: str, area: str = "corpus") -> CorpusPack:
    return CorpusPack(
        corpus_id=corpus_id,
        meta=load_corpus_meta(corpus_id, area),
        label=load_label(corpus_id, area),
        area=area,
    )


def list_corpus(area: str = "corpus") -> list[CorpusPack]:
    """All packs in an area, sorted by corpus_id. Skips dirs without a descriptor."""
    root = corpus_root(area)
    if not root.exists():
        return []
    out: list[CorpusPack] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / CORPUS_META_FILE).exists():
            out.append(load_pack(child.name, area))
    return out


def find_pack(corpus_id: str) -> CorpusPack | None:
    """Locate a pack by id across areas (staging first). A pack lives in one
    area at a time, so the first hit is authoritative. Used by the resolver,
    the label endpoint, and promotion to find a pack without knowing its area.
    """
    for area in AREAS:
        if corpus_exists(corpus_id, area):
            return load_pack(corpus_id, area)
    return None


# --- write ------------------------------------------------------------------

def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def save_corpus_meta(meta: CorpusMeta, area: str = "corpus") -> None:
    _write_json(_meta_path(meta.corpus_id, area), meta.model_dump())


def save_label(corpus_id: str, label: CorpusLabel, area: str = "corpus") -> Path:
    """Persist the golden label for a corpus pack. Returns the file path."""
    if not corpus_exists(corpus_id, area):
        raise FileNotFoundError(f"No corpus pack: {corpus_id}")
    path = _label_path(corpus_id, area)
    _write_json(path, label.model_dump())
    return path


def promote(corpus_id: str) -> CorpusPack:
    """Move a labelled pack from staging into the curated corpus.

    Promotion is a directory *move* (the heavy artifacts + ``label.json`` ride
    along, no copy). Gated on the pack carrying a golden label so only curated
    packs land in the committed corpus. Raises:

    * ``FileNotFoundError`` — no such pack in staging;
    * ``ValueError`` — the pack has no golden label yet;
    * ``FileExistsError`` — a pack with that id already exists in the corpus.
    """
    if not corpus_exists(corpus_id, "staging"):
        raise FileNotFoundError(f"No staging pack: {corpus_id}")
    pack = load_pack(corpus_id, "staging")
    if not pack.is_labeled:
        raise ValueError("pack must have a golden label before promotion")
    if corpus_exists(corpus_id, "corpus"):
        raise FileExistsError(f"already in corpus: {corpus_id}")
    src = pack_path(corpus_id, "staging")
    # Compact before promoting so the committed corpus carries the gzipped
    # master (cross_section.json.gz), never the gitignored plain .json.
    plain_cs = src / "cross_section.json"
    if plain_cs.exists():
        import gzip

        (src / "cross_section.json.gz").write_bytes(gzip.compress(plain_cs.read_bytes()))
        plain_cs.unlink()
    dest = pack_path(corpus_id, "corpus")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return load_pack(corpus_id, "corpus")


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


# --- altitude coverage ------------------------------------------------------
#
# A replay's "N packs, no change" is only as broad as the corpus's cruise
# altitudes, and this corpus is a low-level corpus: 71% of the staging set flies
# below 10,000 ft. Any turbulence/CAT/icing law whose behaviour depends on
# altitude is therefore measured mostly on its low half, and a change that badly
# over-tightened aloft could pass a 201-pack replay clean (#578). These helpers
# make a replay state its own altitude coverage instead of leaving it folklore.

_ALTITUDE_BAND_FT = 2000
# Below this the #539-class laws cannot tighten at all (the old ramp was x1.0
# there), so the comparison is one-way by construction.
LOW_LEVEL_CEILING_FT = 10000
# The band where an altitude-scaled change bites hardest.
ALOFT_FLOOR_FT = 16000


def altitude_profile(
    packs: list[CorpusPack] | None = None, area: str = "corpus"
) -> dict:
    """Cruise-altitude distribution of a pack set, plus what it can't measure.

    ``bands`` is ``[(lo_ft, hi_ft, count), ...]`` over the occupied 2000-ft
    bands. ``aloft_with_flagged_turbulence`` counts the packs at/above
    :data:`ALOFT_FLOOR_FT` whose *saved* baseline actually flags turbulence —
    the only ones on which a de-escalating change has anything to de-escalate.
    """
    if packs is None:
        packs = list_corpus(area)
    altitudes = [p.meta.cruise_altitude_ft for p in packs]
    bands: list[tuple[int, int, int]] = []
    if altitudes:
        top = max(altitudes) // _ALTITUDE_BAND_FT * _ALTITUDE_BAND_FT
        for lo in range(0, top + _ALTITUDE_BAND_FT, _ALTITUDE_BAND_FT):
            hi = lo + _ALTITUDE_BAND_FT
            count = sum(1 for a in altitudes if lo <= a < hi)
            if count:
                bands.append((lo, hi, count))
    aloft = [p for p in packs if p.meta.cruise_altitude_ft >= ALOFT_FLOOR_FT]
    return {
        "total": len(packs),
        "bands": bands,
        "below_low_level_ceiling": sum(
            1 for a in altitudes if a < LOW_LEVEL_CEILING_FT
        ),
        "aloft": len(aloft),
        "aloft_with_flagged_turbulence": sum(
            1 for p in aloft if _baseline_flags(p, area, "turbulence")
        ),
    }


def _baseline_flags(pack: CorpusPack, area: str, advisory_id: str) -> bool:
    """True when the pack's saved advisories flag *advisory_id* on any model.

    Reads the stored baseline rather than re-grading: this is a question about
    what the corpus *contains*, not about the code under test.
    """
    path = pack_path(pack.corpus_id, area) / "route_advisories.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for advisory in data.get("advisories", []):
        if advisory.get("advisory_id") != advisory_id:
            continue
        statuses = {advisory.get("aggregate_status")} | {
            m.get("status") for m in advisory.get("per_model", [])
        }
        return bool(statuses & {"red", "amber"})
    return False


def format_altitude_profile(
    packs: list[CorpusPack] | None = None, area: str = "corpus"
) -> str:
    """The profile as a printable block, with the caveat it exists to carry."""
    prof = altitude_profile(packs, area)
    if not prof["total"]:
        return "Cruise-altitude profile: no packs."
    lines = [f"Cruise-altitude profile of the {prof['total']} pack(s) replayed:"]
    widest = max(count for _, _, count in prof["bands"])
    for lo, hi, count in prof["bands"]:
        bar = "#" * max(1, round(20 * count / widest))
        lines.append(f"  {lo:>6}-{hi:>6} ft : {count:>4}  {bar}")
    pct_low = round(100 * prof["below_low_level_ceiling"] / prof["total"])
    lines += [
        f"  {prof['below_low_level_ceiling']} of {prof['total']} ({pct_low}%) below "
        f"{LOW_LEVEL_CEILING_FT} ft; {prof['aloft']} at/above {ALOFT_FLOOR_FT} ft, "
        f"of which {prof['aloft_with_flagged_turbulence']} carry a flagged "
        "turbulence baseline.",
        "  A clean replay therefore says little about altitude-scaled changes "
        "aloft — see designs/eval-digest-workbench.md, \"What a clean replay "
        "does not cover\".",
    ]
    return "\n".join(lines)
