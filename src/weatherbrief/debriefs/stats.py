"""Per-user debrief summary statistics.

Computes the panel shown between the Recent and Past sections of the
flights list. ``window_days`` is parameterised even though the UI ships
with a fixed 90-day default — this keeps adding 6m/1y/all selectors a
client-side change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from weatherbrief.debriefs.taxonomy import (
    OUTCOME_CATEGORIES,
    ConditionTag,
    Decision,
    OutcomeValue,
)
from weatherbrief.models import Flight, FlightDebrief

DEFAULT_WINDOW_DAYS = 90


class CategoryAccuracy(BaseModel):
    """Outcome counts for one category, restricted to flown flights that
    queried the category (i.e. the briefing raised an advisory)."""

    queried_count: int = 0
    consistent: int = 0
    better: int = 0
    worse: int = 0


class DebriefStats(BaseModel):
    """Summary stats for a user's debrief history within a time window.

    Window scoping: a flight is in-window when its ``departure_time`` falls
    within the last ``window_days``. Future flights are excluded.
    OPS-only cancellations are counted as cancellations but do not affect
    per-category accuracy (OPS is not in OUTCOME_CATEGORIES).
    Monitor-only debriefs are tracked separately and excluded from both
    the cancellation reasons and category accuracy aggregates — they are
    not real go/no-go decisions.
    """

    window_days: int
    total_flights_in_window: int = 0
    flown_count: int = 0
    cancelled_count: int = 0
    monitoring_count: int = 0
    pending_debrief_count: int = 0
    cancellation_reasons: dict[ConditionTag, int] = Field(default_factory=dict)
    category_accuracy: dict[ConditionTag, CategoryAccuracy] = Field(default_factory=dict)


def compute_stats(
    flights: list[Flight],
    debriefs: list[FlightDebrief],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> DebriefStats:
    """Aggregate ``flights`` + ``debriefs`` into a ``DebriefStats``.

    Pure function — no DB access, easy to test.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    # Past flights (departure < now) within the window.
    flights_in_window = [
        f for f in flights
        if f.departure_time < now and f.departure_time >= cutoff
    ]
    flight_id_set = {f.id for f in flights_in_window}

    # Index debriefs by flight_id, keep only those whose flight is in-window.
    debrief_by_flight = {
        d.flight_id: d
        for d in debriefs
        if d.flight_id in flight_id_set
    }

    flown = 0
    cancelled = 0
    monitoring = 0
    cancellation_reasons: dict[ConditionTag, int] = {}
    category_accuracy: dict[ConditionTag, CategoryAccuracy] = {
        cat: CategoryAccuracy() for cat in OUTCOME_CATEGORIES
    }

    for flight in flights_in_window:
        d = debrief_by_flight.get(flight.id)
        if d is None:
            continue
        if d.decision is Decision.CANCELLED:
            cancelled += 1
            for tag in d.reasons:
                cancellation_reasons[tag] = cancellation_reasons.get(tag, 0) + 1
        elif d.decision is Decision.FLOWN:
            flown += 1
            for tag, value in d.outcomes.items():
                if tag not in category_accuracy:
                    continue  # OPS or unknown — skip
                acc = category_accuracy[tag]
                acc.queried_count += 1
                if value is OutcomeValue.CONSISTENT:
                    acc.consistent += 1
                elif value is OutcomeValue.BETTER:
                    acc.better += 1
                elif value is OutcomeValue.WORSE:
                    acc.worse += 1
        elif d.decision is Decision.MONITORING:
            monitoring += 1
            # Intentionally no contribution to reasons or accuracy — monitor
            # flights aren't go/no-go decisions and shouldn't bias the data.

    pending = len(flights_in_window) - flown - cancelled - monitoring

    # Drop categories with zero queries to keep the panel compact.
    pruned_accuracy = {
        cat: acc for cat, acc in category_accuracy.items() if acc.queried_count > 0
    }

    return DebriefStats(
        window_days=window_days,
        total_flights_in_window=len(flights_in_window),
        flown_count=flown,
        cancelled_count=cancelled,
        monitoring_count=monitoring,
        pending_debrief_count=pending,
        cancellation_reasons=cancellation_reasons,
        category_accuracy=pruned_accuracy,
    )
