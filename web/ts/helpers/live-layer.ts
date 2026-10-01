/** Live observation layer (#637) — pure helpers, no DOM, unit-tested.
 *
 * The server keeps one live overlay per flight, relative to its latest pack:
 * newest METAR/TAF, SIGMETs, observed conditions and the "since this
 * briefing" changes. The snapshot endpoint already folds it in when the pack
 * is the latest; `GET /flights/{id}/live` lets the page pick up newer data
 * without reloading the whole pack. These helpers decide whether a live
 * response applies and fold it into a snapshot.
 */

import type {
  ForecastSnapshot,
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
  if (!isNaN(currentMs) && liveMs <= currentMs) return null;
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

/** ICAOs with a METAR/TAF category change (upper-case). */
export function changedIcaos(changes: LiveChanges | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const c of changes?.changes ?? []) {
    if ((c.kind === 'metar_category' || c.kind === 'taf_category') && c.icao) {
      out.add(c.icao.toUpperCase());
    }
  }
  return out;
}

/** Keys of the SIGMET changes, comparable with {@link sigmetChangeKey}. */
export function changedSigmetKeys(changes: LiveChanges | null | undefined): Set<string> {
  const out = new Set<string>();
  for (const c of changes?.changes ?? []) {
    if (c.kind === 'sigmet_issued' || c.kind === 'sigmet_cancelled') out.add(c.key);
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
