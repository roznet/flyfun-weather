import { describe, expect, it } from 'vitest';

import { readTafAtEta, shortTemporaryLabel, tafValidityWindow } from '../../ts/helpers/taf-at-eta';

describe('readTafAtEta', () => {
  it('is none without a TAF', () => {
    expect(readTafAtEta({ has_taf: false, taf_raw: null })).toEqual({ kind: 'noTaf' });
  });

  it('reports an expired TAF with its validity instead of a category', () => {
    // EGSC on 13 Sep: latest TAF issued on the 11th, valid 15-17Z (#610).
    expect(readTafAtEta({
      has_taf: true,
      taf_raw: 'TAF EGSC 111404Z 1115/1117 29006KT 9999 SCT035',
      taf_valid_at_eta: false,
      taf_valid_from: '2026-09-11T15:00:00Z',
      taf_valid_to: '2026-09-11T17:00:00Z',
      taf_flight_category_at_eta: null,
    })).toEqual({ kind: 'notValid', window: '11/15Z-11/17Z' });
  });

  it('splits prevailing from a worse temporary group, with significant weather', () => {
    expect(readTafAtEta({
      has_taf: true,
      taf_valid_at_eta: true,
      taf_flight_category_at_eta: 'IFR',
      taf_prevailing_category_at_eta: 'MVFR',
      taf_temporary_category_at_eta: 'IFR',
      taf_temporary_type: 'PROB30 TEMPO',
      taf_significant_weather: ['TSRA', 'CB'],
    })).toEqual({
      kind: 'reading',
      prevailing: 'MVFR',
      temporary: { category: 'IFR', type: 'PROB30 TEMPO' },
      significantWeather: ['TSRA', 'CB'],
      legacyTrend: null,
    });
  });

  it('labels a temporary group with no type as TEMPO', () => {
    const r = readTafAtEta({
      has_taf: true,
      taf_valid_at_eta: true,
      taf_prevailing_category_at_eta: 'VFR',
      taf_temporary_category_at_eta: 'IFR',
      taf_temporary_type: null,
    });
    expect(r.kind === 'reading' && r.temporary).toEqual({ category: 'IFR', type: 'TEMPO' });
  });

  it('falls back to the combined category on packs built before #610', () => {
    expect(readTafAtEta({
      has_taf: true,
      taf_flight_category_at_eta: 'IFR',
      taf_trend_type: 'TEMPO',
    })).toEqual({
      kind: 'reading',
      prevailing: 'IFR',
      temporary: null,
      significantWeather: [],
      legacyTrend: 'TEMPO',
    });
  });
});

describe('tafValidityWindow', () => {
  it('formats day/hour Z and treats a naive string as UTC', () => {
    expect(tafValidityWindow('2026-09-11T15:00:00+00:00', '2026-09-12T00:00:00')).toBe('11/15Z-12/00Z');
  });

  it('is null when an end is missing or unparseable', () => {
    expect(tafValidityWindow(null, '2026-09-11T17:00:00Z')).toBeNull();
    expect(tafValidityWindow('garbage', '2026-09-11T17:00:00Z')).toBeNull();
  });
});

describe('shortTemporaryLabel', () => {
  it('keeps the PROB part only', () => {
    expect(shortTemporaryLabel('PROB30 TEMPO')).toBe('PROB30');
    expect(shortTemporaryLabel('PROB40')).toBe('PROB40');
    expect(shortTemporaryLabel('TEMPO')).toBe('TEMPO');
  });
});
