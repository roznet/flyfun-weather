import { describe, it, expect } from 'vitest';
import {
  formatVisibility,
  formatQNH,
  formatTemperature,
  getUnitsRegion,
  setUnitsPreference,
  setFlightRegion,
  regionFromIcaos,
} from '../../ts/units';

describe('formatVisibility', () => {
  it('returns empty string for null/undefined', () => {
    expect(formatVisibility(null, 'us')).toBe('');
    expect(formatVisibility(undefined, 'europe')).toBe('');
  });

  it('formats statute miles for us', () => {
    expect(formatVisibility(4500, 'us')).toBe('2.8 SM');
    expect(formatVisibility(20000, 'us')).toBe('>10 SM');
  });

  it('formats meters/km for europe', () => {
    expect(formatVisibility(4500, 'europe')).toBe('4500 m');
    expect(formatVisibility(6000, 'europe')).toBe('6 km');
    expect(formatVisibility(10000, 'europe')).toBe('>10 km');
  });
});

describe('formatQNH', () => {
  it('formats hPa for europe and inHg for us', () => {
    expect(formatQNH(1013, 'europe')).toBe('1013 hPa');
    expect(formatQNH(1013.25, 'us')).toBe('29.92 inHg');
    expect(formatQNH(null, 'us')).toBe('');
  });
});

describe('formatTemperature', () => {
  it('formats C for europe and F for us', () => {
    expect(formatTemperature(12, 'europe')).toBe('12°C');
    expect(formatTemperature(0, 'us')).toBe('32°F');
    expect(formatTemperature(20, 'us')).toBe('68°F');
  });
});

describe('regionFromIcaos', () => {
  it('classifies US, European, and mixed routes', () => {
    expect(regionFromIcaos(['KJFK', 'KBOS'])).toBe('us');
    expect(regionFromIcaos(['CYYZ', 'KORD'])).toBe('us');
    expect(regionFromIcaos(['LFPG', 'EGLL'])).toBe('europe');
    expect(regionFromIcaos(['KJFK', 'LFPG'])).toBe(null); // mixed
    expect(regionFromIcaos([])).toBe(null);
  });
});

describe('units preference + auto resolution', () => {
  it('forced preference ignores flight region', () => {
    setUnitsPreference('us');
    expect(getUnitsRegion()).toBe('us');
    setFlightRegion('europe');
    expect(getUnitsRegion()).toBe('us');
    // formatters with no explicit region follow the active singleton
    expect(formatVisibility(4500)).toBe('2.8 SM');
  });

  it('auto resolves from the flight region, defaulting to europe', () => {
    setUnitsPreference('auto');
    expect(getUnitsRegion()).toBe('europe'); // no hint yet
    setFlightRegion('us');
    expect(getUnitsRegion()).toBe('us');
    setFlightRegion(null);
    expect(getUnitsRegion()).toBe('europe');
  });

  it('unknown preference value falls back to auto', () => {
    setUnitsPreference('garbage');
    setFlightRegion(null);
    expect(getUnitsRegion()).toBe('europe');
    setFlightRegion('us');
    expect(getUnitsRegion()).toBe('us');
  });
});
