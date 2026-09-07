/**
 * Which refresh, if any, a trip card's header should report (#602).
 *
 * The rule this encodes, and the bug it exists to prevent: a leg can be
 * refreshed *outside* a trip run — from the briefing page, a Siri intent, the
 * scheduler, MCP. None of those open a trip run, so a header keyed on
 * `trip.refresh` alone reported nothing while a member leg was busy. The member
 * cards do carry their own badge, but a collapsed card (the default) hides them
 * behind `.trip-card.collapsed .trip-legs`, so the state was invisible.
 *
 * How you *trigger* a refresh and how you *report* one are different questions.
 * The single "Refresh trip" button stands; the reporting has to be wider.
 *
 * Structurally typed so this stays a pure decision with no import cycle back
 * into the store or the adapters.
 */

export interface TripCardRefreshTrip {
  refresh?: { active?: boolean; message?: string | null } | null;
}

export type TripCardRefresh =
  | { kind: 'trip'; message: string | null }
  | { kind: 'leg'; status: string }
  | null;

export function tripCardRefresh(
  trip: TripCardRefreshTrip,
  memberIds: string[],
  activeRefreshes: Record<string, { status: string } | undefined>,
): TripCardRefresh {
  // A live trip run wins: its message ("leg 2 of 3") is strictly richer than
  // anything derivable from one leg's entry.
  if (trip.refresh?.active) {
    return { kind: 'trip', message: trip.refresh.message ?? null };
  }
  const entries = memberIds
    .map(id => activeRefreshes[id])
    .filter((e): e is { status: string } => e !== undefined);
  // "refreshing" outranks "queued" — report the most advanced leg, so a card
  // with one of each does not look merely queued.
  const entry = entries.find(e => e.status === 'refreshing') ?? entries[0];
  return entry ? { kind: 'leg', status: entry.status } : null;
}
