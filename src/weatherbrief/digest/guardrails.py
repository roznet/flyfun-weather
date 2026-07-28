"""Deterministic, model-independent guardrail checks for digest output.

These assertions verify the promises the briefer prompt already makes
(``configs/weather_digest/prompts/briefer_v1.md``) without any judgment
calls. They are intentionally a *shared safety layer*: they take the raw
context string (the user message sent to the LLM) and the structured
output, and return a list of :class:`Violation` regardless of which prompt
or approach produced the output. So they apply across prompt/approach
variants and can gate CI on recorded eval output, be run against a live
subset, or wired in front of the user-facing output.

Checks implemented:

* **coordinate leak** — the prompt promises to convert raw lat/lon to plain
  geographic references; flag any ``58°N`` / ``8°W`` / ``51.8N`` left in.
* **fabricated sources** — if no ``=== TEXT FORECASTS`` section is in the
  context, the output must not cite ``DWD`` / ``NWS`` / ``AFD``.
* **number traceability** — pressure (hPa/mb) and altitude (ft/FL) figures in
  the output must trace back to a number in the context (fuzzy).
* **structure** — assessment in the enum, all six fields present,
  ``specific_concerns`` substantive or the allowed "none" word, and per-field
  sentence/length bounds.

Nothing here calls an LLM. ``run_guardrails`` is pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

# The five free-text fields. ``assessment`` is the enum, checked separately.
TEXT_FIELDS: tuple[str, ...] = (
    "assessment_reason",
    "synoptic",
    "specific_concerns",
    "trend",
    "watch_items",
)

VALID_ASSESSMENTS = frozenset({"GREEN", "AMBER", "RED"})

# Allowed "nothing to add" words for specific_concerns, across the four
# supported locales (en/fr/de/es ``none_word`` frontmatter). Compared
# case-insensitively, trailing punctuation stripped.
NONE_WORDS: frozenset[str] = frozenset(
    {"none", "aucun", "aucune", "keine", "kein", "ninguno", "ninguna"}
)

# Sources the prompt forbids citing unless a text-forecast section is present.
_SOURCE_RE = re.compile(r"\b(DWD|NWS|AFD)\b")

# Raw lat/lon leak. We deliberately tighten the issue's loose
# ``\d{1,2}(\.\d+)?\s*°?\s*[NSEW]`` to require either a degree symbol or a
# decimal so it does not false-positive on legitimate aviation phrasing like
# "25 NM", "30kt SW", or "Runway 27". Coordinates in this system always carry
# a degree symbol (DWD extract feeds "~50°N/8°E") or a decimal.
_COORD_RE = re.compile(
    r"\d{1,3}(?:\.\d+)?\s*°\s*[NSEW]\b"      # 58°N, 51.8 °N, 8°W
    r"|\d{1,3}\.\d+\s*[NSEW](?![A-Za-z])"    # 51.8N, 2.2W (decimal, no degree)
)

# Altitude/pressure figures in the output, including both ends of a range
# ("4500-7000ft", "3800-8200ft"). Groups capture every number that carries an
# aviation unit.
_FIGURE_RANGE_RE = re.compile(
    r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s*(ft|hpa|mb)\b", re.IGNORECASE
)
_FIGURE_SINGLE_RE = re.compile(
    r"(\d[\d,]*)\s*(ft|hpa|mb)\b", re.IGNORECASE
)
_FIGURE_FL_RE = re.compile(r"\bFL\s*(\d{2,3})\b", re.IGNORECASE)

# Any run of digits, for building the set of numbers present in the context.
_NUMBER_RE = re.compile(r"\d[\d,]*")

# METAR cloud/vertical-visibility groups are *coded* altitudes: BKN010 means
# broken at 1,000 ft. Two ways the output can mangle that into something a
# pilot misreads as tens of feet:
#   "BKN010ft"     — a unit welded onto the coded form
#   "BKN010–025ft" — two groups spliced into a range (usually two stations)
# Number traceability cannot catch either: 10 and 25 both appear in the raw
# METARs in context, so the figures trace fine and only the *form* is wrong.
# "BKN010 to BKN025" and plain "1,000 ft" are both correct and must pass.
_CLOUD_GROUP = r"(?:FEW|SCT|BKN|OVC|VV)\d{3}"
# Same groups, capturing the coded hundreds-of-feet so number traceability can
# credit the decoded altitude (BKN025 in context ⇒ 2,500 ft in the output).
_CLOUD_GROUP_DECODE_RE = re.compile(r"\b(?:FEW|SCT|BKN|OVC|VV)(\d{3})\b", re.IGNORECASE)
_CLOUD_GROUP_UNIT_RE = re.compile(rf"\b{_CLOUD_GROUP}\s*(?:ft|feet)\b", re.IGNORECASE)
_CLOUD_GROUP_SPLICE_RE = re.compile(
    # (?!\d) not \b — in "BKN010-025ft" there is no word boundary between the
    # final digit and the unit, so \b would silently never match.
    rf"\b{_CLOUD_GROUP}\s*[-–—]\s*(?!(?:FEW|SCT|BKN|OVC|VV))\d{{3}}(?!\d)\s*(?:ft|feet)?",
    re.IGNORECASE,
)

# Regulatory/legal claims. The briefing describes weather; whether a flight is
# permitted depends on the pilot's ratings and currency, the aircraft's
# equipage, approach availability and the applicable rule set — none of which
# are in the context. Deliberately narrow: "must" and a bare "required" have
# too many legitimate uses ("ceilings must be monitored") and a noisy check
# gets ignored. Validated at 0 false positives over 335 recorded digests; the
# 2 matches were both genuine ("IFR is technically legal but...", "conditions
# are legal and manageable"). Non-English stems included because the digest is
# localised (en/fr/de/es).
_REGULATORY_CLAIM_RE = re.compile(
    r"\b(?:il)?legal(?:ly|es|mente)?\b"
    r"|\b(?:un)?lawful(?:ly)?\b"
    r"|\bl[ée]gal(?:e|ement)?\b|\bill[ée]gal(?:e)?\b"
    r"|\bgesetzlich\b|\brechtlich\b|\bzul[aä]ssig\b"
    r"|\brequired by (?:law|regulation)"
    r"|\bthe only (?:legal|lawful) \w+"
    r"|\bthe only option\b",
    re.IGNORECASE,
)

# Convective character — the route-advisory line prompt_builder emits, e.g.
#   "[AMBER] Convective Character: Isolated cells — circumnavigable VFR ..."
# The status encodes avoidability: AMBER ⇒ isolated/scattered (circumnavigable);
# RED ⇒ widespread/organized/embedded (genuinely VFR-impractical). The advisory
# name is always English in context (catalog name), so this match is locale-
# stable. We only police the AMBER case (issue #294).
_CONV_CHARACTER_RE = re.compile(
    r"\[(GREEN|AMBER|RED)\]\s+Convective Character\b", re.IGNORECASE
)
# Best-effort, multi-locale. A *convective* term AND an *absolute VFR-impractical*
# term in the SAME sentence is the contradiction we flag — narrow on purpose so
# it does not fire when VFR is impractical for an unrelated cause (cloud/airport).
_CONV_TERM_RE = re.compile(
    r"convect|thunderstorm|storm|\bcell|\bcb\b|gewitter|konvekt|zelle|"
    r"orage|cellule|tormenta|c[ée]lula",
    re.IGNORECASE,
)
_VFR_IMPRACTICAL_RE = re.compile(
    r"vfr\s+(?:is\s+)?(?:impractical|impossible|not\s+(?:viable|possible|feasible))"
    r"|not\s+(?:viable|possible|feasible)\s+(?:under|in|for)\s+vfr"
    r"|vfr\s+nicht\s+(?:m[oö]glich|praktikabel|machbar|tauglich)"
    r"|kein(?:e)?\s+(?:vfr|sichtflug)|sichtflug\s+nicht\s+m[oö]glich"
    r"|vfr\s+(?:impraticable|impracticable|imposible|no\s+viable)"
    r"|nicht\s+akzeptabel|inakzeptabel|unacceptable|not\s+acceptable"
    r"|inacceptable|inaceptable|no-go",
    re.IGNORECASE,
)

# Per-field bounds. ``max_sentences`` / ``max_chars`` catch runaway output;
# ``min_chars`` catches empty/stub fields. Bounds are generous on purpose so a
# different model or prompt variant that still respects the contract passes —
# they exist to catch egregious violations, not to police style.
#
# These are RUNAWAY BACKSTOPS, not style policing. Length/sentence bounds are
# inherently fragile — every prompt or model change shifts the output length
# distribution, and a too-tight cap then false-positives on legitimate output
# (we hit this twice: first tuned to 3 fixtures, then to a Feb/Mar sample that
# the more verbose June packs exceeded). So caps sit ~1.5x above the observed
# max across the real prod distribution and only catch egregious blow-ups (a
# model dumping 2x the longest real output). Conciseness/style is the LLM
# judge's job (#255), not this deterministic layer. Observed maxima (non-
# longrange): assessment_reason 525ch/2s, synoptic 1485ch/7s, specific_concerns
# 2563ch/24s, trend 1368ch/10s, watch_items 1496ch/19s. Caps sit ~1.5x above
# those, except assessment_reason (700/525 ≈ 1.33x) where the round number is a
# touch tighter but still a generous backstop for a one-sentence field.
@dataclass(frozen=True)
class _FieldBound:
    min_chars: int
    max_chars: int
    max_sentences: int


_FIELD_BOUNDS: dict[str, _FieldBound] = {
    "assessment_reason": _FieldBound(min_chars=10, max_chars=700, max_sentences=3),
    "synoptic": _FieldBound(min_chars=10, max_chars=2200, max_sentences=11),
    "specific_concerns": _FieldBound(min_chars=1, max_chars=3800, max_sentences=38),
    "trend": _FieldBound(min_chars=1, max_chars=2000, max_sentences=16),
    "watch_items": _FieldBound(min_chars=1, max_chars=2200, max_sentences=30),
}

# Fuzzy traceability tolerance: an output figure is "traceable" if some context
# number is within this relative fraction (or 1 unit, whichever is larger),
# tolerating a model rounding 3810 -> 3800.
_FUZZY_REL_TOL = 0.05

TEXT_FORECASTS_MARKER = "=== TEXT FORECASTS"


@dataclass(frozen=True)
class Violation:
    """A single guardrail failure."""

    check: str
    field: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        loc = f" [{self.field}]" if self.field else ""
        return f"{self.check}{loc}: {self.message}"


def _as_dict(digest: Mapping[str, object] | object) -> dict[str, object]:
    """Normalise a WeatherDigest (Pydantic) or mapping to a plain dict."""
    if isinstance(digest, Mapping):
        return dict(digest)
    dump = getattr(digest, "model_dump", None)
    if callable(dump):
        return dump()
    # Fall back to attribute access for the known fields.
    out: dict[str, object] = {}
    for key in ("assessment", *TEXT_FIELDS):
        if hasattr(digest, key):
            out[key] = getattr(digest, key)
    return out


def _count_sentences(text: str) -> int:
    # Split only on sentence-ending punctuation followed by whitespace (or
    # end-of-string), so a decimal mid-sentence ("QNH 1013.5hPa") is not
    # mistaken for a sentence boundary.
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return len(parts)


def _numbers_in(text: str) -> set[int]:
    out: set[int] = set()
    for m in _NUMBER_RE.finditer(text):
        try:
            out.add(int(m.group(0).replace(",", "")))
        except ValueError:
            continue
    # A METAR cloud group in the context IS its altitude in feet: BKN025 states
    # 2,500 ft just as surely as the digits "2500" would. The prompt asks for
    # exactly that conversion, so credit both forms or the converted value
    # reads as an invented number.
    for m in _CLOUD_GROUP_DECODE_RE.finditer(text):
        out.add(int(m.group(1)) * 100)
    return out


def _is_traceable(value: int, context_numbers: set[int]) -> bool:
    if value in context_numbers:
        return True
    tol = max(1.0, value * _FUZZY_REL_TOL)
    return any(abs(value - n) <= tol for n in context_numbers)


def _output_figures(text: str) -> list[tuple[int, ...]]:
    """Pressure/altitude figures in an output field, as OR-groups.

    Each group is a tuple of acceptable representations; a figure is traceable
    if *any* member traces to the context. Most figures are single-element
    groups, but a flight level yields ``(level, level*100)`` so it traces
    whether the context expresses it as the FL number or the ft-equivalent.
    """
    groups: list[tuple[int, ...]] = []

    def _to_int(token: str) -> int | None:
        try:
            return int(token.replace(",", ""))
        except ValueError:
            return None

    for m in _FIGURE_RANGE_RE.finditer(text):
        for token in (m.group(1), m.group(2)):
            v = _to_int(token)
            if v is not None:
                groups.append((v,))
    # Single figures. _FIGURE_SINGLE_RE also re-matches the unit-bearing end of
    # a range ("9000ft" in "1500-9000ft"), so dedupe at the end to avoid
    # emitting two identical violations for the same value.
    for m in _FIGURE_SINGLE_RE.finditer(text):
        v = _to_int(m.group(1))
        if v is not None:
            groups.append((v,))
    for m in _FIGURE_FL_RE.finditer(text):
        v = _to_int(m.group(1))
        if v is not None:
            # FL080 traces if the context has either the flight level (80) or
            # its altitude (8000ft) — checked as an OR pair, not two constraints.
            groups.append((v, v * 100))
    # Dedup. ``dict.fromkeys`` collapses identical tuples; additionally drop any
    # single-element group whose value already appears in a multi-element (FL)
    # group, so "FL090" + "9000ft" in one field reports one figure, not two.
    covered = {m for g in groups if len(g) > 1 for m in g}
    return [
        g for g in dict.fromkeys(groups) if len(g) > 1 or g[0] not in covered
    ]


# --- Individual checks -------------------------------------------------------


def check_coordinate_leak(digest: Mapping[str, object] | object) -> list[Violation]:
    out = _as_dict(digest)
    violations: list[Violation] = []
    for field in TEXT_FIELDS:
        value = str(out.get(field, "") or "")
        for m in _COORD_RE.finditer(value):
            violations.append(
                Violation(
                    "coordinate_leak",
                    field,
                    f"raw coordinate {m.group(0)!r} (convert to a plain geographic reference)",
                )
            )
    return violations


def check_fabricated_sources(
    digest: Mapping[str, object] | object, context: str
) -> list[Violation]:
    if TEXT_FORECASTS_MARKER in context:
        return []
    out = _as_dict(digest)
    violations: list[Violation] = []
    for field in TEXT_FIELDS:
        value = str(out.get(field, "") or "")
        for m in _SOURCE_RE.finditer(value):
            violations.append(
                Violation(
                    "fabricated_source",
                    field,
                    f"cites {m.group(0)!r} but no text-forecast section was provided",
                )
            )
    return violations


def check_number_traceability(
    digest: Mapping[str, object] | object, context: str
) -> list[Violation]:
    out = _as_dict(digest)
    context_numbers = _numbers_in(context)
    violations: list[Violation] = []
    for field in TEXT_FIELDS:
        value = str(out.get(field, "") or "")
        for group in _output_figures(value):
            if not any(_is_traceable(fig, context_numbers) for fig in group):
                shown = " / ".join(str(g) for g in group)
                violations.append(
                    Violation(
                        "number_traceability",
                        field,
                        f"figure {shown} not found in context (possible invented number)",
                    )
                )
    return violations


def check_regulatory_claim(digest: Mapping[str, object] | object) -> list[Violation]:
    """Flag legal/regulatory verdicts — the briefing's lane is weather only.

    Reported on the 2026-07-26 EGKB→EGHN 07:01 briefing, which said "making VFR
    departure impossible and IFR departure the only legal option". That asserts
    a rule-set conclusion the context cannot support, and it was substantively
    wrong: delaying (which the same digest recommended) and not flying were
    both available. The fix is to drop the verdict, not to soften the
    meteorology — see the "meteorological lane" rule in ``briefer_v2.md``.
    """
    out = _as_dict(digest)
    violations: list[Violation] = []
    for field in TEXT_FIELDS:
        value = str(out.get(field, "") or "")
        for m in _REGULATORY_CLAIM_RE.finditer(value):
            violations.append(
                Violation(
                    "regulatory_claim",
                    field,
                    f"{m.group(0)!r} states a regulatory conclusion "
                    "(describe the conditions; the pilot decides what is permitted)",
                )
            )
    return violations


def check_cloud_group_format(digest: Mapping[str, object] | object) -> list[Violation]:
    """Flag METAR cloud groups written as though the code were a plain number.

    ``BKN010`` already carries its altitude (1,000 ft). Welding a unit on
    ("BKN010ft") or splicing two groups into a range ("BKN010–025ft") reads as
    tens of feet — the confusion a pilot reported on the 2026-07-26 EGKB→EGHN
    briefing, where the assessment said "MVFR cloud bases (BKN010–025ft)" for
    two different airfields. Correct forms — the bare group, "BKN010 to
    BKN025", and plain "1,000 ft" — all pass.
    """
    out = _as_dict(digest)
    violations: list[Violation] = []
    for field in TEXT_FIELDS:
        value = str(out.get(field, "") or "")
        for m in _CLOUD_GROUP_SPLICE_RE.finditer(value):
            violations.append(
                Violation(
                    "cloud_group_format",
                    field,
                    f"{m.group(0)!r} splices two cloud groups into a range "
                    "(name the lowest layer, or give the span in plain feet)",
                )
            )
        for m in _CLOUD_GROUP_UNIT_RE.finditer(value):
            violations.append(
                Violation(
                    "cloud_group_format",
                    field,
                    f"{m.group(0)!r} attaches a unit to a coded cloud group "
                    "(BKN010 already means 1,000 ft)",
                )
            )
    return violations


def check_convective_vfr_consistency(
    digest: Mapping[str, object] | object, context: str
) -> list[Violation]:
    """Flag convection-attributed VFR-impracticality when it's circumnavigable.

    When the deterministic Convective Character advisory is AMBER (isolated /
    scattered = circumnavigable VFR), the output must not say *convection* makes
    VFR impractical/impossible — the cells are avoidable (issue #294). Kept
    deliberately narrow: only fires on a same-sentence convective-term +
    absolute-impractical-term co-occurrence, AMBER only, so it does not trip when
    VFR is impractical for an unrelated reason (cloud, airport). The briefer
    prompt is the primary control; this is a best-effort backstop.
    """
    m = _CONV_CHARACTER_RE.search(context)
    if m is None or m.group(1).upper() != "AMBER":
        return []
    out = _as_dict(digest)
    violations: list[Violation] = []
    for field in TEXT_FIELDS:
        value = str(out.get(field, "") or "")
        for sentence in re.split(r"(?<=[.!?])\s+", value):
            if _CONV_TERM_RE.search(sentence) and _VFR_IMPRACTICAL_RE.search(sentence):
                violations.append(
                    Violation(
                        "convective_vfr_consistency",
                        field,
                        "convection framed as making VFR impractical, but the "
                        "Convective Character advisory is AMBER (isolated/scattered "
                        "= circumnavigable) — attribute VFR-impracticality to its "
                        "actual cause or soften",
                    )
                )
                break  # one per field is enough
    return violations


def check_structure(
    digest: Mapping[str, object] | object,
    *,
    none_words: Iterable[str] = NONE_WORDS,
) -> list[Violation]:
    out = _as_dict(digest)
    violations: list[Violation] = []

    assessment = out.get("assessment")
    if assessment not in VALID_ASSESSMENTS:
        violations.append(
            Violation(
                "structure",
                "assessment",
                f"{assessment!r} not in {sorted(VALID_ASSESSMENTS)}",
            )
        )

    none_set = {w.lower() for w in none_words}

    for field in TEXT_FIELDS:
        raw = out.get(field)
        if raw is None:
            violations.append(Violation("structure", field, "missing field"))
            continue
        value = str(raw).strip()
        if not value:
            violations.append(Violation("structure", field, "empty field"))
            continue

        bound = _FIELD_BOUNDS[field]

        # specific_concerns may legitimately be just the locale "none" word.
        is_none_word = value.rstrip(".").strip().lower() in none_set
        if field == "specific_concerns" and is_none_word:
            continue

        if len(value) < bound.min_chars:
            violations.append(
                Violation(
                    "structure",
                    field,
                    f"too short ({len(value)} < {bound.min_chars} chars)",
                )
            )
        if len(value) > bound.max_chars:
            violations.append(
                Violation(
                    "structure",
                    field,
                    f"too long ({len(value)} > {bound.max_chars} chars)",
                )
            )
        sentences = _count_sentences(value)
        if sentences > bound.max_sentences:
            violations.append(
                Violation(
                    "structure",
                    field,
                    f"too many sentences ({sentences} > {bound.max_sentences})",
                )
            )

    return violations


def run_guardrails(
    digest: Mapping[str, object] | object,
    context: str,
    *,
    none_words: Iterable[str] = NONE_WORDS,
) -> list[Violation]:
    """Run every deterministic guardrail and return all violations.

    ``digest`` may be a :class:`~weatherbrief.digest.llm_digest.WeatherDigest`,
    a mapping, or any object exposing the six fields. ``context`` is the user
    message that was sent to the LLM (the fixture ``context.txt``). An empty
    list means the output honoured every promise.
    """
    violations: list[Violation] = []
    violations.extend(check_structure(digest, none_words=none_words))
    violations.extend(check_coordinate_leak(digest))
    violations.extend(check_fabricated_sources(digest, context))
    violations.extend(check_number_traceability(digest, context))
    violations.extend(check_cloud_group_format(digest))
    violations.extend(check_regulatory_claim(digest))
    violations.extend(check_convective_vfr_consistency(digest, context))
    return violations
