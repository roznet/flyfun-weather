/** Pure helpers for the briefing route-map airport forecast overlay (#424).
 *
 *  No DOM / Leaflet / side effects, so the day/hour boundary math, hour snapping
 *  and deep-link building are unit-testable (tests/unit/forecast-overlay.test.ts).
 *  The forecast horizon and the sample hours each day offers are read from the
 *  server's grid (`fetchAvailableDays`), never restated here — the grid is not
 *  rectangular (far days carry fewer models and coarser hours) and
 *  designs/forecast-page.md is explicit that `tasks/forecast_grid.py` is the one
 *  source of truth and the client must not hardcode it.
 */

import { FORECAST_INDIVIDUAL_MODELS, type DayAvailability } from '../../adapters/maps-adapter';

export interface ForecastOverlaySlot {
  /** Relative day (0 = today) the flight falls on. */
  day: number;
  /** Sample hour actually offered for that day, nearest the departure hour. */
  hour: number;
  /** Models that have airport data for this day/hour (server-advertised). Used
   *  to decide whether the briefing's selected model can be drawn. */
  models: string[];
}

/** Relative (day, hour) in UTC for a departure ISO string, matching the forecast
 *  endpoint's `now + day` date labelling. `null` when unparseable. `now` is
 *  injected so the boundary math is deterministic in tests. */
export function relativeDayHour(
  departureIso: string | null | undefined,
  now: Date,
): { day: number; hour: number } | null {
  if (!departureIso) return null;
  const dep = new Date(departureIso);
  if (Number.isNaN(dep.getTime())) return null;
  const depDate = Date.UTC(dep.getUTCFullYear(), dep.getUTCMonth(), dep.getUTCDate());
  const nowDate = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const day = Math.round((depDate - nowDate) / 86_400_000);
  return { day, hour: dep.getUTCHours() };
}

/** Nearest value in `hours` to `h`; ties resolve to the earlier hour (stable
 *  reduce). `null` for an empty list. */
export function nearestHour(hours: number[], h: number): number | null {
  if (!hours.length) return null;
  return hours.reduce((a, b) => (Math.abs(b - h) < Math.abs(a - h) ? b : a));
}

/** Resolve the overlay slot for a flight against the server's advertised grid.
 *  `null` when the flight is outside the forecast horizon or the target day
 *  carries no data — the overlay is then not offered. The offered hours come
 *  from `days`, so the value always matches what the day actually holds (e.g.
 *  D+6's coarse 6/12/18 rather than the fine near-day set). */
export function resolveOverlaySlot(
  departureIso: string | null | undefined,
  days: DayAvailability[],
  now: Date,
): ForecastOverlaySlot | null {
  const rel = relativeDayHour(departureIso, now);
  if (!rel) return null;
  const entry = days.find((d) => d.day === rel.day && d.available && d.hours.length > 0);
  if (!entry) return null;
  const hour = nearestHour(entry.hours, rel.hour);
  if (hour == null) return null;
  return { day: rel.day, hour, models: entry.models };
}

/** Short UTC label for a forecast valid-time ISO string, e.g. "Wed 12Z". */
export function formatForecastTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()];
  return `${wd} ${String(d.getUTCHours()).padStart(2, '0')}Z`;
}

/** Deep-link to the full forecast map seeded with the same slot/model/metric.
 *  `fc.model` is set only for a supported individual model (shared list);
 *  consensus/other models fall back to the full map's default. */
export function forecastMapUrl(slot: ForecastOverlaySlot, model: string, metric: string): string {
  const p = new URLSearchParams();
  p.set('fc.day', String(slot.day));
  p.set('fc.hour', String(slot.hour));
  if (FORECAST_INDIVIDUAL_MODELS.includes(model)) p.set('fc.model', model);
  p.set('fc.metric', metric);
  return `maps.html?${p.toString()}`;
}
