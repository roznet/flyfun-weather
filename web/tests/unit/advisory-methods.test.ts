import { afterEach, describe, expect, it, vi } from 'vitest';

import { advisoryMethodLabel } from '../../ts/visualization/advisory-methods';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('advisoryMethodLabel', () => {
  it.each([
    ['dewpoint_depression', 'DD'],
    ['nwp', 'NWP'],
    ['nwp_synthesized', 'NWP + DD envelope'],
    ['nwp_precipitation_profile', 'NWP precip'],
    ['ogimet_dd', 'Ogimet-DD'],
    ['ogimet_nwp', 'Ogimet-NWP'],
    ['sfip', 'SFIP'],
    ['ieng', 'IENG'],
    ['thermo', 'Thermo'],
    ['nwp_with_dd_floor', 'NWP + DD floor'],
    ['richardson_cat', 'Ri CAT'],
    ['vertical_motion', 'Vertical motion'],
    ['cat_with_vertical_motion', 'CAT + motion'],
    ['terrain_wind', 'Terrain wind'],
    ['terrain_wind_wave', 'Wind + wave'],
    ['model_divergence', 'Model spread'],
    ['dd_vs_nwp', 'DD ↔ NWP'],
    ['airport_conditions', 'Airport NWP'],
    ['runway_components', 'Runway wind'],
    ['density_altitude', 'Density altitude'],
    ['bulk_shear', 'Bulk shear'],
    ['hewson', 'Hewson'],
    ['vfr_composite', 'VFR composite'],
    ['ifr_composite', 'IFR composite'],
  ])('uses the canonical short label for %s', (methodId, short) => {
    const label = advisoryMethodLabel(methodId);
    expect(label?.short).toBe(short);
    expect(label?.description.length).toBeGreaterThan(0);
  });

  it('renders the compound floor label with a non-empty description', () => {
    expect(advisoryMethodLabel('nwp_with_dd_floor')).toEqual({
      short: 'NWP + DD floor',
      description: expect.stringMatching(/\S/),
    });
  });

  it('warns once per unknown lookup and never infers a badge', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);

    expect(advisoryMethodLabel('future_magic_method')).toBeNull();
    expect(advisoryMethodLabel('future_magic_method')).toBeNull();
    expect(warn).toHaveBeenCalledTimes(2);
    expect(warn).toHaveBeenNthCalledWith(1, 'Unknown advisory method_id: future_magic_method');
    expect(warn).toHaveBeenNthCalledWith(2, 'Unknown advisory method_id: future_magic_method');
  });

  it.each(['toString', 'constructor', '__proto__'])(
    'treats inherited key %s as an unknown method',
    (methodId) => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
      expect(advisoryMethodLabel(methodId)).toBeNull();
      expect(warn).toHaveBeenCalledOnce();
      expect(warn).toHaveBeenCalledWith(`Unknown advisory method_id: ${methodId}`);
    },
  );

  it('returns null without warning when no method ID is present', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(advisoryMethodLabel(null)).toBeNull();
    expect(advisoryMethodLabel(undefined)).toBeNull();
    expect(warn).not.toHaveBeenCalled();
  });
});
