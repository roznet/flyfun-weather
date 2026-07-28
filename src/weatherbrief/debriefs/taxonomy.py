"""Controlled vocabulary for debrief tags and outcome categories.

Single source of truth shared by the cancel-reason chips and the per-category
outcome form. The TS mirror in ``web/ts/components/debrief-taxonomy.ts``
must stay in sync.
"""

from __future__ import annotations

import re
from enum import Enum


class Decision(str, Enum):
    CANCELLED = "cancelled"
    FLOWN = "flown"
    MONITORING = "monitoring"  # flight created to watch weather, not intended to fly


class ConditionTag(str, Enum):
    """Weather/operational categories used by both cancel-reasons and outcomes.

    OPS is the only non-weather member — used as a cancel reason but excluded
    from outcome categories (no per-category accuracy to grade).
    """

    IMC = "IMC"
    ICE = "ICE"
    WIND = "WIND"
    TS = "TS"
    TURB = "TURB"
    FRZ = "FRZ"
    VIS = "VIS"
    OPS = "OPS"


class OutcomeValue(str, Enum):
    CONSISTENT = "consistent"
    BETTER = "better"
    WORSE = "worse"


OUTCOME_CATEGORIES: list[ConditionTag] = [t for t in ConditionTag if t != ConditionTag.OPS]


# --- Display metadata (single source of truth; served to iOS, mirrored in TS) ---
#
# The labels/descriptions below and ``ADVISORY_TAG_MAP`` historically lived only
# in ``web/ts/components/debrief-taxonomy.ts``. They now live here so Python is
# the *complete* source of truth and ``build_taxonomy_catalog()`` can serve them
# in the ``/api/help/catalog`` payload (the iOS client renders the debrief form
# straight from that catalog rather than hand-copying a Swift third copy). The TS
# mirror stays for the web build; keep all three in step.

# Decision display order (Flown first — the common case), with labels.
DECISION_ORDER: list[Decision] = [Decision.FLOWN, Decision.CANCELLED, Decision.MONITORING]

DECISION_LABELS: dict[Decision, str] = {
    Decision.FLOWN: "Flown",
    Decision.CANCELLED: "Cancelled",
    Decision.MONITORING: "Monitor only",
}

TAG_LABELS: dict[ConditionTag, str] = {
    ConditionTag.IMC: "IMC",
    ConditionTag.ICE: "Icing",
    ConditionTag.WIND: "Wind",
    ConditionTag.TS: "Thunderstorm",
    ConditionTag.TURB: "Turbulence",
    ConditionTag.FRZ: "Freezing precip",
    ConditionTag.VIS: "Visibility",
    ConditionTag.OPS: "Operational",
}

TAG_DESCRIPTIONS: dict[ConditionTag, str] = {
    ConditionTag.IMC: "Low ceilings / IFR conditions",
    ConditionTag.ICE: "Airframe icing",
    ConditionTag.WIND: "Strong / gusty / crosswind",
    ConditionTag.TS: "Thunderstorms or convective build-up",
    ConditionTag.TURB: "Turbulence (any intensity)",
    ConditionTag.FRZ: "Freezing rain / sleet",
    ConditionTag.VIS: "Reduced visibility, fog, mist",
    ConditionTag.OPS: "Non-weather (aircraft, pilot, NOTAM, fuel, …)",
}

OUTCOME_LABELS: dict[OutcomeValue, str] = {
    OutcomeValue.CONSISTENT: "As forecast",
    OutcomeValue.BETTER: "Better than forecast",
    OutcomeValue.WORSE: "Worse than forecast",
}

# Free-text note ceiling, shared by both the cancel and flown forms.
NOTE_MAX_LENGTH = 300

# Advisory id → debrief tag. Per-id (not per-category) because some categories
# cover more than one phenomenon. ``model`` advisories aren't weather → no entry.
# Mirrors ADVISORY_TAG_MAP in ``web/ts/components/debrief-taxonomy.ts``.
ADVISORY_TAG_MAP: dict[str, ConditionTag] = {
    "icing_escape": ConditionTag.ICE,
    "fiki_icing": ConditionTag.ICE,
    "vmc_cruise": ConditionTag.IMC,
    "cloud_top": ConditionTag.IMC,
    "vfr_feasibility": ConditionTag.IMC,
    "ifr_feasibility": ConditionTag.IMC,
    # Only bites at an IFR/LIFR destination (VFR is always green, MVFR caps at
    # amber on IAP presence), so the pilot-facing outcome to grade it against is
    # the IMC one, even though a tailwind can be what tips it.
    "approach_feasibility": ConditionTag.IMC,
    "flight_category": ConditionTag.IMC,
    "turbulence": ConditionTag.TURB,
    "mountain_wind": ConditionTag.TURB,
    "convective": ConditionTag.TS,
    "airport_wind": ConditionTag.WIND,
}


def build_taxonomy_catalog() -> dict:
    """JSON-able debrief taxonomy for the served ``/api/help/catalog`` payload.

    iOS renders the debrief form (decision buttons, cancel-reason chips, per-
    category outcome rows) purely from this, so a taxonomy edit is one Python
    change both the web (build-time TS mirror) and iOS (this catalog) pick up.
    Keys are snake_case to match the rest of the catalog's wire convention.
    """
    return {
        "decisions": [
            {"id": d.value, "label": DECISION_LABELS[d]} for d in DECISION_ORDER
        ],
        "tags": [
            {
                "id": t.value,
                "label": TAG_LABELS[t],
                "description": TAG_DESCRIPTIONS[t],
                "outcome_category": t in OUTCOME_CATEGORIES,
            }
            for t in ConditionTag
        ],
        "outcome_values": [
            {"id": v.value, "label": OUTCOME_LABELS[v]} for v in OutcomeValue
        ],
        "advisory_tag_map": {aid: tag.value for aid, tag in ADVISORY_TAG_MAP.items()},
        "note_max_length": NOTE_MAX_LENGTH,
    }


# Phrases scanned in the free-text note to auto-toggle matching chips.
# Lowercase, whole-word-ish matches (see _build_pattern). Conservative on purpose;
# the chip is the source of truth — pilots can untoggle if a match is unwanted.
KEYWORD_MAP: dict[ConditionTag, list[str]] = {
    ConditionTag.IMC:  ["imc", "ifr", "overcast", "low ceiling", "ceiling"],
    ConditionTag.ICE:  ["icing", "ice", "rime", "sld"],
    ConditionTag.WIND: ["wind", "gust", "crosswind"],
    ConditionTag.TS:   ["thunder", "thunderstorm", "cb", "storm", "lightning", "convect"],
    ConditionTag.TURB: ["turb", "bumpy", "rough", "shear"],
    ConditionTag.FRZ:  ["freezing rain", "fzra", "sleet", "freezing precip"],
    ConditionTag.VIS:  ["visib", "fog", "haze", "mist", "smoke"],
    ConditionTag.OPS:  ["fuel", "notam", "currency", "aircraft", "personal", "passenger"],
}


def _build_pattern(phrases: list[str]) -> re.Pattern[str]:
    """Build a single regex matching any phrase as a word-boundary fragment."""
    escaped = sorted((re.escape(p) for p in phrases), key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")", re.IGNORECASE)


_TAG_PATTERNS: dict[ConditionTag, re.Pattern[str]] = {
    tag: _build_pattern(phrases) for tag, phrases in KEYWORD_MAP.items()
}


def match_tags_in_text(text: str) -> set[ConditionTag]:
    """Scan ``text`` for keyword matches and return the matching tag set."""
    if not text:
        return set()
    return {tag for tag, pattern in _TAG_PATTERNS.items() if pattern.search(text)}
