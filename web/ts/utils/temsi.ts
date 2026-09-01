/** Pure helpers for the Météo-France TEMSI chart picker.
 *
 * Split out of `briefing-ui.ts` so they can be unit-tested: that module pulls
 * in Leaflet, which needs a DOM the node test environment doesn't provide.
 */

export const TEMSI_ZONE_LABEL: Record<string, string> = {
  france: 'France',
  euroc: 'Europe',
};

export const TEMSI_ZONE_DETAIL: Record<string, string> = {
  france: 'TEMSI France — SFC to FL150',
  euroc: 'TEMSI EUROC — FL100 to FL450',
};

/**
 * Label one picker entry: `"2026-08-31T15Z"` → `"France 15Z"`.
 *
 * TEMSI tabs name an absolute validity, not a forecast offset like the DWD
 * and Met Office tabs, because AEROWEB publishes no run/offset split.
 *
 * `showDay` adds the day-of-month. Only worth its width when the offered
 * validities straddle midnight UTC — for an 02Z departure the picker holds
 * both 23Z and 02Z, and bare hours there read in the wrong order.
 */
export function temsiTabLabel(zone: string, runCycle: string, showDay: boolean): string {
  const zoneLabel = TEMSI_ZONE_LABEL[zone] || zone;
  const m = runCycle.match(/^\d{4}-\d{2}-(\d{2})T(\d{2})Z$/);
  if (!m) return `${zoneLabel} ${runCycle}`;
  return showDay ? `${zoneLabel} ${m[1]}/${m[2]}Z` : `${zoneLabel} ${m[2]}Z`;
}

/** True when the offered validities span more than one UTC date. */
export function temsiNeedsDay(runCycles: readonly string[]): boolean {
  return new Set(runCycles.map((rc) => rc.slice(0, 10))).size > 1;
}

/**
 * How a chart's validity sits relative to departure, e.g. `"2 h before departure"`.
 *
 * The picker offers anything within 6 h of the ETD, which is wider than TEMSI's
 * ~3 h publishing horizon — so most of the time the chart on screen is valid
 * some hours *before* the flight. Saying so is the price of showing it at all:
 * an unlabelled chart invites reading it as the conditions at departure.
 *
 * Returns `null` at half an hour or less. That bound is inclusive on purpose:
 * validities land on whole hours and departures are commonly at :30, so an
 * exactly-30-minute gap is the single most frequent case, and "0.5 h before
 * departure" is noise against a chart that only changes every three hours.
 */
export function temsiValidityOffset(validMs: number, departureMs: number): string | null {
  const diffMin = Math.round((validMs - departureMs) / 60000);
  if (Math.abs(diffMin) <= 30) return null;
  const hours = Math.abs(diffMin) / 60;
  // Validities land on whole hours and departures usually don't, so a bare
  // integer would round 2h35 down to "2 h" — half-hour steps stay honest.
  const rounded = Math.round(hours * 2) / 2;
  const amount = Number.isInteger(rounded) ? `${rounded}` : rounded.toFixed(1);
  return `${amount} h ${diffMin < 0 ? 'before' : 'after'} departure`;
}
