import { afterEach, describe, expect, it, vi } from 'vitest';

import { advisoryMethodLabel } from '../../ts/visualization/advisory-methods';
import de from '../../ts/i18n/locales/de.json';
import en from '../../ts/i18n/locales/en.json';
import es from '../../ts/i18n/locales/es.json';
import fr from '../../ts/i18n/locales/fr.json';

const EXPECTED_METHOD_KEYS = [
  'airport_conditions',
  'bulk_shear',
  'cat_with_vertical_motion',
  'dd_vs_nwp',
  'density_altitude',
  'dewpoint_depression',
  'gust_factor',
  'hewson',
  'ieng',
  'ifr_composite',
  'llws_composite',
  'model_divergence',
  'nwp',
  'nwp_precipitation_profile',
  'nwp_synthesized',
  'nwp_with_dd_floor',
  'ogimet_dd',
  'ogimet_nwp',
  'richardson_cat',
  'runway_components',
  'runway_wind_with_gust',
  'sfip',
  'terrain_wind',
  'terrain_wind_wave',
  'thermo',
  'vertical_motion',
  'vfr_composite',
  'wind_gust',
].sort();

function advisoryMethodKeys(locale: Record<string, string>): string[] {
  return Object.keys(locale)
    .filter(key => key.startsWith('advisories.methods.'))
    .map(key => key.slice('advisories.methods.'.length))
    .sort();
}

function placeholderNames(message: string): string[] {
  return [...message.matchAll(/\{([^{}]+)\}/g)]
    .map(match => match[1])
    .sort();
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('advisoryMethodLabel', () => {
  it('defines the complete advisory method translation set in every locale', () => {
    const locales = { en, fr, de, es } as const;
    for (const [locale, messages] of Object.entries(locales)) {
      expect(advisoryMethodKeys(messages), locale).toEqual(EXPECTED_METHOD_KEYS);
      for (const key of EXPECTED_METHOD_KEYS) {
        expect(messages[`advisories.methods.${key}` as keyof typeof messages], `${locale}.${key}`)
          .toMatch(/\S/);
      }
    }
  });

  it('defines the unavailable airport-profile message consistently in every locale', () => {
    const locales: Record<string, Record<string, string>> = { en, fr, de, es };
    const key = 'advisories.airportProfileUnavailable';

    for (const [locale, messages] of Object.entries(locales)) {
      expect(messages[key], `${locale}.${key}`).toMatch(/\S/);
    }

    const expectedPlaceholders = placeholderNames(en[key]);
    for (const [locale, messages] of Object.entries(locales)) {
      expect(placeholderNames(messages[key]), `${locale}.${key} placeholders`)
        .toEqual(expectedPlaceholders);
    }
  });

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
    ['wind_gust', 'Wind gust'],
    ['runway_wind_with_gust', 'Runway + gust'],
    ['density_altitude', 'Density altitude'],
    ['bulk_shear', 'Bulk shear'],
    ['gust_factor', 'Gust factor'],
    ['llws_composite', 'Shear + gust'],
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
