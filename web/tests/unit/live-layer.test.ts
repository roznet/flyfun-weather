/** Live observation layer helpers (#637). */

import { describe, it, expect } from 'vitest';
import {
  applyLiveToSnapshot,
  changedIcaos,
  changedSigmetKeys,
  formatHhmmZ,
  isInLiveWindow,
  isLiveWindowPast,
  minutesAgo,
  observedAsOf,
  pythonDatetimeStr,
  sameInstant,
  sigmetChangeKey,
} from '../../ts/helpers/live-layer';
import type {
  ForecastSnapshot,
  LiveChange,
  LiveChanges,
  LiveLayer,
  RouteObservations,
  RouteSigmets,
} from '../../ts/store/types';

function obs(fetch_time: string, icao = 'ZZAA'): RouteObservations {
  return {
    corridor_nm: 25, fetch_time, airports_found: 1, airports_with_metar: 1, airports_with_taf: 0,
    airports: [{ icao } as never], comparisons: [], worst_metar_category: 'VFR', worst_taf_category: null,
    has_conflicts: false, phenomena_along_route: [],
  };
}

function sigs(fetch_time: string): RouteSigmets {
  return {
    corridor_nm: 50, fetch_time, altitude_low_ft: null, altitude_high_ft: null, time_window_from: null,
    time_window_to: null, route_firs: ['ZZZZ'], sigmets: [], hazards: [], has_severe: false, count: 0,
  };
}

function change(over: Partial<LiveChange>): LiveChange {
  return {
    key: 'metar:ZZAA', kind: 'metar_category', source: 'METAR', direction: 'worse', tier: 'alert',
    role: 'departure', icao: 'ZZAA', station_id: null, from_value: 'VFR', to_value: 'IFR',
    observed_at: '2026-10-01T10:50:00Z', enroute_distance_nm: 0, message: 'ZZAA METAR: VFR → IFR',
    ...over,
  };
}

function changes(list: LiveChange[]): LiveChanges {
  return {
    baseline_at: '2026-10-01T09:00:00+00:00', computed_at: '2026-10-01T11:00:00Z', changes: list,
    worsened_count: list.filter(c => c.direction === 'worse').length,
    improved_count: list.filter(c => c.direction === 'better').length,
    alert_count: list.filter(c => c.tier === 'alert').length,
  };
}

function snapshot(over: Partial<ForecastSnapshot> = {}): ForecastSnapshot {
  return {
    route: { name: 'ZZAA-ZZBB', waypoints: [], cruise_altitude_ft: 6000 },
    target_date: '2026-10-01', fetch_date: '2026-10-01', days_out: 0, analyses: [],
    route_observations: obs('2026-10-01T09:00:00Z'),
    route_sigmets: sigs('2026-10-01T09:00:00Z'),
    observed_conditions: null,
    last_refresh_delta: null,
    ...over,
  };
}

function live(over: Partial<LiveLayer> = {}): LiveLayer {
  return {
    flight_id: 'f1',
    pack_timestamp: '2026-10-01T09:00:00+00:00',
    live_updated_at: '2026-10-01T11:00:00+00:00',
    route_observations: obs('2026-10-01T11:00:00Z', 'ZZCC'),
    observations_updated_at: '2026-10-01T11:00:00Z',
    route_sigmets: null,
    sigmets_updated_at: null,
    observed_conditions: null,
    observed_updated_at: null,
    changes: changes([change({})]),
    last_refresh_delta: { worsened: true, messages: ['ZZAA METAR: VFR → IFR'], computed_at: '2026-10-01T11:00:00Z' },
    ...over,
  };
}

describe('applyLiveToSnapshot', () => {
  it('applies when the pack timestamp is the same instant ("+00:00" vs "Z")', () => {
    const snap = snapshot();
    const out = applyLiveToSnapshot(snap, live(), '2026-10-01T09:00:00Z');
    expect(out).not.toBeNull();
    expect(out).not.toBe(snap);
    expect(out!.route_observations!.airports[0].icao).toBe('ZZCC');
    expect(out!.live_updated_at).toBe('2026-10-01T11:00:00+00:00');
    expect(out!.live_changes!.changes).toHaveLength(1);
    expect(out!.last_refresh_delta!.worsened).toBe(true);
    // Input untouched.
    expect(snap.route_observations!.airports[0].icao).toBe('ZZAA');
  });

  it('also matches a naive pack timestamp (taken as UTC)', () => {
    expect(applyLiveToSnapshot(snapshot(), live(), '2026-10-01T09:00:00')).not.toBeNull();
  });

  it('keeps blocks the live layer leaves null', () => {
    const snap = snapshot();
    const out = applyLiveToSnapshot(snap, live({ route_observations: null, last_refresh_delta: null }), '2026-10-01T09:00:00Z')!;
    expect(out.route_observations).toBe(snap.route_observations);
    expect(out.route_sigmets).toBe(snap.route_sigmets);
    expect(out.observed_conditions).toBeNull();
    expect(out.last_refresh_delta).toBeNull();
  });

  it('ignores a layer for a different pack', () => {
    expect(applyLiveToSnapshot(snapshot(), live(), '2026-10-01T06:00:00Z')).toBeNull();
  });

  it('ignores a layer with no live data yet', () => {
    expect(applyLiveToSnapshot(snapshot(), live({ live_updated_at: null }), '2026-10-01T09:00:00Z')).toBeNull();
  });

  it('ignores a layer that is not newer than the applied one (format-insensitive)', () => {
    const snap = snapshot({ live_updated_at: '2026-10-01T11:00:00Z' });
    expect(applyLiveToSnapshot(snap, live(), '2026-10-01T09:00:00Z')).toBeNull();
    const older = live({ live_updated_at: '2026-10-01T10:30:00+00:00' });
    expect(applyLiveToSnapshot(snap, older, '2026-10-01T09:00:00Z')).toBeNull();
    const newer = live({ live_updated_at: '2026-10-01T11:10:00.250000+00:00' });
    expect(applyLiveToSnapshot(snap, newer, '2026-10-01T09:00:00Z')).not.toBeNull();
  });
});

describe('isInLiveWindow', () => {
  const dep = '2026-10-01T12:00:00Z';
  const at = (iso: string) => new Date(iso);

  it('opens 3h before departure (inclusive)', () => {
    expect(isInLiveWindow(dep, 2, at('2026-10-01T08:59:59Z'))).toBe(false);
    expect(isInLiveWindow(dep, 2, at('2026-10-01T09:00:00Z'))).toBe(true);
  });

  it('closes 1h after arrival (inclusive)', () => {
    expect(isInLiveWindow(dep, 2, at('2026-10-01T15:00:00Z'))).toBe(true);
    expect(isInLiveWindow(dep, 2, at('2026-10-01T15:00:01Z'))).toBe(false);
    expect(isLiveWindowPast(dep, 2, at('2026-10-01T15:00:00Z'))).toBe(false);
    expect(isLiveWindowPast(dep, 2, at('2026-10-01T15:00:01Z'))).toBe(true);
  });

  it('accepts legacy target_date + target_time_utc', () => {
    const legacy = { target_date: '2026-10-01', target_time_utc: 9 };
    expect(isInLiveWindow(legacy, 1.5, at('2026-10-01T06:00:00Z'))).toBe(true);
    expect(isInLiveWindow(legacy, 1.5, at('2026-10-01T11:30:00Z'))).toBe(true);
    expect(isInLiveWindow(legacy, 1.5, at('2026-10-01T11:31:00Z'))).toBe(false);
  });

  it('is false for an unparseable departure', () => {
    expect(isInLiveWindow('nope', 1, Date.now())).toBe(false);
  });
});

describe('observedAsOf', () => {
  it('picks the newest of live_updated_at and the observation fetch time', () => {
    expect(observedAsOf(snapshot())).toBe('2026-10-01T09:00:00Z');
    expect(observedAsOf(snapshot({ live_updated_at: '2026-10-01T10:00:00+00:00' }))).toBe('2026-10-01T10:00:00+00:00');
    expect(observedAsOf(snapshot({
      live_updated_at: '2026-10-01T08:00:00Z',
      route_observations: obs('2026-10-01T09:30:00+00:00'),
    }))).toBe('2026-10-01T09:30:00+00:00');
  });

  it('is null with nothing to go on', () => {
    expect(observedAsOf(null)).toBeNull();
    expect(observedAsOf(snapshot({ route_observations: null }))).toBeNull();
  });
});

describe('change matching', () => {
  it('changedIcaos collects METAR/TAF changes only', () => {
    const set = changedIcaos(changes([
      change({}),
      change({ key: 'taf:ZZBB', kind: 'taf_category', source: 'TAF', icao: 'zzbb' }),
      change({ key: 'sigmet:ZZZZ|3', kind: 'sigmet_issued', source: 'SIGMET', icao: null }),
      change({ key: 'lightning:route', kind: 'lightning', source: 'LIGHTNING', icao: 'ZZDD' }),
    ]));
    expect([...set].sort()).toEqual(['ZZAA', 'ZZBB']);
    expect(changedIcaos(null).size).toBe(0);
  });

  it('changedSigmetKeys collects SIGMET keys', () => {
    const set = changedSigmetKeys(changes([
      change({}),
      change({ key: 'sigmet:ZZZZ|3', kind: 'sigmet_issued', source: 'SIGMET', icao: null }),
      change({ key: 'sigmet:ZZYY|TS|', kind: 'sigmet_cancelled', source: 'SIGMET', icao: null, direction: 'better' }),
    ]));
    expect([...set].sort()).toEqual(['sigmet:ZZYY|TS|', 'sigmet:ZZZZ|3']);
  });
});

describe('sigmetChangeKey (parity with the server _sigmet_key_str)', () => {
  it('uses FIR + sequence from the raw text', () => {
    expect(sigmetChangeKey({
      fir_id: 'ZZZZ', raw_text: 'ZZZZ SIGMET 13 VALID 011000/011400 ZZAA- SEV TURB', hazard: 'TURB', valid_from: null,
    })).toBe('sigmet:ZZZZ|13');
    expect(sigmetChangeKey({
      fir_id: 'ZZZZ', raw_text: 'zzzz sigmet a2 valid', hazard: null, valid_from: null,
    })).toBe('sigmet:ZZZZ|a2');
  });

  it('falls back to FIR | hazard | Python str(valid_from), None as ""', () => {
    expect(sigmetChangeKey({ fir_id: 'ZZZZ', raw_text: '', hazard: 'TS', valid_from: '2026-10-01T10:00:00Z' }))
      .toBe('sigmet:ZZZZ|TS|2026-10-01 10:00:00+00:00');
    expect(sigmetChangeKey({ fir_id: 'ZZZZ', raw_text: '', hazard: null, valid_from: null }))
      .toBe('sigmet:ZZZZ||');
  });

  it('pythonDatetimeStr mirrors str(datetime)', () => {
    expect(pythonDatetimeStr('2026-10-01T10:00:00+00:00')).toBe('2026-10-01 10:00:00+00:00');
    expect(pythonDatetimeStr('2026-10-01T10:00Z')).toBe('2026-10-01 10:00:00+00:00');
    expect(pythonDatetimeStr('2026-10-01T10:00:00.5Z')).toBe('2026-10-01 10:00:00.500000+00:00');
    expect(pythonDatetimeStr('2026-10-01T10:00:00.000Z')).toBe('2026-10-01 10:00:00+00:00');
    expect(pythonDatetimeStr('2026-10-01T10:00:00+0200')).toBe('2026-10-01 10:00:00+02:00');
    expect(pythonDatetimeStr('2026-10-01T10:00:00')).toBe('2026-10-01 10:00:00');
  });
});

describe('formatting', () => {
  it('formats HH:MMZ in UTC', () => {
    expect(formatHhmmZ('2026-10-01T10:05:00+02:00')).toBe('08:05Z');
    expect(formatHhmmZ('2026-10-01T23:59:00')).toBe('23:59Z');
    expect(formatHhmmZ(null)).toBe('');
  });

  it('minutesAgo floors and never goes negative', () => {
    const now = new Date('2026-10-01T11:00:30Z');
    expect(minutesAgo('2026-10-01T10:30:00Z', now)).toBe(30);
    expect(minutesAgo('2026-10-01T11:05:00Z', now)).toBe(0);
    expect(minutesAgo('garbage', now)).toBeNull();
  });

  it('sameInstant ignores representation', () => {
    expect(sameInstant('2026-10-01T09:00:00Z', '2026-10-01T09:00:00.000+00:00')).toBe(true);
    expect(sameInstant('2026-10-01T09:00:00Z', null)).toBe(false);
  });
});
