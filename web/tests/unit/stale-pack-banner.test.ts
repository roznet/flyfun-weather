/** Tests for the stale-pack banner decision logic.
 *
 * The banner fires on briefing.html when the loaded pack's altitude or
 * departure time no longer matches the live flight — i.e. the pilot
 * edited the flight after the pack was generated. Owner-only.
 */

import { describe, it, expect, vi } from 'vitest';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  getDateLocale: () => 'en-GB',
  t: (key: string, params?: Record<string, string | number>) => {
    let msg = (en as Record<string, string>)[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) msg = msg.split(`{${k}}`).join(String(v));
    return msg;
  },
}));

import { computeStalePackBanner, toSegments } from '../../ts/helpers/stale-pack-banner';
import { formatAlt } from '../../ts/utils';
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
    expect(state!.altOnly).toBe(true);
  });

  it('altOnly is false when both altitude and time changed', () => {
    const flight = makeFlight({
      cruise_altitude_ft: 6000,
      departure_time: '2026-02-25T15:00:00+00:00',
    });
    const state = computeStalePackBanner(flight, makeManifest(), true);
    expect(state!.altOnly).toBe(false);
  });

  it('suppresses the alt-only banner once advisories are reanchored', () => {
    // Flight altitude edited to 12000; route_analyses still at 8000 (stale),
    // but the advisories have already been recomputed at 12000 → no banner.
    const flight = makeFlight({ cruise_altitude_ft: 12000 });
    const state = computeStalePackBanner(flight, makeManifest(), true, 12000);
    expect(state).toBeNull();
  });

  it('still flags when advisories altitude does not match the new flight altitude', () => {
    const flight = makeFlight({ cruise_altitude_ft: 12000 });
    // advisories still at the stale 8000 → banner persists.
    const state = computeStalePackBanner(flight, makeManifest(), true, 8000);
    expect(state).not.toBeNull();
    expect(state!.altOnly).toBe(true);
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

  it('returns null for non-owners when the Synopsis matches', () => {
    // Subscribers viewing someone else's briefing can't refresh or re-grade,
    // so action buttons would be a dead end.
    const flight = makeFlight({ cruise_altitude_ft: 14000 });
    expect(computeStalePackBanner(flight, makeManifest(), false)).toBeNull();
    expect(computeStalePackBanner(flight, makeManifest(), false, null, 14000)).toBeNull();
  });

  it('tells non-owners the Synopsis describes another altitude, with no buttons', () => {
    const flight = makeFlight({ cruise_altitude_ft: 14000 });
    const state = computeStalePackBanner(flight, makeManifest(), false, null, 8000)!;
    expect(state.kind).toBe('synopsisInfo');
    expect(state.showUpdate).toBe(false);
    expect(state.showRefresh).toBe(false);
    expect(state.message).toContain('Synopsis');
  });

  it('returns null when flight or manifest is missing', () => {
    expect(computeStalePackBanner(null, makeManifest(), true)).toBeNull();
    expect(computeStalePackBanner(makeFlight(), null, true)).toBeNull();
  });

  // #987: one banner instead of two that read as contradicting each other.
  describe('altitude edit (one banner, #987)', () => {
    const flight = makeFlight({ cruise_altitude_ft: 10000 });

    it('offers both actions and says what each covers', () => {
      const state = computeStalePackBanner(flight, makeManifest(), true, 8000, 8000)!;
      expect(state.kind).toBe('altitude');
      expect(state.showUpdate).toBe(true);
      expect(state.showRefresh).toBe(true);
      expect(state.message).toContain(`changed from ${formatAlt(8000)} to ${formatAlt(10000)}`);
      expect(state.message).toContain('Route Advisories');
      expect(state.message).toContain('Synopsis');
      // The old wording that contradicted the digest banner is gone.
      expect(state.message).not.toMatch(/no full refresh needed/i);
    });

    it('links the section names', () => {
      const state = computeStalePackBanner(flight, makeManifest(), true, 8000, 8000)!;
      const links = state.segments.filter((s) => typeof s !== 'string');
      expect(links).toEqual([
        { section: 'advisories', text: 'Route Advisories' },
        { section: 'synopsis', text: 'Synopsis' },
      ]);
    });

    it('shrinks to the Synopsis line once the advisories are re-graded', () => {
      const state = computeStalePackBanner(flight, makeManifest(), true, 10000, 8000)!;
      expect(state.kind).toBe('synopsis');
      expect(state.showUpdate).toBe(false);
      expect(state.showRefresh).toBe(true);
      expect(state.message).toContain(`Route Advisories are graded for ${formatAlt(10000)}`);
      expect(state.message).toContain(`still describe ${formatAlt(8000)}`);
    });

    it('drops the Refresh offer when the Synopsis already matches', () => {
      const state = computeStalePackBanner(flight, makeManifest(), true, 8000, 10000)!;
      expect(state.kind).toBe('altitude');
      expect(state.showRefresh).toBe(false);
      expect(state.message).not.toContain('Synopsis');
    });

    it('a time change needs only Refresh, even with the Synopsis behind', () => {
      const moved = makeFlight({ cruise_altitude_ft: 10000, departure_time: '2026-02-25T19:00:00+00:00' });
      const state = computeStalePackBanner(moved, makeManifest(), true, 8000, 8000)!;
      expect(state.kind).toBe('refresh');
      expect(state.showUpdate).toBe(false);
      expect(state.showRefresh).toBe(true);
    });
  });
});

describe('toSegments', () => {
  it('splits link tokens and keeps the text around them', () => {
    expect(toSegments('A {advisories} B {synopsis}.')).toEqual([
      'A ', { section: 'advisories', text: 'Route Advisories' }, ' B ', { section: 'synopsis', text: 'Synopsis' }, '.',
    ]);
  });

  it('leaves text with no tokens alone', () => {
    expect(toSegments('plain')).toEqual(['plain']);
  });
});
