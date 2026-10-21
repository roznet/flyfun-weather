/** Forecast Overview tab's URL keys (`fc.*`), composed by `maps-main.ts`.
 *
 * Do not rename these: the iOS app opens shared `/maps.html` links natively
 * and reads `fc.day/hour/model/metric/apt/date` by name (`AppState.mapDeepLink`).
 * New keys are safe, it ignores unknown ones.
 *
 * `fc.day` is relative (days from the UTC today of whoever opens the link), so
 * on its own a link drifts a day for each day it sits unopened. `fc.date`
 * (#840) is the absolute UTC date of the same slot; when present it wins, and
 * `fc.day` stays for old clients and old links. */

import { FORECAST_INDIVIDUAL_MODELS } from '../adapters/maps-adapter';
import { FORECAST_METRICS, type ForecastMetric } from './weather-map-format';
import { mapViewField } from '../utils/map-view';
import type { Codec } from '../utils/url-state';

const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Epoch ms of a strict `YYYY-MM-DD` UTC date, or null (bad shape, or a
 *  calendar overflow like 2026-02-30 that `Date.UTC` would roll forward). */
function isoDateMs(s: string): number | null {
  const m = ISO_DATE_RE.exec(s);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const ms = Date.UTC(y, mo - 1, d);
  const back = new Date(ms);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d) return null;
  return ms;
}

/** A garbage `fc.date` reads as absent, so the link falls back to `fc.day`. */
const isoDateCodec: Codec<string> = {
  parse: (s) => (isoDateMs(s) != null ? s : undefined),
  format: (v) => v,
};

/** `YYYY-MM-DD` of `day` days after `today`'s UTC date: the date `fc.day`
 *  names, matching the server's `forecast_hour_for` (`now + day`). */
export function forecastDayIsoDate(day: number, today: Date): string {
  return new Date(Date.UTC(
    today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() + day,
  )).toISOString().slice(0, 10);
}

/** Why a linked `fc.date` could not be shown as is. */
export type LinkedDateFallback = 'past' | 'beyond' | 'unavailable';

export interface LinkedDay {
  /** Relative day to show. */
  day: number;
  /** Set when `day` is not the linked date: the page must say so. */
  fallback: LinkedDateFallback | null;
}

/** Resolve a linked `fc.date` to a relative day against the days the grid
 *  offers (`available` days of `/maps/forecast/days`). Not offered → nearest
 *  offered day, never silently: a past date shows the first offered day
 *  (today), one beyond the horizon the last, one inside the horizon but not
 *  fetched yet the nearest (ties to the earlier). `null` for a malformed date
 *  or an empty grid (the caller keeps `fc.day`). Same rules on iOS
 *  (`ForecastMapViewModel.resolveLinkedDate`). */
export function resolveLinkedDate(date: string, offered: readonly number[], today: Date): LinkedDay | null {
  const ms = isoDateMs(date);
  if (ms == null || offered.length === 0) return null;
  const todayMs = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const day = Math.round((ms - todayMs) / 86_400_000);
  const sorted = [...offered].sort((a, b) => a - b);
  if (sorted.includes(day)) return { day, fallback: null };
  if (day < sorted[0]) return { day: sorted[0], fallback: 'past' };
  if (day > sorted[sorted.length - 1]) return { day: sorted[sorted.length - 1], fallback: 'beyond' };
  const nearest = sorted.reduce((a, b) => (Math.abs(b - day) < Math.abs(a - day) ? b : a));
  return { day: nearest, fallback: 'unavailable' };
}

export const FORECAST_URL_SCHEMA = {
  'fc.day':    { default: 0,  values: [0, 1, 2, 3, 4, 5, 6] as readonly number[] },
  'fc.hour':   { default: 12, values: [6, 9, 12, 15, 18] as readonly number[] },
  // Absolute UTC date of the slot (#840); wins over `fc.day` when present.
  // Empty = follow `fc.day` (bare URLs stay "today").
  'fc.date':   { default: '', codec: isoDateCodec },
  'fc.model':  { default: 'worst', values: ['worst', 'majority', 'gfs', 'icon', 'ecmwf'] as readonly string[] },
  'fc.metric': {
    default: 'flight_category' as ForecastMetric,
    values: FORECAST_METRICS as readonly ForecastMetric[],
  },
  // Open airport-profile panel (ICAO) and the panel's selected model.
  // Empty ICAO = no panel open. Panel model defaults differ from the
  // map's per-airport model (panel always needs a real model, not a
  // consensus mode), so they're separate keys.
  'fc.apt':    { default: '' },
  'fc.apModel':{ default: 'ecmwf', values: FORECAST_INDIVIDUAL_MODELS },
  // Panel view: card (default) | cross | skewt. Lets a shared link land
  // straight on the cross-section; bare links open the summary card.
  'fc.apView': { default: 'card', values: ['card', 'cross', 'skewt'] as readonly string[] },
  'fc.view':   mapViewField(),
};
