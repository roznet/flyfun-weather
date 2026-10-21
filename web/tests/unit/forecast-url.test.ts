/** Absolute-date forecast links (`fc.date`, #840): precedence and fallback. */

import { describe, it, expect } from 'vitest';
import { createUrlState } from '../../ts/utils/url-state';
import {
  FORECAST_URL_SCHEMA, forecastDayIsoDate, resolveLinkedDate,
} from '../../ts/visualization/forecast-url';

// Late in the UTC day, so a local-time slip would show up as the wrong date.
const TODAY = new Date('2026-10-21T23:30:00Z');
const OFFERED = [0, 1, 2, 3, 4, 5, 6];

describe('forecastDayIsoDate', () => {
  it('names the UTC date fc.day points at', () => {
    expect(forecastDayIsoDate(0, TODAY)).toBe('2026-10-21');
    expect(forecastDayIsoDate(3, TODAY)).toBe('2026-10-24');
    expect(forecastDayIsoDate(11, TODAY)).toBe('2026-11-01');
  });
});

describe('resolveLinkedDate', () => {
  it('maps a date inside the horizon to its relative day', () => {
    expect(resolveLinkedDate('2026-10-24', OFFERED, TODAY)).toEqual({ day: 3, fallback: null });
    expect(resolveLinkedDate('2026-10-21', OFFERED, TODAY)).toEqual({ day: 0, fallback: null });
  });

  it('keeps the same calendar date whichever day the link is opened', () => {
    // Made Wednesday for Saturday (fc.day=3); opened Thursday it is day 2.
    const thursday = new Date('2026-10-22T08:00:00Z');
    expect(resolveLinkedDate('2026-10-24', OFFERED, thursday)).toEqual({ day: 2, fallback: null });
  });

  it('falls back visibly to today for a past date', () => {
    expect(resolveLinkedDate('2026-10-19', OFFERED, TODAY)).toEqual({ day: 0, fallback: 'past' });
  });

  it('falls back visibly to the last offered day beyond the horizon', () => {
    expect(resolveLinkedDate('2026-10-30', OFFERED, TODAY)).toEqual({ day: 6, fallback: 'beyond' });
    expect(resolveLinkedDate('2026-10-27', [0, 1, 2, 3], TODAY)).toEqual({ day: 3, fallback: 'beyond' });
  });

  it('falls back to the nearest offered day inside a gap, ties to the earlier', () => {
    expect(resolveLinkedDate('2026-10-24', [0, 1, 2, 5, 6], TODAY)).toEqual({ day: 2, fallback: 'unavailable' });
    expect(resolveLinkedDate('2026-10-25', [0, 1, 2, 5, 6], TODAY)).toEqual({ day: 5, fallback: 'unavailable' });
    expect(resolveLinkedDate('2026-10-24', [0, 1, 2, 4, 6], TODAY)).toEqual({ day: 2, fallback: 'unavailable' });
  });

  it('is null for a malformed date or an empty grid (fc.day then applies)', () => {
    for (const bad of ['', '2026-10-2', '2026-13-01', '2026-02-30', 'tomorrow']) {
      expect(resolveLinkedDate(bad, OFFERED, TODAY)).toBeNull();
    }
    expect(resolveLinkedDate('2026-10-24', [], TODAY)).toBeNull();
  });
});

describe('fc.date URL key', () => {
  const state = createUrlState(FORECAST_URL_SCHEMA);

  // vitest runs in `node`: stub just what `read()` touches.
  function read(search: string) {
    (globalThis as any).window = { location: { pathname: '/maps.html', search, hash: '' } };
    return state.read();
  }

  it('reads a well-formed date and drops a malformed one', () => {
    expect(read('?fc.date=2026-10-24&fc.day=3')['fc.date']).toBe('2026-10-24');
    expect(read('?fc.date=2026-02-30&fc.day=3')['fc.date']).toBe('');
  });

  it('leaves a link without fc.date on fc.day, as before', () => {
    const v = read('?fc.day=3&fc.hour=15');
    expect(v['fc.date']).toBe('');
    expect(v['fc.day']).toBe(3);
  });
});
