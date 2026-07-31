import { describe, it, expect } from 'vitest';
import { modelSlotLabel } from '../../ts/utils';

// Variant badges for pack-model slots served by a higher-resolution source:
// the icon slot on ICON-D2 (#456), the gfs slot on HRRR (#457). modelLabel()
// falls back to the uppercased key while no catalog is loaded — deterministic
// for the unit test.
describe('modelSlotLabel', () => {
  it('badges the gfs slot when HRRR-sourced', () => {
    expect(modelSlotLabel('gfs', 'hrrr:noaa')).toBe('GFS (HRRR)');
  });

  it('badges the icon slot when ICON-D2-sourced', () => {
    expect(modelSlotLabel('icon', 'icon_d2:dwd')).toBe('ICON (D2)');
  });

  it('leaves ordinary sources unbadged', () => {
    expect(modelSlotLabel('gfs', 'gfs:noaa')).toBe('GFS');
    expect(modelSlotLabel('icon', 'icon_eu:dwd')).toBe('ICON');
    expect(modelSlotLabel('ecmwf', 'ecmwf:direct')).toBe('ECMWF');
  });
});
