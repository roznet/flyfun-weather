/** Tests for the stale-pack banner decision logic.
 *
 * The banner fires on briefing.html when the loaded pack's altitude or
 * departure time no longer matches the live flight — i.e. the pilot
 * edited the flight after the pack was generated. Owner-only.
 */

import { describe, it, expect } from 'vitest';
import { computeStalePackBanner } from '../../ts/helpers/stale-pack-banner';
import type { FlightResponse, RouteAnalysesManifest } from '../../ts/store/types';

function makeFlight(overrides: Partial<FlightResponse> = {}): FlightResponse {
  return {
    id: 'egtf_eglf-2026-02-25-45ed',
    user_id: 'dev-user-001',
    profile_id: null,
    route_name: 'egtf_eglf',
    waypoints: ['EGTF', 'EGLF'],
    raw_route: null,
    parser_version: null,
    departure_time: '2026-02-25T17:00:00+00:00',
    alt_departure_time: null,
    target_date: '2026-02-25',
    target_time_utc: 17,
    cruise_altitude_ft: 8000,
    flight_ceiling_ft: 18000,
    flight_duration_hours: 1.0,
    auto_refresh: false,
    auto_refresh_hour: null,
    private: false,
    aircraft_id: null,
    created_at: '2026-02-25T16:10:07.255073+00:00',
    ...overrides,
  } as FlightResponse;
}

function makeManifest(overrides: Partial<RouteAnalysesManifest> = {}): RouteAnalysesManifest {
  return {
    route_name: 'egtf_eglf',
    target_date: '2026-02-25',
    departure_time: '2026-02-25T17:00:00+00:00',
    flight_duration_hours: 1.0,
    total_distance_nm: 9.2,
    cruise_altitude_ft: 8000,
    models: ['gfs'],
    analyses: [],
    ...overrides,
  } as RouteAnalysesManifest;
}

describe('computeStalePackBanner', () => {
  it('returns null when flight and manifest match', () => {
    const state = computeStalePackBanner(makeFlight(), makeManifest(), true);
    expect(state).toBeNull();
  });

  it('flags altitude change', () => {
    const flight = makeFlight({ cruise_altitude_ft: 12000 });
    const state = computeStalePackBanner(flight, makeManifest(), true);
    expect(state).not.toBeNull();
    expect(state!.altChanged).toBe(true);
    expect(state!.timeChanged).toBe(false);
  });

  it('flags time change', () => {
    const flight = makeFlight({ departure_time: '2026-02-25T19:00:00+00:00' });
    const state = computeStalePackBanner(flight, makeManifest(), true);
    expect(state).not.toBeNull();
    expect(state!.altChanged).toBe(false);
    expect(state!.timeChanged).toBe(true);
  });

  it('flags both when both differ', () => {
    const flight = makeFlight({
      cruise_altitude_ft: 6000,
      departure_time: '2026-02-25T15:00:00+00:00',
    });
    const state = computeStalePackBanner(flight, makeManifest(), true);
    expect(state).not.toBeNull();
    expect(state!.altChanged).toBe(true);
    expect(state!.timeChanged).toBe(true);
  });

  it('treats equivalent ISO timestamps as unchanged', () => {
    // Same instant expressed two ways: pack stored naive, flight stored
    // with explicit offset. Both normalize to the same UTC ISO string.
    const flight = makeFlight({ departure_time: '2026-02-25T17:00:00.000Z' });
    const manifest = makeManifest({ departure_time: '2026-02-25T17:00:00+00:00' });
    const state = computeStalePackBanner(flight, manifest, true);
    expect(state).toBeNull();
  });

  it('returns null for non-owners even when flight differs', () => {
    // Subscribers viewing someone else's briefing can't refresh, so the
    // banner would be a dead-end UI.
    const flight = makeFlight({ cruise_altitude_ft: 14000 });
    const state = computeStalePackBanner(flight, makeManifest(), false);
    expect(state).toBeNull();
  });

  it('returns null when flight or manifest is missing', () => {
    expect(computeStalePackBanner(null, makeManifest(), true)).toBeNull();
    expect(computeStalePackBanner(makeFlight(), null, true)).toBeNull();
  });
});
