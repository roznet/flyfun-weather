/**
 * Canonical analytics event names. Mirror of
 * ``src/weatherbrief/analytics/events.py``. The server rejects unknown
 * event names — keep these in sync when adding new ones.
 */

export const EVENTS = {
  // Session lifecycle
  SESSION_STARTED: 'app.session_started',

  // Flight lifecycle
  FLIGHT_CREATED: 'flight.created',

  // Briefing lifecycle
  BRIEFING_OPENED: 'briefing.opened',
  BRIEFING_REFRESH_REQUESTED: 'briefing.refresh_requested',
  BRIEFING_REFRESHED: 'briefing.refreshed',

  // Cross-section snapshot (one per view, carries current display config)
  XSECTION_VIEWED: 'xsection.viewed',

  // Visualization features
  FORECAST_MAP_OPENED: 'forecast_map.opened',
  FORECAST_MAP_LAYER_CHANGED: 'forecast_map.layer_changed',
  SKEWT_OPENED: 'skewt.opened',
  COMPARE_OPENED: 'compare.opened',
  COMPARE_AIRPORT_ADDED: 'compare.airport_added',
  ALTITUDE_TABLE_OPENED: 'altitude_table.opened',
  // Flexibility / timing-scenarios feature (props: {mode}).
  TIMING_SCENARIOS_USED: 'timing_scenarios.used',

  // Onboarding
  TOUR_STARTED: 'tour.started',
  TOUR_COMPLETED: 'tour.completed',

  // Standalone (maps page — no briefing context)
  CLIMATOLOGY_OPENED: 'climatology.opened',

  // Help page (no briefing context)
  HELP_WHATS_NEW_OPENED: 'help.whats_new_opened',

  // Donate nudge (web-only; props: {kind, rung})
  DONATE_NUDGE_SHOWN: 'donate.nudge_shown',
  DONATE_NUDGE_OPENED: 'donate.nudge_opened',
  DONATE_NUDGE_CLICKED: 'donate.nudge_clicked',
  DONATE_NUDGE_DISMISSED: 'donate.nudge_dismissed',

  // User preferences
  AUTO_REFRESH_ENABLED: 'auto_refresh.enabled',
  AUTO_REFRESH_DISABLED: 'auto_refresh.disabled',
  DISPLAY_MODE_CHANGED: 'display_mode.changed',
} as const;

export type EventName = (typeof EVENTS)[keyof typeof EVENTS];
