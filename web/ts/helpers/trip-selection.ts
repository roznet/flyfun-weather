/** Selection-bar context for the trip actions (#602).
 *
 * Pure — no DOM, no leaflet — so it can be unit-tested directly and reused by
 * anything else that needs the same rule.
 */

import type { FlightResponse } from '../store/types';

export interface TripSelectionContext {
  selectedIds: string[];
  /** Picks not currently in any trip — what "Add to …" would move. */
  ungroupedIds: string[];
  memberships: { tripId: string; flightId: string }[];
  /** Set only when the selection touches exactly one trip. */
  singleTrip: { id: string; name: string } | null;
  /** How many distinct trips the selection touches. */
  tripCount: number;
}

/** What the selection bar needs to know about trips, derived from the picks.
 *
 * The bar is context-sensitive on exactly one question — how many *distinct*
 * trips the selection touches:
 *
 * - none → **Group as trip** (creating a new one is the only sensible action);
 * - exactly one → **Add to "…"** (the destination is unambiguous, so ask for
 *   no further input);
 * - more than one → neither, because merging two trips is a different decision
 *   that this bar has no way to ask about.
 *
 * **Remove from trip** appears whenever any selected flight is in one. It is an
 * unlink and is labelled as one — it must never read as a delete. */
export function buildTripSelection(
  flights: FlightResponse[], selectedIds: Set<string>,
): TripSelectionContext {
  const picked = flights.filter(f => selectedIds.has(f.id));
  const inTrips = picked.filter(f => f.trip);
  const distinct = new Map<string, string>();
  for (const f of inTrips) distinct.set(f.trip!.id, f.trip!.name);
  return {
    selectedIds: picked.map(f => f.id),
    ungroupedIds: picked.filter(f => !f.trip).map(f => f.id),
    memberships: inTrips.map(f => ({ tripId: f.trip!.id, flightId: f.id })),
    singleTrip: distinct.size === 1
      ? { id: [...distinct.keys()][0], name: [...distinct.values()][0] }
      : null,
    tripCount: distinct.size,
  };
}
