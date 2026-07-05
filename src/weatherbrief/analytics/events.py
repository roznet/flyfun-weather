"""Registry of allowed analytics events.

Adding a new event = adding a constant here AND wiring it on the client.
The ingest endpoint rejects any event name not in :data:`ALLOWED_EVENTS`,
which prevents typos and silent naming drift over time.

Conventions
-----------
* Lowercase, dot-separated namespaces (``namespace.action``).
* Past tense for things that happened (``compare.airport_added``).
* Keep ``props`` low-cardinality (enums, small ints, booleans). Avoid free
  text, IDs, timestamps, or anything you can't ``GROUP BY`` cleanly.

The ``FEATURE_OF`` mapping is what the per-briefing rollup uses to derive
feature attachment rates (e.g. "what % of briefings used the map?").
Any event with a feature label counts as "feature used" if it occurs
within a briefing context (``briefing_id`` is set).
"""

from __future__ import annotations

from enum import StrEnum


class Event(StrEnum):
    """Canonical event names. Mirror this in ``web/ts/analytics/events.ts``."""

    # Session lifecycle ------------------------------------------------------
    SESSION_STARTED = "app.session_started"

    # Flight lifecycle -------------------------------------------------------
    FLIGHT_CREATED = "flight.created"

    # Briefing lifecycle -----------------------------------------------------
    BRIEFING_OPENED = "briefing.opened"
    BRIEFING_REFRESH_REQUESTED = "briefing.refresh_requested"
    BRIEFING_REFRESHED = "briefing.refreshed"

    # Cross-section snapshot -------------------------------------------------
    # One snapshot per cross-section view carrying the current display config
    # (theme/preset/layout/layers/…). Rolled up into per-dimension
    # distributions, not the briefing-feature attachment rollup.
    XSECTION_VIEWED = "xsection.viewed"

    # Visualization features -------------------------------------------------
    FORECAST_MAP_OPENED = "forecast_map.opened"
    FORECAST_MAP_LAYER_CHANGED = "forecast_map.layer_changed"
    SKEWT_OPENED = "skewt.opened"
    COMPARE_OPENED = "compare.opened"
    COMPARE_AIRPORT_ADDED = "compare.airport_added"
    ALTITUDE_TABLE_OPENED = "altitude_table.opened"
    # Flexibility / timing-scenarios feature. Emitted once per briefing when
    # scan results actually render (props: {mode}).
    TIMING_SCENARIOS_USED = "timing_scenarios.used"

    # Onboarding -------------------------------------------------------------
    TOUR_STARTED = "tour.started"
    TOUR_COMPLETED = "tour.completed"

    # Standalone (maps page — no briefing context) ---------------------------
    CLIMATOLOGY_OPENED = "climatology.opened"

    # Help page (no briefing context) ----------------------------------------
    HELP_WHATS_NEW_OPENED = "help.whats_new_opened"  # props: {source}

    # User preferences -------------------------------------------------------
    AUTO_REFRESH_ENABLED = "auto_refresh.enabled"
    AUTO_REFRESH_DISABLED = "auto_refresh.disabled"
    DISPLAY_MODE_CHANGED = "display_mode.changed"  # props: {from, to}


ALLOWED_EVENTS: frozenset[str] = frozenset(e.value for e in Event)


# Maps event name → feature label used for per-briefing attachment rollup.
# Events without a mapping don't contribute to feature rates (e.g.
# session_started is counted only in the per-day event rollup).
FEATURE_OF: dict[str, str] = {
    Event.FORECAST_MAP_OPENED.value: "forecast_map",
    Event.FORECAST_MAP_LAYER_CHANGED.value: "forecast_map",
    Event.SKEWT_OPENED.value: "skewt",
    Event.COMPARE_OPENED.value: "compare",
    Event.COMPARE_AIRPORT_ADDED.value: "compare",
    Event.ALTITUDE_TABLE_OPENED.value: "altitude_table",
    Event.TIMING_SCENARIOS_USED.value: "timing_scenarios",
    Event.AUTO_REFRESH_ENABLED.value: "auto_refresh",
    Event.BRIEFING_REFRESH_REQUESTED.value: "manual_refresh",
}


# Features tracked in the per-briefing rollup. The rollup also synthesises a
# ``detailed_mode`` feature from the most recent ``display_mode.changed``
# event in each briefing (see rollup.py).
KNOWN_FEATURES: tuple[str, ...] = (
    "forecast_map",
    "skewt",
    "compare",
    "altitude_table",
    "timing_scenarios",
    "auto_refresh",
    "manual_refresh",
    "detailed_mode",
)


# ---------------------------------------------------------------------------
# Cross-section config snapshot (``xsection.viewed``)
# ---------------------------------------------------------------------------
#
# The snapshot event's ``props`` are rolled up into a per-dimension breakdown
# in ``analytics_xsection_config_daily``. Two kinds of dimension:
#
# * Scalar dimensions — each props key is a single low-cardinality value
#   (enum/id/bool). One view contributes one ``(dimension, value)`` row.
#   Booleans are normalised to ``"true"``/``"false"`` so they GROUP BY as
#   strings.
# * Set dimensions — the props key holds an array; each element becomes a
#   value under a single output dimension. ``layers`` → dimension ``"layer"``,
#   one row per *enabled layer* per view (the per-layer attachment signal).
#
# The rollup is generic over whatever keys are present, so adding/removing a
# dimension here is the only change needed (plus the mirrored client props).
XSECTION_SCALAR_DIMENSIONS: tuple[str, ...] = (
    "theme",
    "preset",
    "layout",
    "cloud_style",
    "display_mode",
    "model",
    "route_graph_visible",
    "map_fronts_visible",
    # Optional medium-cardinality metrics (omitted by the client when N/A).
    "route_graph_left_metric",
    "route_graph_right_metric",
    "map_color_metric",
    "map_width_metric",
)

# Array-valued props key → output dimension name for each element.
XSECTION_SET_DIMENSIONS: dict[str, str] = {
    "layers": "layer",
}
