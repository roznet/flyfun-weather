/** Aircraft form climb / descent presets (#1005): when the values read as
 *  "Custom", and what a save sends (the base preset + only what differs). */

import { describe, it, expect } from 'vitest';
import {
  isCustom,
  overridesFor,
  presetValues,
  type PerformanceValues,
} from '../../ts/components/aircraft-performance-form';
import type { PerformancePreset } from '../../ts/adapters/aircraft-adapter';

const HP: PerformancePreset = {
  id: 'hp_piston', climb_ias_kt: 110, climb_rate_fpm: 1000,
  descent_mode: 'rate', descent_rate_fpm: 500, descent_angle_deg: null, turbo: false,
};
const TURBOPROP: PerformancePreset = {
  id: 'single_turboprop', climb_ias_kt: 150, climb_rate_fpm: 2000,
  descent_mode: 'angle', descent_rate_fpm: null, descent_angle_deg: 3, turbo: false,
};

const values = (over: Partial<PerformanceValues> = {}): PerformanceValues => ({ ...presetValues(HP), ...over });

describe('isCustom', () => {
  it('is false on the preset values and on empty fields', () => {
    expect(isCustom(values(), HP)).toBe(false);
    expect(isCustom(values({ climb_ias_kt: null, climb_rate_fpm: null }), HP)).toBe(false);
  });

  it('is true when a value or the turbo box differs', () => {
    expect(isCustom(values({ climb_rate_fpm: 900 }), HP)).toBe(true);
    expect(isCustom(values({ turbo: true }), HP)).toBe(true);
    // An angle on a rate preset is a different descent.
    expect(isCustom(values({ descent_rate_fpm: null, descent_angle_deg: 3 }), HP)).toBe(true);
  });

  it('reads an angle preset with the rate field empty as the preset', () => {
    expect(isCustom(presetValues(TURBOPROP), TURBOPROP)).toBe(false);
  });
});

describe('overridesFor', () => {
  it('sends the preset and zeros when nothing differs', () => {
    expect(overridesFor(values(), HP)).toEqual({
      performance_preset: 'hp_piston', is_turbo: false,
      climb_ias_kt: 0, climb_rate_fpm: 0, descent_rate_fpm: 0, descent_angle_deg: 0,
    });
  });

  it('sends only the differing values, and turbo as is', () => {
    expect(overridesFor(values({ climb_rate_fpm: 850, turbo: true }), HP)).toEqual({
      performance_preset: 'hp_piston', is_turbo: true,
      climb_ias_kt: 0, climb_rate_fpm: 850, descent_rate_fpm: 0, descent_angle_deg: 0,
    });
  });

  it('switches an angle preset to a rate descent', () => {
    const v = { ...presetValues(TURBOPROP), descent_angle_deg: null, descent_rate_fpm: 1500 };
    const o = overridesFor(v, TURBOPROP);
    expect(o.descent_rate_fpm).toBe(1500);
    expect(o.descent_angle_deg).toBe(0);
  });
});
