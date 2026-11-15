/** Tests for shared visualization color/width scales. */

import { describe, it, expect } from 'vitest';
import {
  altitudeToPressureHpa,
  randomOverlapPct,
} from '../../ts/visualization/scales';

describe('randomOverlapPct', () => {
  it('returns 0 when all bands are 0', () => {
    expect(randomOverlapPct(0, 0, 0)).toBe(0);
  });

  it('returns 100 when any band is 100', () => {
    expect(randomOverlapPct(100, 0, 0)).toBe(100);
    expect(randomOverlapPct(0, 100, 0)).toBe(100);
    expect(randomOverlapPct(0, 0, 100)).toBe(100);
    expect(randomOverlapPct(100, 50, 30)).toBe(100);
  });

  it('passes a single band through unchanged', () => {
    expect(randomOverlapPct(80, 0, 0)).toBeCloseTo(80, 9);
    expect(randomOverlapPct(0, 45, 0)).toBeCloseTo(45, 9);
  });

  it('matches the random-overlap reference values from #124', () => {
    // 1 - 0.7^3 = 0.657
    expect(randomOverlapPct(30, 30, 30)).toBeCloseTo(65.7, 1);
    // 1 - 0.5^3 = 0.875
    expect(randomOverlapPct(50, 50, 50)).toBeCloseTo(87.5, 9);
    // 1 - 0.4^3 = 0.936
    expect(randomOverlapPct(60, 60, 60)).toBeCloseTo(93.6, 1);
  });

  it('is strictly less than the additive sum in the partial-overlap regime', () => {
    const additive = 30 + 30 + 30;
    expect(randomOverlapPct(30, 30, 30)).toBeLessThan(additive);
  });

  it('clamps inputs outside [0, 100]', () => {
    expect(randomOverlapPct(-10, 0, 0)).toBe(0);
    expect(randomOverlapPct(150, 0, 0)).toBe(100);
  });
});

describe('altitudeToPressureHpa', () => {
  it('returns ~1013 hPa at sea level', () => {
    expect(altitudeToPressureHpa(0)).toBeCloseTo(1013.25, 1);
  });

  it('returns ~700 hPa at ~10000 ft', () => {
    // Standard atmosphere: 10000 ft ≈ 697 hPa
    expect(altitudeToPressureHpa(10000)).toBeGreaterThan(690);
    expect(altitudeToPressureHpa(10000)).toBeLessThan(710);
  });

  it('returns ~500 hPa at ~18000 ft', () => {
    expect(altitudeToPressureHpa(18000)).toBeGreaterThan(490);
    expect(altitudeToPressureHpa(18000)).toBeLessThan(510);
  });

  it('is monotonically decreasing', () => {
    expect(altitudeToPressureHpa(0)).toBeGreaterThan(altitudeToPressureHpa(5000));
    expect(altitudeToPressureHpa(5000)).toBeGreaterThan(altitudeToPressureHpa(15000));
  });
});
