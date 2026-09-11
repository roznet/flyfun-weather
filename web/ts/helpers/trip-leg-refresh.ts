/**
 * Per-leg refresh on the trip page.
 *
 * v1 made refresh trip-only. That was wrong for the commonest trip-day pattern:
 * the next leg wants several refreshes (fresh METAR/TAF just before departure)
 * while the rest of the chain wants none, and a trip refresh re-digests every
 * leg with new model data. A single-leg refresh is an ordinary per-flight
 * refresh; the only trip-specific rule is that a leg owned by a live trip run is
 * refused server-side (`leg_is_claimed`, 409), so the button is off meanwhile.
 *
 * Structurally typed so this stays a pure decision with no import cycle back
 * into the store or the adapters.
 */

export interface LegRefreshButton {
  /** Rendered at all: only a leg still ahead has anything to refresh for. */
  visible: boolean;
  /** Clickable: no trip run in flight and this leg not already queued/running. */
  enabled: boolean;
}

export function legRefreshButton(
  leg: { state: string },
  tripBusy: boolean,
  entry: { status: string } | undefined,
): LegRefreshButton {
  // A flown leg's refresh is a historical re-run (admin-only on the briefing
  // page) and a cancelled leg will not be flown — neither belongs on a row
  // whose purpose is "brief the next leg again".
  if (leg.state !== 'remaining') return { visible: false, enabled: false };
  return { visible: true, enabled: !tripBusy && entry === undefined };
}

/**
 * Legs whose refresh was in flight on the previous poll and is gone now.
 *
 * This is what keeps the trip summary honest after a single-leg refresh: the
 * aggregate is computed per read, so the page only has to re-read the trip when
 * a leg settles. Keyed on disappearance rather than on a status transition
 * because the poll is 5 s and a fast refresh can go queued → gone between two
 * ticks without ever being seen as "refreshing".
 */
export function settledLegIds(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
): string[] {
  return Object.keys(before).filter(id => !(id in after));
}

/**
 * The trip-run progress line to show, or '' for none.
 *
 * The server keeps a finished run's results so its final "2 of 3 legs had new
 * data" can render — but keeps them indefinitely. Once a leg is refreshed on
 * its own, that line describes a run the legs have moved past (it called a leg
 * "already current" that has since been re-briefed). So: always while a run is
 * live, and after one only until any leg has a pack newer than its finish.
 */
export function tripRunMessage(
  refresh: { active?: boolean; message?: string | null; finished_at?: string | null } | null | undefined,
  legs: { fetch_timestamp: string | null }[],
): string {
  if (!refresh?.message) return '';
  if (refresh.active) return refresh.message;
  // No finish time means the age is unknown: say nothing rather than risk a
  // stale claim.
  const finished = refresh.finished_at ? Date.parse(refresh.finished_at) : NaN;
  if (Number.isNaN(finished)) return '';
  const legMovedOn = legs.some(
    l => l.fetch_timestamp != null && Date.parse(l.fetch_timestamp) > finished,
  );
  return legMovedOn ? '' : refresh.message;
}
