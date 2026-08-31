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
