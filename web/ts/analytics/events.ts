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

  // Visualization features
  FORECAST_MAP_OPENED: 'forecast_map.opened',
  FORECAST_MAP_LAYER_CHANGED: 'forecast_map.layer_changed',
  SKEWT_OPENED: 'skewt.opened',
  COMPARE_OPENED: 'compare.opened',
  COMPARE_AIRPORT_ADDED: 'compare.airport_added',

  // User preferences
  AUTO_REFRESH_ENABLED: 'auto_refresh.enabled',
  AUTO_REFRESH_DISABLED: 'auto_refresh.disabled',
  DISPLAY_MODE_CHANGED: 'display_mode.changed',

  // Search / navigation
  AIRPORT_SEARCHED: 'airport.searched',
} as const;

export type EventName = (typeof EVENTS)[keyof typeof EVENTS];
