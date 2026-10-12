import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { sigmetSectionState } from '../../ts/helpers/route-sigmets';
import type { RouteSigmets } from '../../ts/store/types';

function block(over: Partial<RouteSigmets> = {}): RouteSigmets {
  return {
    corridor_nm: 50, fetch_time: '2026-10-12T07:00:00Z',
    altitude_low_ft: 0, altitude_high_ft: 10000,
    time_window_from: null, time_window_to: null,
    route_firs: ['ZZZZ'], sigmets: [], hazards: [], has_severe: false, count: 0,
    ...over,
  };
}

const ONE = {
  fir_id: 'ZZZZ', fir_name: null, hazard: 'TS', qualifier: 'EMBD', base_ft: null, top_ft: 38000,
  valid_from: null, valid_to: null, direction: null, speed_kt: null, raw_text: '',
  matched_firs: ['ZZZZ'], min_distance_nm: 0, enroute_distance_from_nm: null,
  enroute_distance_to_nm: null, coords: [],
} as RouteSigmets['sigmets'][number];

describe('sigmetSectionState (#734)', () => {
  it('a failed fetch reads "unavailable", not an empty (hidden) route', () => {
    expect(sigmetSectionState(block({ fetch_ok: false }))).toBe('unavailable');
  });

  it('a good fetch with nothing matched stays hidden, as before', () => {
    expect(sigmetSectionState(block({ fetch_ok: true }))).toBe('hidden');
  });

  it('an older pack without fetch_ok renders as today', () => {
    expect(sigmetSectionState(block())).toBe('hidden');
    expect(sigmetSectionState(block({ sigmets: [ONE], count: 1 }))).toBe('table');
  });

  it('no block (not D-0) is hidden', () => {
    expect(sigmetSectionState(null)).toBe('hidden');
    expect(sigmetSectionState(undefined)).toBe('hidden');
  });

  it('every locale carries the unavailable wording', () => {
    for (const loc of ['en', 'fr', 'de', 'es']) {
      const json = JSON.parse(readFileSync(resolve(__dirname, `../../ts/i18n/locales/${loc}.json`), 'utf8'));
      expect(json['sigmets.unavailable'], loc).toBeTruthy();
    }
    // Mirrors models/observations.py::SIGMETS_UNAVAILABLE_TEXT word for word.
    const en = JSON.parse(readFileSync(resolve(__dirname, '../../ts/i18n/locales/en.json'), 'utf8'));
    expect(en['sigmets.unavailable']).toBe(
      'SIGMETs unavailable at briefing time (source did not respond). Not checked, not "none": check SIGMETs before departure.',
    );
  });
});
