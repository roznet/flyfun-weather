/** Debrief recap and single-tick replay (#769) — pure helpers, no DOM.
 *
 * The server (#711, `designs/debrief-recap.md`) records one display snapshot
 * per live tick and computes the recap on request. These helpers decide what
 * the page shows from those responses:
 *
 * - the recap's time-base caption and departure gap;
 * - which recorded tick a route point maps to (aircraft time at the point,
 *   then the last tick at or before it — the rule `/live/at` applies);
 * - the forecast lane: one worst-of row from the route-weather advisories,
 *   each advisory's per-model bands combined with the pack's own aggregation
 *   (worst / majority), the same rule as its route-level grade;
 * - the replay mode state: while a tick is on screen, live updates land in
 *   the store but do not replace the view; going back re-renders the latest;
 * - the recap drawn as a `LiveRibbon`, so the ribbon renderer draws it.
 *
 * Observations only, as everywhere in the live layer: nothing here re-grades.
 */

import type {
  ForecastSnapshot,
  LiveLayer,
  LiveRibbon,
  LiveRibbonSegment,
  LiveStorm,
  LiveStorms,
  LiveTickEntry,
  LiveTickResponse,
  RecapForecast,
  RecapResponse,
  RibbonWeather,
} from '../store/types';
import { formatHhmmZ, isoToMs } from './live-layer';

// --- Captions ------------------------------------------------------------------

/** What the recap's times are: "actual times", "partly observed", "planned
 *  schedule", or "planned, flight cancelled". Null when the recap has no
 *  time base (untimed / nothing recorded). */
export function timeBaseCaption(recap: Pick<RecapResponse, 'time_base' | 'cancelled'>): string | null {
  if (recap.cancelled) return 'planned, flight cancelled';
  switch (recap.time_base) {
    case 'observed': return 'actual times';
    case 'partial': return 'partly observed';
    case 'planned': return 'planned schedule';
    default: return null;
  }
}

/** "forecast for 16:45Z departure, flown 17:40Z" — the pack graded its
 *  advisories at its own planned ETAs, so a late departure is shown, not
 *  hidden. "planned" instead of "flown" when the recap is on the plan.
 *  Null when either time is unknown. */
export function departureGapText(
  plannedDeparture: string | null | undefined,
  departedAt: string | null | undefined,
  timeBase: string | null | undefined,
): string | null {
  const planned = formatHhmmZ(plannedDeparture);
  const departed = formatHhmmZ(departedAt);
  if (!planned || !departed) return null;
  const verb = timeBase === 'observed' || timeBase === 'partial' ? 'flown' : 'planned';
  if (Math.abs(isoToMs(plannedDeparture) - isoToMs(departedAt)) < 60_000) {
    return `forecast for ${planned} departure, ${verb} on time`;
  }
  return `forecast for ${planned} departure, ${verb} ${departed}`;
}

/** The "nothing to draw" line for a recap that is not `ok`. Never an empty
 *  "nothing observed": the server's message says why, with a short title. */
export function recapEmptyTitle(status: string | null | undefined): string {
  switch (status) {
    case 'untimed': return 'Cannot place the flight along the route';
    case 'load_failed': return 'Recap unavailable';
    default: return 'Not recorded for this flight';
  }
}

// --- Route point → tick -----------------------------------------------------------

/** A known (position, time) pair along the flight. */
export interface TimePoint {
  along_nm: number;
  ms: number;
}

/** The recap's own (position, time) pairs: waypoint ETAs and each stretch's
 *  midpoint time, all from `FlightProgress` on the server. Sorted by
 *  position. */
export function recapTimePoints(recap: Pick<RecapResponse, 'waypoints' | 'segments'>): TimePoint[] {
  const out: TimePoint[] = [];
  for (const wp of recap.waypoints ?? []) {
    const ms = isoToMs(wp.eta);
    if (wp.along_nm != null && !isNaN(ms)) out.push({ along_nm: wp.along_nm, ms });
  }
  for (const seg of recap.segments ?? []) {
    const ms = isoToMs(seg.at);
    if (!isNaN(ms)) out.push({ along_nm: (seg.from_nm + seg.to_nm) / 2, ms });
  }
  return out.sort((a, b) => a.along_nm - b.along_nm);
}

/** During the window there is no recap yet: each tick's position at its own
 *  time is the time base (the server's `FlightProgress` then). Sorted by
 *  position; ticks parked at 0 (before departure) keep only the latest. */
export function tickTimePoints(ticks: LiveTickEntry[]): TimePoint[] {
  const byPos = new Map<number, number>();
  for (const t of ticks) {
    const ms = isoToMs(t.tick_at);
    if (t.flown_nm == null || isNaN(ms)) continue;
    const prev = byPos.get(t.flown_nm);
    // Several ticks at one position (before departure, after arrival): the
    // aircraft was there from the first; at 0 it left at the last.
    if (prev == null || (t.flown_nm === 0 ? ms > prev : ms < prev)) byPos.set(t.flown_nm, ms);
  }
  return [...byPos.entries()].map(([along_nm, ms]) => ({ along_nm, ms }))
    .sort((a, b) => a.along_nm - b.along_nm);
}

/** The aircraft's time at `alongNm`, interpolated between known points and
 *  clamped to the ends. NaN without points. */
export function aircraftTimeAt(alongNm: number, points: TimePoint[]): number {
  if (points.length === 0) return NaN;
  if (alongNm <= points[0].along_nm) return points[0].ms;
  const last = points[points.length - 1];
  if (alongNm >= last.along_nm) return last.ms;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (alongNm <= b.along_nm) {
      const span = b.along_nm - a.along_nm;
      const f = span > 0 ? (alongNm - a.along_nm) / span : 0;
      return a.ms + f * (b.ms - a.ms);
    }
  }
  return last.ms;
}

/** The last recorded tick at or before `ms` — what `/live/at` serves. Null
 *  when every tick is after it (the server would 404). */
export function tickAtOrBefore(ticks: LiveTickEntry[], ms: number): LiveTickEntry | null {
  if (isNaN(ms)) return null;
  let best: LiveTickEntry | null = null;
  let bestMs = -Infinity;
  for (const t of ticks) {
    const tm = isoToMs(t.tick_at);
    if (!isNaN(tm) && tm <= ms && tm > bestMs) {
      best = t;
      bestMs = tm;
    }
  }
  return best;
}

/** A route point → the tick to replay: the aircraft's time there, then the
 *  last tick at or before it. */
export function tickForRoutePoint(
  alongNm: number,
  points: TimePoint[],
  ticks: LiveTickEntry[],
): LiveTickEntry | null {
  return tickAtOrBefore(ticks, aircraftTimeAt(alongNm, points));
}

// --- Forecast lane -----------------------------------------------------------------

/** The advisories whose evaluator category is icing, cloud, precipitation,
 *  turbulence or convective. That includes `vmc_cruise` (category cloud) and
 *  `mountain_wind` (category turbulence: wave-corroborated, not trip wind).
 *  Left out: airport, trip wind (`headwind`), feasibility, model-quality,
 *  front and sun advisories. The observed lane beside it sees radar and
 *  lightning only, so a cloud / icing / turbulence grade has nothing
 *  observed to match; first version, the #769 review pass settles it. */
export const ROUTE_WEATHER_ADVISORIES: ReadonlySet<string> = new Set([
  'cloud_top',
  'vmc_cruise',
  'convective',
  'convective_character',
  'aircraft_icing',
  'enroute_precip',
  'fiki_icing',
  'freezing_precip',
  'icing_escape',
  'turbulence',
  'mountain_wind',
]);

const SEVERITY_ORDER: Record<string, number> = { green: 0, amber: 1, red: 2 };

/** `AdvisoryStatus.worst` / `.majority` (models/advisories.py): unavailable
 *  ignored; nothing gradable is "unavailable", never green. */
export function combineSeverities(values: string[], aggregation: string | null | undefined): string {
  const valid = values.filter((v) => v in SEVERITY_ORDER);
  if (valid.length === 0) return 'unavailable';
  const worst = (vs: string[]) => vs.reduce((a, b) => (SEVERITY_ORDER[b] > SEVERITY_ORDER[a] ? b : a));
  if (aggregation !== 'majority') return worst(valid);
  const counts = new Map<string, number>();
  for (const v of valid) counts.set(v, (counts.get(v) ?? 0) + 1);
  const max = Math.max(...counts.values());
  return worst([...counts.entries()].filter(([, c]) => c === max).map(([v]) => v));
}

/** One stretch of the forecast lane. */
export interface ForecastBand {
  from_nm: number;
  to_nm: number;
  severity: string;
  /** The advisories at that severity there (empty when green). */
  advisories: string[];
}

function severityAt(ribbon: [number, number, string][], nm: number): string | null {
  for (let i = 0; i < ribbon.length; i++) {
    const [lo, hi, sev] = ribbon[i];
    if (nm >= lo && (nm < hi || (i === ribbon.length - 1 && nm <= hi))) return sev;
  }
  return null;
}

/** The forecast lane as one worst-of row: at every stretch, each included
 *  advisory's consensus across models (the pack's aggregation), then the
 *  worst of those. Adjacent stretches with the same severity are merged, the
 *  advisories behind them unioned. Empty when the pack has no lane. */
export function forecastLaneBands(
  forecast: RecapForecast | null | undefined,
  include: (advisoryId: string) => boolean = (id) => ROUTE_WEATHER_ADVISORIES.has(id),
): ForecastBand[] {
  if (!forecast?.available) return [];
  const advisories = (forecast.advisories ?? []).filter((a) => include(a.advisory_id));
  const edges = new Set<number>();
  for (const a of advisories) {
    for (const m of a.per_model) for (const [lo, hi] of m.ribbon) { edges.add(lo); edges.add(hi); }
  }
  const xs = [...edges].sort((a, b) => a - b);
  const out: ForecastBand[] = [];
  for (let i = 1; i < xs.length; i++) {
    const lo = xs[i - 1];
    const hi = xs[i];
    if (hi <= lo) continue;
    const mid = (lo + hi) / 2;
    let severity = 'unavailable';
    let ids: string[] = [];
    for (const a of advisories) {
      const perModel = a.per_model
        .map((m) => severityAt(m.ribbon, mid))
        .filter((s): s is string => s != null);
      if (perModel.length === 0) continue;
      const sev = combineSeverities(perModel, forecast.aggregation);
      const rank = SEVERITY_ORDER[sev] ?? -1;
      const bestRank = SEVERITY_ORDER[severity] ?? -1;
      if (rank > bestRank) {
        severity = sev;
        ids = rank > 0 ? [a.advisory_id] : [];
      } else if (rank === bestRank && rank > 0) {
        ids.push(a.advisory_id);
      }
    }
    const prev = out[out.length - 1];
    if (prev && prev.severity === severity && prev.to_nm === lo) {
      prev.to_nm = hi;
      for (const id of ids) if (!prev.advisories.includes(id)) prev.advisories.push(id);
    } else {
      out.push({ from_nm: lo, to_nm: hi, severity, advisories: ids });
    }
  }
  return out;
}

// --- Replay mode -------------------------------------------------------------------

/** What the Observed section shows: the base (live in the window, the recap
 *  after it), a tick being fetched, or a recorded tick. */
/** Where each stepper button goes (#769): a tick time, or null when the
 *  button is disabled. On the base view (`selected` null), ▶ starts at the
 *  first tick and ◀ at the last; the alert jumps likewise start from the ends. */
export interface StepTargets {
  first: string | null;
  prev: string | null;
  next: string | null;
  last: string | null;
  prevAlert: string | null;
  nextAlert: string | null;
}

/** The ticks where the alerts on screen changed. Falls back to a change in
 *  `alert_count` for a tick index without `alert_changed` (older server). */
export function alertChangeFlags(ticks: LiveTickEntry[]): boolean[] {
  return ticks.map((t, i) => {
    if (t.alert_changed != null) return t.alert_changed;
    const prev = i > 0 ? (ticks[i - 1].alert_count ?? 0) : 0;
    return (t.alert_count ?? 0) !== prev;
  });
}

export function stepTargets(ticks: LiveTickEntry[], selected: string | null): StepTargets {
  const timed = ticks.filter((t) => t.tick_at);
  const times = timed.map((t) => t.tick_at as string);
  const changed = alertChangeFlags(timed);
  const n = times.length;
  const none: StepTargets = { first: null, prev: null, next: null, last: null, prevAlert: null, nextAlert: null };
  if (!n) return none;
  const selMs = selected != null ? isoToMs(selected) : null;
  const i = selMs == null ? -1 : times.findIndex((t) => isoToMs(t) === selMs);
  const alertIdx = times.map((_, k) => k).filter((k) => changed[k]);
  if (i < 0) {
    return {
      first: times[0], prev: times[n - 1], next: times[0], last: times[n - 1],
      prevAlert: alertIdx.length ? times[alertIdx[alertIdx.length - 1]] : null,
      nextAlert: alertIdx.length ? times[alertIdx[0]] : null,
    };
  }
  const before = alertIdx.filter((k) => k < i);
  const after = alertIdx.filter((k) => k > i);
  return {
    first: i > 0 ? times[0] : null,
    prev: i > 0 ? times[i - 1] : null,
    next: i < n - 1 ? times[i + 1] : null,
    last: i < n - 1 ? times[n - 1] : null,
    prevAlert: before.length ? times[before[before.length - 1]] : null,
    nextAlert: after.length ? times[after[0]] : null,
  };
}

export type ReplayState =
  | { kind: 'base' }
  | { kind: 'loading'; tick: string }
  | { kind: 'tick'; tick: string; view: LiveTickResponse };

export const REPLAY_BASE: ReplayState = { kind: 'base' };

/** Start viewing a tick. The previous view stays on screen until the new one
 *  lands (no flash of live data between two ticks). */
export function startTick(state: ReplayState, tick: string): ReplayState {
  if (state.kind === 'tick') return { ...state, tick };
  return { kind: 'loading', tick };
}

/** A tick response landed. Dropped when the pilot went back, or asked for
 *  another tick meanwhile (a slow response must not win). */
export function tickLoaded(state: ReplayState, tick: string, view: LiveTickResponse): ReplayState {
  if (state.kind === 'base' || state.tick !== tick) return state;
  return { kind: 'tick', tick, view };
}

/** The tick failed to load: back to what was on screen. */
export function tickFailed(state: ReplayState, tick: string, previous: ReplayState): ReplayState {
  if (state.kind === 'base' || state.tick !== tick) return state;
  return previous.kind === 'tick' ? previous : REPLAY_BASE;
}

/** Is a recorded tick on screen? */
export function isReplaying(state: ReplayState): state is { kind: 'tick'; tick: string; view: LiveTickResponse } {
  return state.kind === 'tick';
}

/** The live layer the Observed section renders: the tick while replaying,
 *  else the store's latest. Live polling keeps replacing `live`; the view
 *  ignores it until the pilot goes back. */
export function observedLive(state: ReplayState, live: LiveLayer | null): LiveLayer | null {
  return state.kind === 'tick' ? state.view : live;
}

/** A tick folded into the snapshot for the snapshot-driven sections (radar &
 *  lightning cards, METAR table, SIGMETs, changes). Unconditional, unlike
 *  `applyLiveToSnapshot`: a block the tick did not record stays empty rather
 *  than showing newer data from the pack. */
export function snapshotAtTick(snapshot: ForecastSnapshot, view: LiveTickResponse): ForecastSnapshot {
  return {
    ...snapshot,
    route_observations: view.route_observations ?? null,
    route_sigmets: view.route_sigmets ?? null,
    observed_conditions: view.observed_conditions ?? null,
    last_refresh_delta: view.last_refresh_delta ?? null,
    live_changes: view.changes ?? null,
    live_updated_at: view.live_updated_at ?? view.tick_at,
  } as ForecastSnapshot;
}

/** "Viewing 17:23Z" for the mode bar; null on the base view. */
export function modeBarText(state: ReplayState): string | null {
  if (state.kind === 'base') return null;
  const time = formatHhmmZ(state.kind === 'tick' ? state.view.tick_at : state.tick);
  return state.kind === 'loading' ? `Loading ${time}…` : `Viewing ${time}`;
}

// --- Recap as a ribbon --------------------------------------------------------------

/** A band clipped to the stretch it was read for: a rain area of the 17:20Z
 *  tick must not paint over the stretch the 17:40Z tick describes. Null when
 *  nothing is left. */
export function clipBand(band: RibbonWeather, lo: number, hi: number): RibbonWeather | null {
  const from = Math.max(band.from_nm, lo);
  const to = Math.min(band.to_nm, hi);
  if (to <= from) return null;
  const profile = (band.profile ?? []).filter(([along]) => along >= lo && along <= hi);
  return { ...band, from_nm: from, to_nm: to, profile };
}

/** The recap as the ribbon renderer's input: each stretch as it was when the
 *  aircraft was there. Stations and SIGMET geometry are not on the recap (the
 *  replay has them); bands only when every observed stretch had the cells
 *  feed, else the radar strip. */
export function recapRibbon(recap: RecapResponse): { ribbon: LiveRibbon; storms: LiveStorms } {
  const segs = recap.segments ?? [];
  const observed = segs.filter((s) => s.status === 'observed');
  const bandsOk = observed.length > 0 && observed.every((s) => s.weather_status === 'available');
  const byIndex = new Map(segs.map((s) => [s.index, s]));
  const weather: RibbonWeather[] = [];
  if (bandsOk) {
    for (const b of recap.weather ?? []) {
      const seg = byIndex.get(b.segment_index ?? -1);
      if (!seg || b.from_nm == null || b.to_nm == null) continue;
      const clipped = clipBand(b as RibbonWeather, seg.from_nm, seg.to_nm);
      if (clipped) weather.push(clipped);
    }
  }
  const segments: LiveRibbonSegment[] = segs.map((s, i) => ({
    index: s.index ?? i,
    from_nm: s.from_nm,
    to_nm: s.to_nm,
    eta_from: s.at ?? null,
    eta_to: s.at ?? null,
    radar_max_dbz: s.status === 'observed' ? s.radar_max_dbz ?? null : null,
    radar_intensity: s.radar_intensity ?? null,
    // A stretch not observed is "no sample": grey/empty, never "no echo".
    radar_status: s.status === 'observed' ? (s.radar_status ?? 'no_sample') : 'no_sample',
    lightning: s.status === 'observed' ? s.lightning ?? null : null,
    sigmet_ids: s.sigmet_ids ?? [],
    storm_ids: s.storm_ids ?? [],
    focus: null,
  }));
  const routeNm = recap.route_nm ?? Math.max(0, ...segs.map((s) => s.to_nm));
  const ribbon: LiveRibbon = {
    route_nm: routeNm,
    flown_nm: null,
    departure_at: recap.departed_at ?? null,
    arrival_at: recap.arrival_at ?? null,
    segment_nm: recap.segment_nm ?? 0,
    radar_radius_nm: recap.radar_radius_nm ?? 10,
    radar_time: null,
    waypoints: recap.waypoints ?? [],
    segments,
    stations: [],
    sigmets: [],
    weather,
    weather_status: bandsOk ? 'available' : 'unavailable',
    weather_corridor_nm: recap.weather_corridor_nm ?? null,
    weather_bin_nm: recap.weather_bin_nm ?? null,
  };
  const storms: LiveStorms = {
    status: 'available',
    corridor_nm: recap.weather_corridor_nm ?? 30,
    storms: (recap.storms ?? []) as LiveStorm[],
  } as LiveStorms;
  return { ribbon, storms };
}

/** "17:23Z" under each observed stretch: the radar frame it was read from
 *  (the tick time when the tick had no radar). '' for the rest. */
export function segmentSeenAt(seg: RecapResponse['segments'][number]): string {
  if (seg.status !== 'observed') return '';
  return formatHhmmZ(seg.radar_time ?? seg.tick_at);
}

/** How a stretch that was not observed reads. */
export function segmentStatusText(status: string): string {
  switch (status) {
    case 'not_reached': return 'not recorded';
    case 'before_recording': return 'before recording';
    case 'untimed': return 'untimed';
    default: return '';
  }
}

// --- Distance × time grid ------------------------------------------------------------

/** One row of the grid: a recorded tick's radar lane, the aircraft's
 *  position then, and which of its stretches the recap took (the diagonal). */
export interface GridRow {
  tick_at: string;
  flown_nm: number | null;
  segments: { from_nm: number; to_nm: number; seg: LiveRibbonSegment; diagonal: boolean }[];
}

export function gridRows(ticks: LiveTickEntry[], recap: RecapResponse | null): GridRow[] {
  const taken = new Map<number, Set<number>>();
  for (const s of recap?.segments ?? []) {
    if (s.status !== 'observed') continue;
    const ms = isoToMs(s.tick_at);
    if (isNaN(ms)) continue;
    if (!taken.has(ms)) taken.set(ms, new Set());
    taken.get(ms)!.add((s.from_nm + s.to_nm) / 2);
  }
  return ticks
    .filter((t) => t.tick_at && (t.segments ?? []).length > 0)
    .map((t) => {
      const mids = taken.get(isoToMs(t.tick_at)) ?? new Set<number>();
      return {
        tick_at: t.tick_at as string,
        flown_nm: t.flown_nm ?? null,
        segments: (t.segments ?? []).map((s, i) => ({
          from_nm: s.from_nm,
          to_nm: s.to_nm,
          seg: {
            index: i, from_nm: s.from_nm, to_nm: s.to_nm,
            radar_status: s.radar_status ?? 'no_sample',
            radar_max_dbz: s.radar_max_dbz ?? null,
            lightning: s.lightning ?? null,
          } as LiveRibbonSegment,
          diagonal: [...mids].some((m) => m >= s.from_nm && m < s.to_nm),
        })),
      };
    });
}
