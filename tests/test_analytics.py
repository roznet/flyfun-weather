"""Tests for the usage-analytics rollup + dashboard aggregation.

Covers two regressions fixed together:

* ``detailed_mode`` always showed 0 because the rollup matched the string
  ``"detailed"`` while the client emits the ``DisplayMode`` value ``"full"``.
* Briefing-shape buckets came back in arbitrary DB order; numeric and
  ordinal dimensions now sort into a human-sensible sequence.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from flyfun_common.db.models import Base
from weatherbrief.analytics import models as analytics_models  # noqa: F401
from weatherbrief.analytics.admin_api import _sort_buckets
from weatherbrief.analytics.events import Event
from weatherbrief.analytics.models import (
    AnalyticsBriefingFeatureDailyRow,
    AnalyticsEventRow,
)
from weatherbrief.analytics.rollup import rollup_day

_DAY = date(2026, 5, 20)
_DAY_START = datetime(2026, 5, 20, tzinfo=timezone.utc)


@pytest.fixture
def analytics_db(db_session):
    """db_session with the analytics tables guaranteed to exist."""
    Base.metadata.create_all(db_session.get_bind())
    return db_session


def _add_event(db, *, event: str, briefing_id: int | None, hour: int,
               props: dict | None = None) -> None:
    db.add(
        AnalyticsEventRow(
            ts=_DAY_START + timedelta(hours=hour),
            anon_id="anon-1",
            session_id="sess-1",
            briefing_id=briefing_id,
            event=event,
            props=json.dumps(props) if props is not None else None,
        )
    )


def _detailed_row(db) -> AnalyticsBriefingFeatureDailyRow | None:
    db.flush()
    return (
        db.query(AnalyticsBriefingFeatureDailyRow)
        .filter_by(day=_DAY, feature="detailed_mode")
        .one_or_none()
    )


class TestDetailedModeRollup:
    def test_final_full_counts_as_detailed(self, analytics_db):
        db = analytics_db
        _add_event(db, event=Event.BRIEFING_OPENED.value, briefing_id=1, hour=1)
        _add_event(db, event=Event.DISPLAY_MODE_CHANGED.value, briefing_id=1,
                   hour=2, props={"from": "full", "to": "compact"})
        _add_event(db, event=Event.DISPLAY_MODE_CHANGED.value, briefing_id=1,
                   hour=3, props={"from": "compact", "to": "full"})
        db.flush()

        rollup_day(db, _DAY)

        row = _detailed_row(db)
        assert row is not None
        # Final mode is full → briefing counts; one transition *to* full.
        assert row.briefings_with_feature == 1
        assert row.total_uses == 1
        assert row.briefings_total == 1

    def test_final_compact_does_not_count(self, analytics_db):
        db = analytics_db
        _add_event(db, event=Event.BRIEFING_OPENED.value, briefing_id=2, hour=1)
        _add_event(db, event=Event.DISPLAY_MODE_CHANGED.value, briefing_id=2,
                   hour=2, props={"from": "compact", "to": "full"})
        _add_event(db, event=Event.DISPLAY_MODE_CHANGED.value, briefing_id=2,
                   hour=3, props={"from": "full", "to": "compact"})
        db.flush()

        rollup_day(db, _DAY)

        row = _detailed_row(db)
        assert row is not None
        # Ended in compact → not detailed; but one transition *to* full happened.
        assert row.briefings_with_feature == 0
        assert row.total_uses == 1

    def test_legacy_detailed_string_is_ignored(self, analytics_db):
        # Regression guard: the pre-fix code matched "detailed", which the
        # client never emits. Such a value must not revive the old behaviour.
        db = analytics_db
        _add_event(db, event=Event.BRIEFING_OPENED.value, briefing_id=3, hour=1)
        _add_event(db, event=Event.DISPLAY_MODE_CHANGED.value, briefing_id=3,
                   hour=2, props={"from": "compact", "to": "detailed"})
        db.flush()

        rollup_day(db, _DAY)

        row = _detailed_row(db)
        assert row is not None
        assert row.briefings_with_feature == 0
        assert row.total_uses == 0


class TestSortBuckets:
    def test_numeric_dims_sort_ascending_unknown_last(self):
        buckets = [
            {"key": "10", "count": 1},
            {"key": "2", "count": 5},
            {"key": None, "count": 9},
            {"key": "1", "count": 3},
        ]
        keys = [b["key"] for b in _sort_buckets("by_model_count", buckets)]
        assert keys == ["1", "2", "10", None]

    def test_distance_follows_logical_order(self):
        buckets = [{"key": "long"}, {"key": "short"}, {"key": "medium"}]
        keys = [b["key"] for b in _sort_buckets("by_distance", buckets)]
        assert keys == ["short", "medium", "long"]

    def test_lead_time_follows_logical_order_unknown_last(self):
        buckets = [
            {"key": "7d_plus"},
            {"key": "same_day"},
            {"key": "no_etd"},
            {"key": "1d"},
            {"key": "post_departure"},
            {"key": None},
        ]
        keys = [b["key"] for b in _sort_buckets("by_lead_time", buckets)]
        assert keys == ["post_departure", "same_day", "1d", "7d_plus", "no_etd", None]

    def test_region_explicit_order(self):
        buckets = [{"key": "OTHER"}, {"key": None}, {"key": "US"}, {"key": "EU"}]
        keys = [b["key"] for b in _sort_buckets("by_region", buckets)]
        assert keys == ["EU", "US", "OTHER", None]

    def test_unknown_dim_passthrough(self):
        buckets = [{"key": "z"}, {"key": "a"}]
        assert _sort_buckets("by_something_else", buckets) == buckets
