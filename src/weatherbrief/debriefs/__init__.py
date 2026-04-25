"""Flight debrief — pilot judgement captured against past flights.

Single-pilot Phase 1: cancel-or-flew decision, controlled tag taxonomy,
per-category outcome scoring (consistent / better / worse), free-text note.
"""

from weatherbrief.debriefs.taxonomy import (  # noqa: F401
    KEYWORD_MAP,
    OUTCOME_CATEGORIES,
    ConditionTag,
    Decision,
    OutcomeValue,
    match_tags_in_text,
)
