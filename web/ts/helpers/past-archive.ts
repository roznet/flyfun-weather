/** Past-flights finder chips (#1032) and their mapping to the archive query.
 *
 *  The server (`GET /api/flights/archive`, #1031) filters, counts and pages:
 *  this module only names the chips (same order and labels on web, iOS and
 *  Android) and turns the chip state into query parameters. Nothing here
 *  computes a tag, a count or a match.
 */

// SYNC: HAZARD_CHIPS / OUTCOME_CHIPS (order, keys, labels) are hand-copied to
// iOS `PastChips` (Models/API/PastArchiveResponse.swift) and Android `PastChips`
// (model/PastArchiveModels.kt). Change all three together.

/** Hazard chips, in display order. `tag` is the archive tag the server takes. */
export const HAZARD_CHIPS: ReadonlyArray<{ tag: string; labelKey: string }> = [
  { tag: 'ICE', labelKey: 'past.chipIcing' },
  { tag: 'TS', labelKey: 'past.chipConvection' },
  { tag: 'IMC', labelKey: 'past.chipLowCloud' },
  { tag: 'WIND', labelKey: 'past.chipWind' },
  { tag: 'TURB', labelKey: 'past.chipTurbulence' },
  { tag: 'FRZ', labelKey: 'past.chipFreezingPrecip' },
  { tag: 'VIS', labelKey: 'past.chipVisibility' },
];

/** Outcome chips, in display order. */
export const OUTCOME_CHIPS: ReadonlyArray<{ key: string; labelKey: string }> = [
  { key: 'flown', labelKey: 'past.chipFlown' },
  { key: 'cancelled', labelKey: 'past.chipCancelled' },
  { key: 'monitoring', labelKey: 'past.chipMonitoring' },
  { key: 'debriefed', labelKey: 'past.chipDebriefed' },
  { key: 'differs', labelKey: 'past.chipDiffers' },
];

/** What the one-tap "hide cancelled and monitor only" chip hides. */
export const HIDE_INACTIVE = ['cancelled', 'monitoring'] as const;

export type ArchiveSource = 'forecast' | 'reported';

export interface PastFilterState {
  q: string;
  tags: string[];
  source: ArchiveSource;
  outcomes: string[];
  hideInactive: boolean;
}

export const EMPTY_PAST_FILTER: PastFilterState = {
  q: '', tags: [], source: 'forecast', outcomes: [], hideInactive: false,
};

/** True when anything narrows the past list (search, chip or hide). */
export function pastFilterActive(s: PastFilterState): boolean {
  return s.q.trim() !== '' || s.tags.length > 0 || s.outcomes.length > 0 || s.hideInactive;
}

/** Add or remove one value, keeping the chip order stable. */
export function toggleValue(list: readonly string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** The archive query for one list page. */
export function archiveParams(
  s: PastFilterState,
  page: { limit: number; offset: number },
): URLSearchParams {
  const p = new URLSearchParams();
  const q = s.q.trim();
  if (q) p.set('q', q);
  if (s.tags.length) p.set('tags', s.tags.join(','));
  if (s.source !== 'forecast') p.set('source', s.source);
  if (s.outcomes.length) p.set('outcome', s.outcomes.join(','));
  if (s.hideInactive) p.set('hide', HIDE_INACTIVE.join(','));
  p.set('limit', String(page.limit));
  if (page.offset > 0) p.set('offset', String(page.offset));
  return p;
}
