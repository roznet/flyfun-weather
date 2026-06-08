import { describe, it, expect } from 'vitest';
import { iasToTasISA } from '../../ts/utils/atmo';

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
});
