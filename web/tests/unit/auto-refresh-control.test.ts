/** What the briefing page's auto-refresh control offers, per flight.
 *
 * The bug this pins: a leg inside a trip had the *whole* control disabled, so
 * the per-leg hour — which is what actually fires the chain, earliest leg wins
 * — could not be set from anywhere. The switch is genuinely the trip's; the
 * hour is genuinely the leg's, and the two must not be conflated again.
 */

import { describe, it, expect } from 'vitest';
import { autoRefreshControl, hourPatchValue } from '../../ts/helpers/auto-refresh-control';
import type { FlightResponse, TripLegRef } from '../../ts/store/types';

function makeFlight(overrides: Partial<FlightResponse> = {}): FlightResponse {
  return {
    id: 'egtf_lsgs-2026-02-25-45ed',
    user_id: 'dev-user-001',
    route_name: 'egtf_lsgs',
    waypoints: ['EGTF', 'LSGS'],
    departure_time: '2026-02-25T17:00:00+00:00',
    target_date: '2026-02-25',
    target_time_utc: 17,
    cruise_altitude_ft: 8000,
    flight_duration_hours: 1.0,
    auto_refresh: false,
    auto_refresh_hour: null,
    private: false,
    created_at: '2026-02-25T16:10:07.255073+00:00',
    ...overrides,
  } as FlightResponse;
}

function trip(overrides: Partial<TripLegRef> = {}): TripLegRef {
  return { id: 'trip123', name: 'Sion', position: 1, total: 3, auto_refresh: false, ...overrides };
}

describe('a flight of its own', () => {
  it('owns its switch and shows no hour while off', () => {
    const c = autoRefreshControl(makeFlight());
    expect(c.owner).toBe('flight');
    expect(c.switchEditable).toBe(true);
    expect(c.switchOn).toBe(false);
    expect(c.hourVisible).toBe(false);
    expect(c.tripId).toBeNull();
  });

  it('shows the hour once switched on', () => {
    const c = autoRefreshControl(makeFlight({ auto_refresh: true }));
    expect(c.hourVisible).toBe(true);
  });

  it('defaults the hour to departure − 1 h', () => {
    expect(autoRefreshControl(makeFlight()).effectiveHour).toBe(16);
  });

  it('wraps the default hour past midnight', () => {
    const c = autoRefreshControl(makeFlight({ target_time_utc: 0 }));
    expect(c.defaultHour).toBe(23);
    expect(c.effectiveHour).toBe(23);
  });

  it('prefers an explicit hour over the default', () => {
    expect(autoRefreshControl(makeFlight({ auto_refresh_hour: 7 })).effectiveHour).toBe(7);
  });

  it('keeps hour 0 rather than falling back to the default', () => {
    // `?? ` not `|| ` — 00:00Z is a real choice, and midnight is falsy.
    expect(autoRefreshControl(makeFlight({ auto_refresh_hour: 0 })).effectiveHour).toBe(0);
  });
});

describe('a leg inside a trip', () => {
  it('hands the switch to the trip and shows the trip state, not its own', () => {
    const c = autoRefreshControl(makeFlight({
      auto_refresh: false, trip: trip({ auto_refresh: true }),
    }));
    expect(c.owner).toBe('trip');
    expect(c.switchEditable).toBe(false);
    expect(c.switchOn).toBe(true);
    expect(c.tripId).toBe('trip123');
  });

  it('still offers the hour — that is what fires the chain', () => {
    const c = autoRefreshControl(makeFlight({ trip: trip({ auto_refresh: true }) }));
    expect(c.hourVisible).toBe(true);
    expect(c.effectiveHour).toBe(16);
  });

  it('offers the leg hour even when the leg has its own switch off', () => {
    // The regression: a member leg is refreshed on the trip's flag, so its own
    // one being off must not take the hour away.
    const c = autoRefreshControl(makeFlight({
      auto_refresh: false, trip: trip({ auto_refresh: true }),
    }));
    expect(c.hourVisible).toBe(true);
  });

  it('hides the hour when the trip is off, whatever the leg says', () => {
    const c = autoRefreshControl(makeFlight({
      auto_refresh: true, trip: trip({ auto_refresh: false }),
    }));
    expect(c.switchOn).toBe(false);
    expect(c.hourVisible).toBe(false);
  });
});

describe('hourPatchValue', () => {
  it('sends null for the default so the leg keeps following departure − 1 h', () => {
    expect(hourPatchValue(16, 16)).toBeNull();
  });

  it('sends the hour when it differs from the default', () => {
    expect(hourPatchValue(7, 16)).toBe(7);
  });
});
