"""Smoke tests for the airport profile SSE helpers.

The SSE endpoint itself depends on Open-Meteo and the airports DB so it's
not easy to exercise in unit tests; what we can pin down here is the
purely-functional helpers (hour-window construction, time-key matching).
"""
from __future__ import annotations

from datetime import datetime, timezone

from weatherbrief.api.airport_profile import _build_hours, _DEFAULT_WINDOW_H


def test_build_hours_default_window():
    start = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    hours = _build_hours(start, _DEFAULT_WINDOW_H)
    assert len(hours) == _DEFAULT_WINDOW_H + 1
    assert hours[0] == start
    assert hours[-1].hour == start.hour + _DEFAULT_WINDOW_H
    # All hours are aware UTC.
    assert all(h.tzinfo is not None for h in hours)


def test_build_hours_zero_window_is_single_point():
    start = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    hours = _build_hours(start, 0)
    assert hours == [start]


def test_build_hours_crosses_midnight():
    start = datetime(2026, 5, 7, 22, 0, tzinfo=timezone.utc)
    hours = _build_hours(start, 3)  # 22, 23, 00, 01
    assert [h.hour for h in hours] == [22, 23, 0, 1]
    assert hours[0].day == 7
    assert hours[-1].day == 8
