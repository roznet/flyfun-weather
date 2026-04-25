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
