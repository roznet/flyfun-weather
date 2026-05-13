/** Tests for shared visualization color/width scales. */

import { describe, it, expect } from 'vitest';
import {
  riskMapColor, cloudCoverMapColor, headwindMapColor, capeMapColor,
  freezingLevelMapColor, ceilingMapColor, temperatureMapColor,
  crosswindMapColor, agreementMapColor, linearWidth, altitudeToPressureHpa,
  randomOverlapPct,
} from '../../ts/visualization/scales';

describe('riskMapColor', () => {
  it('maps known risk levels to expected hex colors', () => {
    expect(riskMapColor('none')).toBe('#22c55e');
    expect(riskMapColor('light')).toBe('#facc15');
    expect(riskMapColor('moderate')).toBe('#f97316');
    expect(riskMapColor('severe')).toBe('#ef4444');
    expect(riskMapColor('extreme')).toBe('#991b1b');
  });

  it('falls back to green for unknown risk', () => {
    expect(riskMapColor('totally-unknown')).toBe('#22c55e');
  });
});

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

describe('cloudCoverMapColor', () => {
  it('uses lightest gray at 0%', () => {
    expect(cloudCoverMapColor(0)).toBe('rgb(220, 220, 230)');
  });

  it('uses darkest gray at 100%', () => {
    expect(cloudCoverMapColor(100)).toBe('rgb(60, 60, 70)');
  });

  it('clamps values outside 0-100', () => {
    expect(cloudCoverMapColor(-50)).toBe(cloudCoverMapColor(0));
    expect(cloudCoverMapColor(150)).toBe(cloudCoverMapColor(100));
  });
});

describe('headwindMapColor', () => {
  it('returns white at 0 kt', () => {
    expect(headwindMapColor(0)).toBe('rgb(255, 255, 255)');
  });

  it('shifts toward red for headwind (positive kt)', () => {
    const c = headwindMapColor(30);
    // r=255 fixed, g and b drop equally
    expect(c).toMatch(/^rgb\(255, \d+, \d+\)$/);
    const match = c.match(/rgb\(255, (\d+), (\d+)\)/)!;
    expect(match[1]).toBe(match[2]); // g === b
    expect(parseInt(match[1])).toBeLessThan(100);
  });

  it('shifts toward green for tailwind (negative kt)', () => {
    const c = headwindMapColor(-30);
    // tailwind keeps r low, g high — clearly different from headwind
    const match = c.match(/rgb\((\d+), (\d+), (\d+)\)/)!;
    const [, r, g, b] = match.map(Number);
    expect(g).toBeGreaterThan(r);
    expect(g).toBeGreaterThan(b);
  });

  it('clamps magnitude at 30 kt', () => {
    expect(headwindMapColor(60)).toBe(headwindMapColor(30));
    expect(headwindMapColor(-60)).toBe(headwindMapColor(-30));
  });
});

describe('capeMapColor', () => {
  it('returns green at 0 J/kg', () => {
    expect(capeMapColor(0)).toBe('#22c55e');
  });

  it('returns dark red at >= 2000 J/kg', () => {
    expect(capeMapColor(2000)).toBe('#dc2626');
    expect(capeMapColor(5000)).toBe('#dc2626');
  });

  it('produces a continuous ramp through midpoint', () => {
    // At t=0.5 (jkg=1000) the two branches meet — color should be valid rgb
    const c = capeMapColor(1000);
    expect(c).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
  });
});

describe('ceilingMapColor', () => {
  it('returns green for null (treats absent as VFR)', () => {
    expect(ceilingMapColor(null)).toBe('#22c55e');
  });

  it('uses METAR category boundaries', () => {
    // Category boundaries are exactly the kind of off-by-one bug worth pinning.
    expect(ceilingMapColor(199)).toBe('#8e24aa');     // LIFR-purple < 200
    expect(ceilingMapColor(200)).toBe('#dc3545');     // LIFR-red 200..499
    expect(ceilingMapColor(499)).toBe('#dc3545');
    expect(ceilingMapColor(500)).toBe('#dc3545');     // IFR-red 500..999
    expect(ceilingMapColor(999)).toBe('#dc3545');
    expect(ceilingMapColor(1000)).toBe('#f59e0b');    // MVFR-amber 1000..2999
    expect(ceilingMapColor(2999)).toBe('#f59e0b');
    expect(ceilingMapColor(3000)).toBe('#22c55e');    // VFR-green >= 3000
  });
});

describe('temperatureMapColor', () => {
  it('returns valid rgb for cold/temperate/hot', () => {
    expect(temperatureMapColor(-20)).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
    expect(temperatureMapColor(0)).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
    expect(temperatureMapColor(40)).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
  });

  it('clamps below -20°C to coldest color', () => {
    expect(temperatureMapColor(-50)).toBe(temperatureMapColor(-20));
  });

  it('clamps above +40°C to hottest color', () => {
    expect(temperatureMapColor(60)).toBe(temperatureMapColor(40));
  });
});

describe('freezingLevelMapColor', () => {
  it('darkest blue at 0 ft (cold)', () => {
    expect(freezingLevelMapColor(0)).toBe('rgb(30, 60, 200)');
  });

  it('lightest blue at 15000 ft (warm)', () => {
    expect(freezingLevelMapColor(15000)).toBe('rgb(205, 235, 255)');
  });

  it('clamps above 15000', () => {
    expect(freezingLevelMapColor(25000)).toBe(freezingLevelMapColor(15000));
  });
});

describe('crosswindMapColor', () => {
  it('white at 0 kt', () => {
    expect(crosswindMapColor(0)).toBe('rgb(255, 255, 255)');
  });

  it('uses absolute value (sign-independent)', () => {
    expect(crosswindMapColor(15)).toBe(crosswindMapColor(-15));
  });

  it('clamps at 25 kt', () => {
    expect(crosswindMapColor(50)).toBe(crosswindMapColor(25));
  });
});

describe('agreementMapColor', () => {
  it('maps known agreement levels', () => {
    expect(agreementMapColor('good')).toBe('#22c55e');
    expect(agreementMapColor('moderate')).toBe('#f97316');
    expect(agreementMapColor('poor')).toBe('#ef4444');
  });

  it('falls back to green for unknown', () => {
    expect(agreementMapColor('???')).toBe('#22c55e');
  });
});

describe('linearWidth', () => {
  it('returns minW at value=0', () => {
    expect(linearWidth(0, 30, 3, 25)).toBe(3);
  });

  it('returns maxW at value=max', () => {
    expect(linearWidth(30, 30, 3, 25)).toBe(25);
  });

  it('interpolates linearly', () => {
    expect(linearWidth(15, 30, 3, 25)).toBe(14);
  });

  it('uses absolute value (sign-independent)', () => {
    expect(linearWidth(-30, 30, 3, 25)).toBe(25);
  });

  it('clamps above max', () => {
    expect(linearWidth(60, 30, 3, 25)).toBe(25);
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
