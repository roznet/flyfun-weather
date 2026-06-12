import { describe, it, expect } from 'vitest';
import { iasToTasISA, resolveCruiseSpeedIAS } from '../../ts/utils/atmo';

describe('iasToTasISA', () => {
  it('returns IAS unchanged at sea level (ISA datum)', () => {
    expect(iasToTasISA(120, 0)).toBeCloseTo(120, 1);
  });

  it('matches the documented ~+13% TAS at 8000 ft', () => {
    // 120 kt IAS @ 8000 ft → ~135 kt TAS (±1)
    expect(iasToTasISA(120, 8000)).toBeCloseTo(135, 0);
  });

  it('increases monotonically with altitude', () => {
    const ias = 120;
    const tas0 = iasToTasISA(ias, 0);
    const tas4k = iasToTasISA(ias, 4000);
    const tas8k = iasToTasISA(ias, 8000);
    const tas12k = iasToTasISA(ias, 12000);
    expect(tas4k).toBeGreaterThan(tas0);
    expect(tas8k).toBeGreaterThan(tas4k);
    expect(tas12k).toBeGreaterThan(tas8k);
  });

  it('scales linearly with IAS at a fixed altitude', () => {
    expect(iasToTasISA(200, 8000) / iasToTasISA(100, 8000)).toBeCloseTo(2, 5);
  });

  it('clamps negative altitudes to the sea-level datum', () => {
    expect(iasToTasISA(120, -500)).toBeCloseTo(120, 5);
  });

  it('clamps above the ISA tropopause (~36,089 ft) to tropopause density', () => {
    // The lapse-rate formula is only valid below the tropopause; FL400/FL450
    // are clamped to the FL360 (11,000 m) value rather than over-predicting TAS.
    const tasTropo = iasToTasISA(120, 36089);
    expect(iasToTasISA(120, 40000)).toBeCloseTo(tasTropo, 1);
    expect(iasToTasISA(120, 45000)).toBeCloseTo(tasTropo, 1);
    // Still strictly greater than a mid-altitude value (monotonic up to the clamp).
    expect(tasTropo).toBeGreaterThan(iasToTasISA(120, 12000));
  });
});

describe('resolveCruiseSpeedIAS', () => {
  it('prefers the aircraft speed when both are set', () => {
    expect(resolveCruiseSpeedIAS(140, 110)).toBe(140);
  });

  it('falls back to the profile speed when no aircraft speed', () => {
    expect(resolveCruiseSpeedIAS(null, 110)).toBe(110);
    expect(resolveCruiseSpeedIAS(undefined, 110)).toBe(110);
  });

  it('falls back to the profile speed when the aircraft speed is 0 or negative', () => {
    // A 0/negative aircraft speed is not a usable cruise speed — skip it.
    expect(resolveCruiseSpeedIAS(0, 110)).toBe(110);
    expect(resolveCruiseSpeedIAS(-5, 110)).toBe(110);
  });

  it('uses the aircraft speed when the profile has none', () => {
    expect(resolveCruiseSpeedIAS(140, null)).toBe(140);
    expect(resolveCruiseSpeedIAS(140, undefined)).toBe(140);
  });

  it('returns undefined when neither yields a usable value', () => {
    expect(resolveCruiseSpeedIAS(null, null)).toBeUndefined();
    expect(resolveCruiseSpeedIAS(undefined, undefined)).toBeUndefined();
    expect(resolveCruiseSpeedIAS(0, 0)).toBeUndefined();
  });
});
