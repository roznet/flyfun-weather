import { describe, expect, it } from 'vitest';

import {
  actionForAdvisory,
  COMPARE_LAYER_BY_METRIC,
} from '../../ts/visualization/advisory-actions';

describe('actionForAdvisory', () => {
  it('maps existing preset advisories to preset-focus', () => {
    expect(actionForAdvisory('cloud_top')).toEqual({ kind: 'preset-focus' });
  });

  it('opens model agreement in cross-model compare', () => {
    expect(actionForAdvisory('model_agreement')).toEqual({ kind: 'compare-models' });
  });

  it('never maps DD/NWP agreement to cross-model compare', () => {
    expect(actionForAdvisory('dd_nwp_agreement')).toEqual({ kind: 'method-context' });
  });

  it('maps airport and fronts actions to their semantic surfaces', () => {
    expect(actionForAdvisory('airport_wind')).toEqual({ kind: 'airport-profile' });
    expect(actionForAdvisory('flight_category')).toEqual({ kind: 'airport-profile' });
    expect(actionForAdvisory('fronts')).toEqual({ kind: 'fronts-map' });
  });

  it('returns null for an unknown advisory', () => {
    expect(actionForAdvisory('not_a_real_advisory')).toBeNull();
  });

  it.each(['toString', 'constructor', '__proto__'])(
    'does not treat inherited key %s as a registered action or metric',
    (key) => {
      expect(actionForAdvisory(key)).toBeNull();
      expect((COMPARE_LAYER_BY_METRIC as Record<string, unknown>)[key]).toBeUndefined();
    },
  );
});

describe('COMPARE_LAYER_BY_METRIC', () => {
  it('contains the closed backend metric mapping', () => {
    expect(COMPARE_LAYER_BY_METRIC).toEqual({
      freezing_level_ft: 'freezing-level',
      cloud_coverage: 'square-nwp-cloud-bands',
      cloud_cover_pct: 'square-nwp-cloud-bands',
      icing_risk: 'icing-bands',
      icing_ogimet_nwp_risk: 'icing-ogimet-nwp-bands',
      sfip_risk: 'sfip-bands',
      cat_risk: 'cat-bands',
      convective_risk: 'thermo-convective-bg',
      nwp_convective_risk: 'nwp-convective-bg',
    });
  });
});
