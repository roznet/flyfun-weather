/** Historical tab's URL keys (`hist.*`, #629), composed by `maps-main.ts`.
 *
 * Do not rename these: the iOS app reads `hist.date/time/lead/source/metric/apt`
 * by name (`AppState.historicalMapDeepLink`). */

import { FORECAST_METRICS, type ForecastMetric } from './weather-map-format';
import { mapViewField } from '../utils/map-view';
import type { UrlValues } from '../utils/url-state';

export const HISTORICAL_SOURCES = [
  'metar', 'taf', 'worst', 'majority', 'gfs', 'icon', 'ecmwf',
] as const;
export type HistoricalSource = typeof HISTORICAL_SOURCES[number];
export const HISTORICAL_LEADS: readonly number[] = [0, 1, 2, 3, 4, 5, 6];

/** Deep-linkable state. Empty date/time mean "latest available slot"; empty
 *  view means the map's initial view. */
export interface HistoricalState {
  date: string;   // YYYY-MM-DD (UTC)
  time: string;   // HH:MM (UTC)
  lead: number;
  source: HistoricalSource;
  metric: ForecastMetric;
  apt: string;
  view: string;   // lat,lon,zoom (see utils/map-view.ts)
}

export const HISTORICAL_URL_SCHEMA = {
  // Empty date/time = the latest available slot.
  'hist.date':   { default: '' },
  'hist.time':   { default: '' },
  'hist.lead':   { default: 0, values: HISTORICAL_LEADS },
  'hist.source': {
    default: 'metar' as HistoricalSource,
    values: HISTORICAL_SOURCES as readonly HistoricalSource[],
  },
  'hist.metric': {
    default: 'flight_category' as ForecastMetric,
    values: FORECAST_METRICS as readonly ForecastMetric[],
  },
  'hist.apt':    { default: '' },
  'hist.view':   mapViewField(),
};

export type HistoricalUrlValues = UrlValues<typeof HISTORICAL_URL_SCHEMA>;

export function historicalStateFromUrl(v: HistoricalUrlValues): HistoricalState {
  return {
    date: v['hist.date'], time: v['hist.time'], lead: v['hist.lead'],
    source: v['hist.source'], metric: v['hist.metric'], apt: v['hist.apt'],
    view: v['hist.view'],
  };
}

export function historicalUrlValues(s: HistoricalState): HistoricalUrlValues {
  return {
    'hist.date': s.date, 'hist.time': s.time, 'hist.lead': s.lead,
    'hist.source': s.source, 'hist.metric': s.metric, 'hist.apt': s.apt,
    'hist.view': s.view,
  };
}
