/**
 * The context-sensitive rule behind the trip actions on the selection bar
 * (#602). One question decides everything: how many *distinct* trips the
 * selection touches.
 */

import { describe, expect, it } from 'vitest';
import { buildTripSelection } from '../../ts/helpers/trip-selection';
import type { FlightResponse } from '../../ts/store/types';

function flight(id: string, trip?: { id: string; name: string }): FlightResponse {
  return {
    id,
    user_id: 'u',
    profile_id: null,
    aircraft_id: null,
    aircraft: null,
    route_name: id,
    waypoints: ['EGTF', 'LSGS'],
    departure_time: '2026-09-11T09:00:00Z',
    alt_departure_time: null,
    flexibility: 'none',
    target_date: '2026-09-11',
    target_time_utc: 9,
    cruise_altitude_ft: 8000,
    flight_ceiling_ft: 18000,
    flight_duration_hours: 1,
    private: false,
    auto_refresh: false,
    auto_refresh_hour: null,
    notify_override: 'default',
    created_at: '2026-09-01T00:00:00Z',
    role: 'owner',
    owner_display_name: null,
    is_subscribed: false,
    trip: trip ? { ...trip, position: 1, total: 2 } : null,
  };
}

describe('buildTripSelection', () => {
  it('offers "Group as trip" when nothing selected is in a trip', () => {
    const flights = [flight('a'), flight('b'), flight('c')];
    const ctx = buildTripSelection(flights, new Set(['a', 'b']));
    expect(ctx.tripCount).toBe(0);
    expect(ctx.singleTrip).toBeNull();
    expect(ctx.ungroupedIds).toEqual(['a', 'b']);
    expect(ctx.memberships).toEqual([]);
  });

  it('offers "Add to …" when exactly one trip is represented', () => {
    const flights = [
      flight('a', { id: 't1', name: 'Sion' }),
      flight('b'),
      flight('c'),
    ];
    const ctx = buildTripSelection(flights, new Set(['a', 'b']));
    expect(ctx.tripCount).toBe(1);
    expect(ctx.singleTrip).toEqual({ id: 't1', name: 'Sion' });
    // Only the ungrouped picks get added — 'a' is already in the trip.
    expect(ctx.ungroupedIds).toEqual(['b']);
  });

  it('offers neither when two different trips are represented', () => {
    // Merging two trips is a different decision that this bar has no way to
    // ask about, so it declines to guess.
    const flights = [
      flight('a', { id: 't1', name: 'Sion' }),
      flight('b', { id: 't2', name: 'Le Touquet' }),
    ];
    const ctx = buildTripSelection(flights, new Set(['a', 'b']));
    expect(ctx.tripCount).toBe(2);
    expect(ctx.singleTrip).toBeNull();
  });

  it('reports memberships so "Remove from trip" can unlink each pick', () => {
    const flights = [
      flight('a', { id: 't1', name: 'Sion' }),
      flight('b', { id: 't1', name: 'Sion' }),
      flight('c'),
    ];
    const ctx = buildTripSelection(flights, new Set(['a', 'b', 'c']));
    expect(ctx.memberships).toEqual([
      { tripId: 't1', flightId: 'a' },
      { tripId: 't1', flightId: 'b' },
    ]);
    // Still one distinct trip, so "Add to …" stays available for 'c'.
    expect(ctx.tripCount).toBe(1);
    expect(ctx.ungroupedIds).toEqual(['c']);
  });

  it('ignores flights that are not selected', () => {
    const flights = [flight('a', { id: 't1', name: 'Sion' }), flight('b')];
    const ctx = buildTripSelection(flights, new Set(['b']));
    expect(ctx.tripCount).toBe(0);
    expect(ctx.selectedIds).toEqual(['b']);
  });

  it('is empty for an empty selection', () => {
    const ctx = buildTripSelection([flight('a')], new Set());
    expect(ctx.selectedIds).toEqual([]);
    expect(ctx.tripCount).toBe(0);
  });
});
