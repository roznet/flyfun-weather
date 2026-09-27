/** Shared timezone utilities — pure browser Intl, no library needed. */

import type { WaypointInfo } from '../adapters/api-adapter';

/** Wall-clock date/time parts of `instant` in `tz`. */
function wallClockParts(instant: Date, tz: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant);
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0', 10);
  return { year: get('year'), month: get('month'), day: get('day'), hour: get('hour'), minute: get('minute') };
}

/** Get the UTC offset in minutes for a timezone at a given reference date. */
export function getUtcOffsetMinutes(timezone: string, refDate: Date): number {
  const w = wallClockParts(refDate, timezone);
  const wallMs = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute);
  const utcMs = Math.floor(refDate.getTime() / 60000) * 60000;
  return Math.round((wallMs - utcMs) / 60000);
}

/** Format a UTC offset in minutes as "GMT+2" or "GMT-5:30". */
export function formatUtcOffset(offsetMinutes: number): string {
  const sign = offsetMinutes >= 0 ? '+' : '-';
  const abs = Math.abs(offsetMinutes);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return m ? `GMT${sign}${h}:${m.toString().padStart(2, '0')}` : `GMT${sign}${h}`;
}

/** Snap a minute value to the nearest available option (0, 15, 30, 45). */
export function nearestMinuteOption(m: number): number {
  const options = [0, 15, 30, 45];
  return options.reduce((best, o) => Math.abs(o - m) < Math.abs(best - m) ? o : best);
}

/** Default departure for a new flight: `now` + `leadHours`, rounded to the
 *  nearest 15 minutes (the minute picker's step). */
export function defaultDepartureInstant(now: Date = new Date(), leadHours = 2): Date {
  const step = 15 * 60 * 1000;
  return new Date(Math.round((now.getTime() + leadHours * 3600 * 1000) / step) * step);
}

/** Date (YYYY-MM-DD), hour and minute of `instant` as shown on a clock in `tz`. */
export function instantToLocalFields(instant: Date, tz: string): { date: string; hour: number; minute: number } {
  const w = wallClockParts(instant, tz);
  const pad = (n: number) => String(n).padStart(2, '0');
  return { date: `${w.year}-${pad(w.month)}-${pad(w.day)}`, hour: w.hour, minute: w.minute };
}

/** The instant at which a clock in `tz` reads `date` (YYYY-MM-DD) `hour`:`minute`. */
export function localFieldsToInstant(date: string, hour: number, minute: number, tz: string): Date {
  const [y, mo, d] = date.split('-').map(Number);
  const wallMs = Date.UTC(y, mo - 1, d, hour, minute);
  // Guess with the offset at the wall time, then re-check at the guessed instant
  // so a DST transition between the two is resolved.
  let t = wallMs - getUtcOffsetMinutes(tz, new Date(wallMs)) * 60000;
  t = wallMs - getUtcOffsetMinutes(tz, new Date(t)) * 60000;
  return new Date(t);
}

const UTC_ALIASES = new Set(['UTC', 'Etc/UTC', 'Etc/GMT', 'GMT', 'Etc/Universal', 'Etc/Zulu']);

/** The viewer's IANA timezone from the browser, or 'UTC' when it is UTC or unknown. */
export function browserTimeZone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz && !UTC_ALIASES.has(tz) ? tz : 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Dropdown label for a timezone, e.g. "Los Angeles (GMT-7)". */
export function timezoneLabel(tz: string, refDate: Date): string {
  return `${tz.split('/').pop()!.replace(/_/g, ' ')} (${formatUtcOffset(getUtcOffsetMinutes(tz, refDate))})`;
}

export interface TimezoneOption {
  tz: string;
  label: string;
}

/** Build unique timezone options from waypoints, suitable for a <select> dropdown. */
export function buildTimezoneOptions(waypoints: WaypointInfo[], refDate: Date): TimezoneOption[] {
  const seen = new Set<string>();
  const entries: TimezoneOption[] = [];
  for (const wp of waypoints) {
    if (wp.timezone && !seen.has(wp.timezone)) {
      seen.add(wp.timezone);
      entries.push({ tz: wp.timezone, label: timezoneLabel(wp.timezone, refDate) });
    }
  }
  return entries;
}

/** Convert local hour+minute in a timezone to UTC hour+minute. */
export function localToUtc(
  localHour: number, localMinute: number, tz: string, refDate: Date,
): { hour: number; minute: number } {
  if (tz === 'UTC') return { hour: localHour, minute: localMinute };
  const offsetMin = getUtcOffsetMinutes(tz, refDate);
  let totalMin = localHour * 60 + localMinute - offsetMin;
  totalMin = ((totalMin % 1440) + 1440) % 1440;
  return { hour: Math.floor(totalMin / 60), minute: totalMin % 60 };
}

/** Convert UTC hour+minute to local hour+minute in a timezone. */
export function utcToLocal(
  utcHour: number, utcMinute: number, tz: string, refDate: Date,
): { hour: number; minute: number } {
  if (tz === 'UTC') return { hour: utcHour, minute: utcMinute };
  const offsetMin = getUtcOffsetMinutes(tz, refDate);
  let totalMin = utcHour * 60 + utcMinute + offsetMin;
  totalMin = ((totalMin % 1440) + 1440) % 1440;
  return { hour: Math.floor(totalMin / 60), minute: nearestMinuteOption(totalMin % 60) };
}
