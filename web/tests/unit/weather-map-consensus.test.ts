/** Tests for the shared consensus reducers still used off the forecast map.
 *
 * The forecast map's worst/majority consensus is now baked server-side and
 * read straight off the payload (#419), so `computeConsensus` was retired.
 * What remains are the small pure reducers other surfaces share: the mode
 * type-guard, `median`/`circularMean`, and `ordinalConsensus` (which powers
 * the client-side FAA/EASA alternate-required aggregation). */

import { describe, it, expect } from 'vitest';
import {
  isConsensusMode, median, circularMean, ordinalConsensus,
  CAT_ORDER, RISK_ORDER,
} from '../../ts/visualization/weather-map-consensus';

describe('isConsensusMode', () => {
  it('detects "worst" and "majority"', () => {
    expect(isConsensusMode('worst')).toBe(true);
    expect(isConsensusMode('majority')).toBe(true);
  });

  it('rejects model names', () => {
    expect(isConsensusMode('gfs')).toBe(false);
    expect(isConsensusMode('ecmwf')).toBe(false);
    expect(isConsensusMode('')).toBe(false);
  });
});

describe('median', () => {
  it('handles odd and even counts', () => {
    expect(median([1, 2, 3])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });

  it('does not mutate input', () => {
    const xs = [3, 1, 2];
    median(xs);
    expect(xs).toEqual([3, 1, 2]);
  });

  it('throws on empty input rather than returning NaN', () => {
    expect(() => median([])).toThrow(/empty/);
  });
});

describe('circularMean', () => {
  it('returns the angle for a single value', () => {
    expect(circularMean([90])).toBeCloseTo(90, 5);
  });

  it('handles wrap-around at 0/360', () => {
    // Mean of 350 and 10 should be ~0 (or 360), not 180
    const m = circularMean([350, 10]);
    expect(Math.min(m, 360 - m)).toBeLessThan(1);
  });

  it('returns the linear mean for nearby angles', () => {
    expect(circularMean([80, 100])).toBeCloseTo(90, 1);
  });

  it('returns a value in [0, 360)', () => {
    const m = circularMean([350, 10, 20]);
    expect(m).toBeGreaterThanOrEqual(0);
    expect(m).toBeLessThan(360);
  });
});

describe('ordinalConsensus', () => {
  describe('with CAT_ORDER (object)', () => {
    it('worst returns highest-ranked', () => {
      expect(ordinalConsensus(['VFR', 'MVFR', 'IFR'], CAT_ORDER, 'worst')).toBe('IFR');
      expect(ordinalConsensus(['VFR', 'LIFR', 'MVFR'], CAT_ORDER, 'worst')).toBe('LIFR');
    });

    it('majority returns the modal value', () => {
      expect(ordinalConsensus(['VFR', 'VFR', 'IFR'], CAT_ORDER, 'majority')).toBe('VFR');
    });

    it('majority breaks ties by picking the worse value', () => {
      // 1 each — picks worst among tied (LIFR)
      expect(ordinalConsensus(['VFR', 'IFR', 'LIFR'], CAT_ORDER, 'majority')).toBe('LIFR');
      // 2 vs 1, but since two distinct candidates have count 1, modal is the 2 — let's check actual
      expect(ordinalConsensus(['VFR', 'VFR', 'IFR'], CAT_ORDER, 'majority')).toBe('VFR');
    });
  });

  describe('with RISK_ORDER (array)', () => {
    it('worst picks highest-ranked', () => {
      expect(ordinalConsensus(['none', 'low', 'high'], RISK_ORDER, 'worst')).toBe('high');
      expect(ordinalConsensus(['marginal', 'extreme', 'low'], RISK_ORDER, 'worst')).toBe('extreme');
    });

    it('majority picks the modal risk', () => {
      expect(ordinalConsensus(['low', 'low', 'high'], RISK_ORDER, 'majority')).toBe('low');
    });
  });

  it('worst returns the only value when all equal', () => {
    expect(ordinalConsensus(['VFR', 'VFR'], CAT_ORDER, 'worst')).toBe('VFR');
  });

  it('throws on empty input rather than reduce-of-empty TypeError', () => {
    expect(() => ordinalConsensus([], CAT_ORDER, 'worst')).toThrow(/empty/);
    expect(() => ordinalConsensus([], CAT_ORDER, 'majority')).toThrow(/empty/);
  });
});
