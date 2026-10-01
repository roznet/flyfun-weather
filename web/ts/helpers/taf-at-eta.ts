/** Pure reading of a route-observation airport's TAF at its ETA (#613).
 *
 * The server reads the TAF the way a pilot does (`analysis/taf_reading.py`,
 * #611) and ships structured fields; this turns them into what the table cell
 * and the ⓘ popup show. Kept structured rather than a server-built string so
 * the labels go through i18n.
 *
 * SYNC: iOS mirrors these rules in `AirportObservation.tafAtEta`
 * (app/flyfun-weather/flyfun-weather/Models/API/SnapshotResponse.swift), and
 * the server's English one-liner is `AirportObservation.taf_at_eta_line()`.
 */
import type { AirportObservation } from '../store/types';

export type TafAtEta =
  /** No TAF at all for this airport. */
  | { kind: 'noTaf' }
  /** A TAF exists but its validity does not contain the ETA, e.g. a field
   *  that issues TAFs only in opening hours. `window` is "11/15Z-11/17Z". */
  | { kind: 'notValid'; window: string | null }
  | {
      kind: 'reading';
      /** Prevailing category (BECMG/FM applied). On packs built before #610
       *  this is the single combined category. */
      prevailing: string | null;
      /** The worst TEMPO/PROB group at ETA, only when worse than prevailing. */
      temporary: { category: string; type: string } | null;
      /** TS, CB, FG… in the prevailing or temporary conditions. */
      significantWeather: string[];
      /** Pre-#610 packs: the trend label that set the combined category. */
      legacyTrend: string | null;
    };

type TafFields = Pick<
  AirportObservation,
  | 'has_taf'
  | 'taf_raw'
  | 'taf_valid_from'
  | 'taf_valid_to'
  | 'taf_valid_at_eta'
  | 'taf_flight_category_at_eta'
  | 'taf_trend_type'
  | 'taf_prevailing_category_at_eta'
  | 'taf_temporary_category_at_eta'
  | 'taf_temporary_type'
  | 'taf_significant_weather'
>;

export function readTafAtEta(apt: Partial<TafFields>): TafAtEta {
  if (!apt.has_taf && !apt.taf_raw) return { kind: 'noTaf' };

  if (apt.taf_valid_at_eta === false) {
    return { kind: 'notValid', window: tafValidityWindow(apt.taf_valid_from, apt.taf_valid_to) };
  }

  if (apt.taf_valid_at_eta == null) {
    // Packs built before #610 carry only the single-group reading.
    return {
      kind: 'reading',
      prevailing: apt.taf_flight_category_at_eta ?? null,
      temporary: null,
      significantWeather: [],
      legacyTrend: apt.taf_trend_type ?? null,
    };
  }

  const tempCategory = apt.taf_temporary_category_at_eta ?? null;
  return {
    kind: 'reading',
    // A reading with no prevailing category (rare: no visibility/ceiling in
    // the base group) still has the combined one to show.
    prevailing: apt.taf_prevailing_category_at_eta ?? apt.taf_flight_category_at_eta ?? null,
    temporary: tempCategory
      ? { category: tempCategory, type: apt.taf_temporary_type || 'TEMPO' }
      : null,
    significantWeather: apt.taf_significant_weather ?? [],
    legacyTrend: null,
  };
}

/** "11/15Z-11/17Z" (day/hour UTC), the server's `taf_at_eta_line` format.
 *  Null when either end is missing or unparseable. */
export function tafValidityWindow(
  from: string | null | undefined,
  to: string | null | undefined,
): string | null {
  const a = dayHourZ(from);
  const b = dayHourZ(to);
  return a && b ? `${a}-${b}` : null;
}

function dayHourZ(iso: string | null | undefined): string | null {
  if (!iso) return null;
  // Server datetimes are aware UTC; tolerate a naive string as UTC too.
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const ms = Date.parse(hasZone ? iso : `${iso}Z`);
  if (Number.isNaN(ms)) return null;
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getUTCDate())}/${pad(d.getUTCHours())}Z`;
}

/** Compact label for a temporary group in a table cell: "PROB30 TEMPO" →
 *  "PROB30" (the PROB is the informative part), "TEMPO" stays. */
export function shortTemporaryLabel(type: string): string {
  const prob = /^PROB\d+/.exec(type);
  return prob ? prob[0] : type;
}
