/** What the briefing page's auto-refresh control should show for a flight.
 *
 * Extracted from the renderer because the interesting part is a decision, not
 * markup — and because getting it wrong is invisible until a pilot cannot set
 * something. A member leg splits the control in two: *whether* to refresh is
 * the trip's (it refreshes the whole chain or none of it), while the *hour*
 * stays the leg's, because whichever leg comes due first is what fires the
 * chain — so the earliest leg's hour is the trip's effective refresh time.
 */

import type { FlightResponse } from '../store/types';

export interface AutoRefreshControl {
  /** `trip` when the on/off switch belongs to the trip, not this flight. */
  owner: 'flight' | 'trip';
  /** Checked state of the switch — the trip's flag for a member leg. */
  switchOn: boolean;
  /** False for a member leg: the switch lives on the trip page. */
  switchEditable: boolean;
  /** The hour select is shown (and always editable when shown). */
  hourVisible: boolean;
  /** Hour the select should land on, 0-23 UTC. */
  effectiveHour: number;
  /** Departure − 1 h: what a null `auto_refresh_hour` resolves to. */
  defaultHour: number;
  /** Trip to link to, when the switch is the trip's. */
  tripId: string | null;
}

export function autoRefreshControl(flight: FlightResponse): AutoRefreshControl {
  const defaultHour = ((flight.target_time_utc - 1) + 24) % 24;
  const effectiveHour = flight.auto_refresh_hour ?? defaultHour;
  const trip = flight.trip ?? null;

  if (trip) {
    return {
      owner: 'trip',
      switchOn: trip.auto_refresh,
      switchEditable: false,
      // No point offering an hour for a chain that never refreshes.
      hourVisible: trip.auto_refresh,
      effectiveHour,
      defaultHour,
      tripId: trip.id,
    };
  }

  return {
    owner: 'flight',
    switchOn: flight.auto_refresh,
    switchEditable: true,
    hourVisible: flight.auto_refresh,
    effectiveHour,
    defaultHour,
    tripId: null,
  };
}

/** The value to PATCH as `auto_refresh_hour`: null means "follow the default". */
export function hourPatchValue(selected: number, defaultHour: number): number | null {
  return selected === defaultHour ? null : selected;
}
