/** Shared timezone utilities — pure browser Intl, no library needed. */

import type { WaypointInfo } from '../adapters/api-adapter';

/** Get the UTC offset in minutes for a timezone at a given reference date. */
export function getUtcOffsetMinutes(timezone: string, refDate: Date): number {
  const fmt = (tz: string) => {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(refDate);
    const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0', 10);
    return { year: get('year'), month: get('month'), day: get('day'), hour: get('hour'), minute: get('minute') };
  };
  const utc = fmt('UTC');
  const local = fmt(timezone);
  const utcMin = ((utc.year * 12 + utc.month) * 31 + utc.day) * 1440 + utc.hour * 60 + utc.minute;
  const localMin = ((local.year * 12 + local.month) * 31 + local.day) * 1440 + local.hour * 60 + local.minute;
  return localMin - utcMin;
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
      const offset = getUtcOffsetMinutes(wp.timezone, refDate);
      entries.push({
        tz: wp.timezone,
        label: `${wp.timezone.split('/').pop()!.replace(/_/g, ' ')} (${formatUtcOffset(offset)})`,
      });
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
