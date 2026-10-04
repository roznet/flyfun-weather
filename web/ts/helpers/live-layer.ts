/** Live observation layer (#637) — pure helpers, no DOM, unit-tested.
 *
 * The server keeps one live overlay per flight, relative to its latest pack:
 * newest METAR/TAF, SIGMETs, observed conditions and the "since this
 * briefing" changes. The snapshot endpoint already folds it in when the pack
 * is the latest; `GET /flights/{id}/live` lets the page pick up newer data
 * without reloading the whole pack. These helpers decide whether a live
 * response applies and fold it into a snapshot.
 */

import { t } from '../i18n/i18n';
import type {
  ForecastSnapshot,
  LiveChange,
  LiveChanges,
  LiveLayer,
  SigmetAlongRoute,
} from '../store/types';

/** Window around the flight when the server keeps the live layer fresh. */
export const LIVE_WINDOW_BEFORE_H = 3;
export const LIVE_WINDOW_AFTER_H = 1;
/** "Observed as of" turns stale-tinted past this age. */
export const OBSERVED_STALE_MIN = 30;

/** Parse an ISO timestamp to epoch ms. A string without any zone designator
 *  is taken as UTC (pack timestamps are UTC). NaN when unparseable. */
export function isoToMs(iso: string | null | undefined): number {
  if (!iso) return NaN;
  const s = iso.trim();
  // Date-time without Z / ±hh:mm → treat as UTC rather than local time.
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(s);
  const isDateTime = /\d{2}:\d{2}/.test(s);
  return new Date(isDateTime && !hasZone ? `${s.replace(' ', 'T')}Z` : s).getTime();
}

/** Same instant? ("+00:00" vs "Z", fractional seconds etc. don't matter.) */
export function sameInstant(a: string | null | undefined, b: string | null | undefined): boolean {
  const am = isoToMs(a);
  const bm = isoToMs(b);
  return !isNaN(am) && !isNaN(bm) && am === bm;
}

/**
 * Fold a live layer into a snapshot. Returns a NEW snapshot, or `null` when
 * the response must be ignored:
 * - it is relative to a different pack than `packTimestamp`;
 * - it has no live data yet (`live_updated_at` null);
 * - it is not newer than what the snapshot already carries.
 *
 * Only non-null blocks are patched — a null block means "nothing newer for
 * that source", so the snapshot keeps what it has.
 */
export function applyLiveToSnapshot(
  snapshot: ForecastSnapshot,
  live: LiveLayer,
  packTimestamp: string,
): ForecastSnapshot | null {
  if (!sameInstant(live.pack_timestamp, packTimestamp)) return null;
  const liveMs = isoToMs(live.live_updated_at);
  if (isNaN(liveMs)) return null;
  const currentMs = isoToMs(snapshot.live_updated_at);
  if (!isNaN(currentMs) && liveMs < currentMs) return null;
  if (!isNaN(currentMs) && liveMs === currentMs && !addsTrails(snapshot.live_changes, live.changes)) return null;
  return {
    ...snapshot,
    ...(live.route_observations != null ? { route_observations: live.route_observations } : {}),
    ...(live.route_sigmets != null ? { route_sigmets: live.route_sigmets } : {}),
    ...(live.observed_conditions != null ? { observed_conditions: live.observed_conditions } : {}),
    ...(live.last_refresh_delta != null ? { last_refresh_delta: live.last_refresh_delta } : {}),
    live_changes: live.changes ?? snapshot.live_changes ?? null,
    live_updated_at: live.live_updated_at,
  };
}

/** The same tick, but `incoming` carries the read-time trails (#669) and
 *  `current` does not: the snapshot overlay never has them, so the first
 *  `/live` after a pack load must still apply at an equal timestamp. */
export function addsTrails(
  current: LiveChanges | null | undefined,
  incoming: LiveChanges | null | undefined,
): boolean {
  return incoming?.recently_cleared != null && current?.recently_cleared == null;
}

/** Departure as epoch ms, from an ISO `departure_time` or the legacy
 *  target_date + target_time_utc pair. */
export function departureMs(
  departure: string | { target_date: string; target_time_utc: number },
): number {
  if (typeof departure === 'string') return isoToMs(departure);
  const hh = String(departure.target_time_utc).padStart(2, '0');
  return isoToMs(`${departure.target_date}T${hh}:00:00Z`);
}

/** departure − 3h ≤ now ≤ departure + duration + 1h (inclusive both ends). */
export function isInLiveWindow(
  departure: string | { target_date: string; target_time_utc: number },
  durationHours: number,
  now: Date | number = Date.now(),
): boolean {
  const dep = departureMs(departure);
  if (isNaN(dep)) return false;
  const t = typeof now === 'number' ? now : now.getTime();
  const start = dep - LIVE_WINDOW_BEFORE_H * 3600_000;
  const end = dep + ((durationHours || 0) + LIVE_WINDOW_AFTER_H) * 3600_000;
  return t >= start && t <= end;
}

/** True once the live window has closed (polling can stop for good). */
export function isLiveWindowPast(
  departure: string | { target_date: string; target_time_utc: number },
  durationHours: number,
  now: Date | number = Date.now(),
): boolean {
  const dep = departureMs(departure);
  if (isNaN(dep)) return true;
  const t = typeof now === 'number' ? now : now.getTime();
  return t > dep + ((durationHours || 0) + LIVE_WINDOW_AFTER_H) * 3600_000;
}

/** Newest of the live-layer update and the observation fetch time, as the
 *  original ISO string; null when neither is known. */
export function observedAsOf(snapshot: ForecastSnapshot | null | undefined): string | null {
  if (!snapshot) return null;
  const candidates = [snapshot.live_updated_at, snapshot.route_observations?.fetch_time];
  let best: string | null = null;
  let bestMs = -Infinity;
  for (const c of candidates) {
    const ms = isoToMs(c);
    if (!isNaN(ms) && ms > bestMs) {
      bestMs = ms;
      best = c as string;
    }
  }
  return best;
}

/** Change kinds about an area, not an airport (never highlight a row). */
const AREA_KINDS: ReadonlySet<string> = new Set(['sigmet_issued', 'sigmet_cancelled', 'lightning', 'radar']);

/** ICAOs with any change at the airport — category, convective weather,
 *  significant weather, wind or TAF (upper-case). An airport kind the server
 *  adds later still highlights its row. */
export function changedIcaos(changes: LiveChanges | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const c of changes?.changes ?? []) {
    if (c.icao && !AREA_KINDS.has(c.kind)) {
      out.add(c.icao.toUpperCase());
    }
  }
  return out;
}

/** Keys of the SIGMET changes, comparable with {@link sigmetChangeKey}. */
export function changedSigmetKeys(changes: LiveChanges | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const c of changes?.changes ?? []) {
    // One phenomenon issued by two FIRs is one change keyed on both
    // ("sigmet:LECB|3+sigmet:LECM|3"): split it back per SIGMET.
    if (c.kind === 'sigmet_issued' || c.kind === 'sigmet_cancelled') {
      for (const k of c.key.split('+')) out.add(k);
    }
  }
  return out;
}

const SIGMET_SEQ_RE = /SIGMET\s+(\w+)/i;

/** Render an ISO datetime the way Python's `str(datetime)` does — that's what
 *  the server joins into the SIGMET fallback key: "2026-10-01 12:00:00+00:00"
 *  (space separator, seconds always, 6-digit fraction only when non-zero,
 *  "Z" spelled "+00:00"). Unrecognised input is returned unchanged. */
export function pythonDatetimeStr(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$/i.exec(iso.trim());
  if (!m) return iso;
  const [, date, hm, sec = '00', frac, zone] = m;
  let out = `${date} ${hm}:${sec}`;
  if (frac && /[1-9]/.test(frac)) out += `.${frac.slice(0, 6).padEnd(6, '0')}`;
  if (zone) {
    if (zone.toUpperCase() === 'Z') out += '+00:00';
    else out += zone.includes(':') ? zone : `${zone.slice(0, 3)}:${zone.slice(3)}`;
  }
  return out;
}

/**
 * Stable SIGMET identity, mirroring the server's `_sigmet_key_str`
 * (src/weatherbrief/tasks/live_significance.py): FIR + sequence parsed from
 * the raw text, else FIR + hazard + valid_from, joined with "|" (None → "").
 */
export function sigmetChangeKey(
  sigmet: Pick<SigmetAlongRoute, 'fir_id' | 'raw_text' | 'hazard' | 'valid_from'>,
): string {
  const seq = SIGMET_SEQ_RE.exec(sigmet.raw_text || '')?.[1];
  if (seq != null) return `sigmet:${sigmet.fir_id}|${seq}`;
  const vf = sigmet.valid_from ? pythonDatetimeStr(sigmet.valid_from) : '';
  return `sigmet:${sigmet.fir_id}|${sigmet.hazard ?? ''}|${vf}`;
}

/** "HH:MMZ" in UTC; '' when unparseable. */
export function formatHhmmZ(iso: string | null | undefined): string {
  const ms = isoToMs(iso);
  if (isNaN(ms)) return '';
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}Z`;
}

/** Whole minutes from `iso` to `now` (never negative); null when unparseable. */
export function minutesAgo(iso: string | null | undefined, now: Date | number = Date.now()): number | null {
  const ms = isoToMs(iso);
  if (isNaN(ms)) return null;
  const t = typeof now === 'number' ? now : now.getTime();
  return Math.max(0, Math.floor((t - ms) / 60_000));
}

// --- Trails (#669) -----------------------------------------------------------
//
// Display only: the server computes each change's history from the flight's
// live history (tasks/live_trail.py); these helpers turn it into the line
// under a row. Same text as iOS (LiveChangesView): keep the two in step.

/** Spans shown on one line (the latest). */
export const TRAIL_MAX_SPANS = 4;

/** The recently cleared rows, newest first. The server owns the 60-min
 *  window (it re-applies it on every `/live` read); no client re-filter, so
 *  web and iOS always show the same rows. Each says when it cleared. */
export function clearedRows(changes: LiveChanges | null | undefined): LiveChange[] {
  return changes?.recently_cleared ?? [];
}

/** "HH:MM" in UTC; '' when unparseable. */
function hhmm(iso: string | null | undefined): string {
  return formatHhmmZ(iso).replace(/Z$/, '');
}

/** "12:42–13:02Z" for a closed span, "since 13:33Z" for an open one. */
export function trailSpanText(span: { start: string; end: string | null }): string {
  if (span.end) return `${hhmm(span.start)}–${formatHhmmZ(span.end)}`;
  return t('live.trail.since', { time: formatHhmmZ(span.start) });
}

/** English ordinal ("1st", "2nd", "3rd", "11th"). */
export function englishOrdinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  return `${n}${({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}`;
}

/** "2nd time today" from 2; null below. */
export function timesTodayText(n: number | null | undefined): string | null {
  if (!n || n < 2) return null;
  return t('live.trail.nthTimeToday', { n, nth: englishOrdinal(n) });
}

/** "briefed VFR · 11:00Z MVFR · 11:30Z VFR" for a category row; null when
 *  there are no reports. */
export function categoryStripText(c: LiveChange): string | null {
  const reports = c.trail?.reports ?? [];
  if (reports.length === 0) return null;
  const parts: string[] = [];
  if (c.from_value) {
    const key = c.trail?.baseline_source === 'live_start' ? 'live.trail.atStart' : 'live.trail.briefed';
    parts.push(t(key, { cat: c.from_value }));
  }
  for (const r of reports) {
    parts.push(`${formatHhmmZ(r.at)} ${r.category ?? '?'}`);
  }
  return parts.join(' · ');
}

/**
 * The trail line under a change row, or null when it would only repeat the
 * row (first time on screen, one span, one report). A cleared row always
 * gets one: it is the only trace of what happened.
 *
 * Category rows show the airport's category per report; every other kind
 * shows the on/off periods. "Nth time today" is appended from 2.
 */
export function trailText(c: LiveChange, cleared = false): string | null {
  const trail = c.trail;
  if (!trail) return null;
  const strip = c.kind === 'metar_category' ? categoryStripText(c) : null;
  const spans = trail.spans.slice(-TRAIL_MAX_SPANS).map(trailSpanText).join(', ');
  const times = timesTodayText(trail.times_today);
  const worth = cleared
    || (trail.times_today ?? 0) >= 2
    || trail.spans.length >= 2
    || (trail.reports?.length ?? 0) >= 2;
  if (!worth) return null;
  const parts = [strip ?? (spans || null), times].filter((p): p is string => !!p);
  return parts.length > 0 ? parts.join(' · ') : null;
}
