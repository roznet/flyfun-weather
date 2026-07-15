import { describe, it, expect } from 'vitest';
import {
  formatVisibility,
  formatQNH,
  formatTemperature,
  getUnitsRegion,
  setUnitsPreference,
  setFlightRegion,
  regionFromIcaos,
  formatWind,
  formatWindComponent,
  GUST_DISPLAY_MIN_EXCESS_KT,
} from '../../ts/units';

describe('formatWind', () => {
  it('formats dir@speedGgust with a zero-padded 3-digit direction', () => {
    expect(formatWind(15, 273, 22)).toBe('273@15G22');
    expect(formatWind(8, 5, null)).toBe('005@8');
  });

  it('drops the direction prefix when direction is missing, keeps speed', () => {
    expect(formatWind(15, null, null)).toBe('15');
  });

  it('returns empty when speed is missing', () => {
    expect(formatWind(null, 270, 30)).toBe('');
  });

  it('shows the gust only when it exceeds the sustained speed by the threshold', () => {
    // +1 kt gust — suppressed
    expect(formatWind(5, 9, 6)).toBe('009@5');
    // exactly the threshold — shown
    expect(formatWind(5, 9, 5 + GUST_DISPLAY_MIN_EXCESS_KT)).toBe('009@5G9');
    // well above — shown
    expect(formatWind(5, 9, 10)).toBe('009@5G10');
    // just below the threshold — suppressed
    expect(formatWind(5, 9, 5 + GUST_DISPLAY_MIN_EXCESS_KT - 1)).toBe('009@5');
  });
});

describe('formatWindComponent', () => {
  it('formats a crosswind/headwind component with the same gust rule', () => {
    expect(formatWindComponent(12, 18)).toBe('12G18');
    expect(formatWindComponent(6, 6)).toBe('6');       // no meaningful gust
    expect(formatWindComponent(6, null)).toBe('6');
    expect(formatWindComponent(null, 10)).toBe('');
  });
});

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
