import { describe, it, expect } from 'vitest';
import {
  relativeDayHour, nearestHour, resolveOverlaySlot, formatForecastTime, forecastMapUrl,
} from '../../ts/visualization/route-map/forecast-overlay';
import type { DayAvailability } from '../../ts/adapters/maps-adapter';

const NOW = new Date('2026-07-15T08:00:00Z');

// A representative (non-rectangular) grid: near days carry the fine hour set and
// all three models; D+6 is coarse (6/12/18) and ICON has dropped off.
const DAYS: DayAvailability[] = [
  { day: 0, date: '2026-07-15', available: true, hours: [6, 9, 12, 15, 18], models: ['gfs', 'icon', 'ecmwf'] },
  { day: 3, date: '2026-07-18', available: true, hours: [6, 9, 12, 15, 18], models: ['gfs', 'icon', 'ecmwf'] },
  { day: 5, date: '2026-07-20', available: false, hours: [], models: [] },
  { day: 6, date: '2026-07-21', available: true, hours: [6, 12, 18], models: ['gfs', 'ecmwf'] },
];

describe('relativeDayHour', () => {
  it('today → day 0, UTC hour', () => {
    expect(relativeDayHour('2026-07-15T14:30:00Z', NOW)).toEqual({ day: 0, hour: 14 });
  });
  it('future day difference in UTC calendar days', () => {
    expect(relativeDayHour('2026-07-18T09:00:00Z', NOW)).toEqual({ day: 3, hour: 9 });
  });
  it('past flight → negative day', () => {
    expect(relativeDayHour('2026-07-14T12:00:00Z', NOW)).toEqual({ day: -1, hour: 12 });
  });
  it('null / garbage → null', () => {
    expect(relativeDayHour(null, NOW)).toBeNull();
    expect(relativeDayHour('not-a-date', NOW)).toBeNull();
  });
});

describe('nearestHour', () => {
  it('snaps to the closest offered hour', () => {
    expect(nearestHour([6, 12, 18], 14)).toBe(12);
    expect(nearestHour([6, 9, 12, 15, 18], 13)).toBe(12);
    expect(nearestHour([6, 9, 12, 15, 18], 0)).toBe(6);
    expect(nearestHour([6, 9, 12, 15, 18], 23)).toBe(18);
  });
  it('ties resolve to the earlier hour', () => {
    expect(nearestHour([6, 12, 18], 15)).toBe(12); // equidistant 12/18 → 12
  });
  it('empty list → null', () => {
    expect(nearestHour([], 12)).toBeNull();
  });
});

describe('resolveOverlaySlot', () => {
  it('snaps the departure hour to the day’s offered hours', () => {
    expect(resolveOverlaySlot('2026-07-15T14:00:00Z', DAYS, NOW))
      .toEqual({ day: 0, hour: 15, models: ['gfs', 'icon', 'ecmwf'] });
  });
  it('honours the coarse hour grid on the far day (not the fine set)', () => {
    // A D+6 flight at 14Z must snap to 12 (the coarse grid), never 15.
    expect(resolveOverlaySlot('2026-07-21T14:00:00Z', DAYS, NOW))
      .toEqual({ day: 6, hour: 12, models: ['gfs', 'ecmwf'] });
  });
  it('returns null beyond the advertised horizon', () => {
    expect(resolveOverlaySlot('2026-07-22T12:00:00Z', DAYS, NOW)).toBeNull(); // day 7 absent
  });
  it('returns null for a day present but not available', () => {
    expect(resolveOverlaySlot('2026-07-20T12:00:00Z', DAYS, NOW)).toBeNull(); // day 5 available:false
  });
  it('returns null for a past flight', () => {
    expect(resolveOverlaySlot('2026-07-14T12:00:00Z', DAYS, NOW)).toBeNull();
  });
});

describe('formatForecastTime', () => {
  it('formats as "<weekday> <HH>Z" in UTC', () => {
    expect(formatForecastTime('1970-01-01T00:00:00Z')).toBe('Thu 00Z'); // epoch = Thursday
    expect(formatForecastTime('1970-01-04T15:00:00Z')).toBe('Sun 15Z');
  });
  it('empty string for an unparseable value', () => {
    expect(formatForecastTime('nope')).toBe('');
  });
});

describe('forecastMapUrl', () => {
  const slot = { day: 2, hour: 12, models: ['gfs', 'icon', 'ecmwf'] };
  it('passes a supported individual model through as fc.model', () => {
    expect(forecastMapUrl(slot, 'ecmwf', 'flight_category'))
      .toBe('maps.html?fc.day=2&fc.hour=12&fc.model=ecmwf&fc.metric=flight_category');
  });
  it('omits fc.model for an unsupported model (full map uses its default)', () => {
    expect(forecastMapUrl(slot, 'ukmo', 'wind_speed_kt'))
      .toBe('maps.html?fc.day=2&fc.hour=12&fc.metric=wind_speed_kt');
    expect(forecastMapUrl(slot, 'worst', 'flight_category'))
      .toBe('maps.html?fc.day=2&fc.hour=12&fc.metric=flight_category');
  });
});
