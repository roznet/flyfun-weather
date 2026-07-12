import { describe, it, expect } from 'vitest';
import { buildParamDefaults, pruneAdvisoryParams, pruneEngineMethod } from '../../ts/helpers/profile-sparsify';
import type { AdvisoryCatalogEntry, AdvisoryParameterDef } from '../../ts/types/advisories';

/** Minimal catalog entry with just the params under test. */
function entry(id: string, params: Array<[string, number]>): AdvisoryCatalogEntry {
  return {
    id,
    name: id,
    short_description: '',
    description: '',
    category: 'cloud',
    default_enabled: true,
    altitude_dependent: false,
    parameters: params.map(([key, def]): AdvisoryParameterDef => ({
      key, label: key, description: '', type: 'number', unit: '',
      default: def, min: null, max: null, step: null,
    })),
  };
}

const CATALOG: AdvisoryCatalogEntry[] = [
  entry('vmc_cruise', [['bkn_pct_amber', 30], ['ovc_pct_red', 50]]),
  entry('icing_escape', [['no_escape_pct_red', 15]]),
];

describe('pruneAdvisoryParams', () => {
  it('drops every param equal to its catalog default (all-default → empty)', () => {
    const raw = {
      vmc_cruise: { bkn_pct_amber: 30, ovc_pct_red: 50 },
      icing_escape: { no_escape_pct_red: 15 },
    };
    const pruned = pruneAdvisoryParams(raw, buildParamDefaults(CATALOG));
    expect(pruned).toEqual({});
  });

  it('keeps exactly the one param moved off its default', () => {
    const raw = {
      vmc_cruise: { bkn_pct_amber: 30, ovc_pct_red: 40 },  // ovc moved 50 → 40
      icing_escape: { no_escape_pct_red: 15 },              // still default
    };
    const pruned = pruneAdvisoryParams(raw, buildParamDefaults(CATALOG));
    expect(pruned).toEqual({ vmc_cruise: { ovc_pct_red: 40 } });
  });

  it('keeps params with no known catalog default (unknown key is never pruned)', () => {
    const raw = { mystery: { some_key: 7 } };
    const pruned = pruneAdvisoryParams(raw, buildParamDefaults(CATALOG));
    expect(pruned).toEqual({ mystery: { some_key: 7 } });
  });
});

describe('pruneEngineMethod', () => {
  it('maps a method equal to its default to null ("follow the default")', () => {
    expect(pruneEngineMethod('ogimet_nwp', 'ogimet_nwp')).toBeNull();
    expect(pruneEngineMethod('square_nwp', 'square_nwp')).toBeNull();
    expect(pruneEngineMethod('nwp', 'nwp')).toBeNull();
  });

  it('keeps a method that differs from its default', () => {
    expect(pruneEngineMethod('ogimet_dd', 'ogimet_nwp')).toBe('ogimet_dd');
    expect(pruneEngineMethod('soft_nwp', 'square_nwp')).toBe('soft_nwp');
    expect(pruneEngineMethod('thermo', 'nwp')).toBe('thermo');
  });

  it('keeps the value when no default is known', () => {
    expect(pruneEngineMethod('ogimet_nwp', undefined)).toBe('ogimet_nwp');
  });
});
