import { describe, it, expect } from 'vitest';
import {
  buildParamDefaults,
  pruneAdvisoryParams,
  pruneEngineMethod,
  renameExtentParams,
} from '../../ts/helpers/profile-sparsify';
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
  entry('vmc_cruise', [['extent_pct_amber', 30], ['extent_pct_red', 50]]),
  entry('icing_escape', [['extent_pct_red', 15]]),
];

describe('pruneAdvisoryParams', () => {
  it('drops every param equal to its catalog default (all-default → empty)', () => {
    const raw = {
      vmc_cruise: { extent_pct_amber: 30, extent_pct_red: 50 },
      icing_escape: { extent_pct_red: 15 },
    };
    const pruned = pruneAdvisoryParams(raw, buildParamDefaults(CATALOG));
    expect(pruned).toEqual({});
  });

  it('keeps exactly the one param moved off its default', () => {
    const raw = {
      vmc_cruise: { extent_pct_amber: 30, extent_pct_red: 40 },  // ovc moved 50 → 40
      icing_escape: { extent_pct_red: 15 },              // still default
    };
    const pruned = pruneAdvisoryParams(raw, buildParamDefaults(CATALOG));
    expect(pruned).toEqual({ vmc_cruise: { extent_pct_red: 40 } });
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

describe('renameExtentParams', () => {
  it('rewrites the old keys to the consolidated ones', () => {
    const out = renameExtentParams({
      vmc_cruise: { bkn_pct_amber: 30, ovc_pct_red: 40 },
      vfr_feasibility: { imc_pct_amber: 10, imc_pct_red: 20 },
    });
    expect(out).toEqual({
      vmc_cruise: { extent_pct_amber: 30, extent_pct_red: 40 },
      vfr_feasibility: { extent_pct_amber: 10, extent_pct_red: 20 },
    });
  });

  it('inverts fiki_icing, which stored a percentage of the CLEAR cruise', () => {
    // "amber below 70% clear" means "amber at or above 30% affected".
    const out = renameExtentParams({
      fiki_icing: { clear_cruise_amber_pct: 70, clear_cruise_red_pct: 40 },
    });
    expect(out).toEqual({
      fiki_icing: { extent_pct_amber: 30, extent_pct_red: 60 },
    });
  });

  it('applies a secondary alias only when the primary name is absent', () => {
    // icing_escape's read path preferred the primary and fell back to the
    // alias; migrating must resolve the same way.
    const out = renameExtentParams({
      icing_escape: { icing_coverage_pct_amber: 25, route_pct_amber: 99 },
    });
    expect(out).toEqual({ icing_escape: { extent_pct_amber: 25 } });
  });

  it('lets a value already under the new key win', () => {
    const out = renameExtentParams({
      turbulence: { extent_pct_amber: 40, route_pct_amber: 15 },
    });
    expect(out).toEqual({ turbulence: { extent_pct_amber: 40 } });
  });

  it('leaves unrelated params and advisories untouched, and does not mutate', () => {
    const raw = {
      airport_wind: { crosswind_red_kt: 25 },
      enroute_precip: { snow_pct_amber: 8, rain_pct_amber: 40 },
    };
    const snapshot = JSON.parse(JSON.stringify(raw));
    const out = renameExtentParams(raw);
    expect(out).toEqual({
      airport_wind: { crosswind_red_kt: 25 },
      enroute_precip: { extent_pct_amber: 8, rain_pct_amber: 40 },
    });
    expect(raw).toEqual(snapshot);
  });

  it('uses the secondary alias when no primary key is present', () => {
    // The precedence path the Python side tests but this suite did not: with
    // no primary key stored, icing_escape's read-path aliases must still
    // migrate, or the pilot's tuning is silently dropped (#571 review).
    expect(renameExtentParams({ icing_escape: { min_route_pct: 8 } }))
      .toEqual({ icing_escape: { extent_pct_red: 8 } });
    expect(renameExtentParams({ icing_escape: { route_pct_amber: 25 } }))
      .toEqual({ icing_escape: { extent_pct_amber: 25 } });
  });

  it('migrates turbulence\'s red threshold, not just its amber one', () => {
    // `route_pct_red` was missing from the rename map on both sides (#571
    // review) — the key existed, so a stored value would have gone inert.
    expect(renameExtentParams({ turbulence: { route_pct_red: 65 } }))
      .toEqual({ turbulence: { extent_pct_red: 65 } });
  });

  it('is a no-op on an already-migrated profile', () => {
    const raw = { vmc_cruise: { extent_pct_amber: 30 } };
    expect(renameExtentParams(raw)).toEqual(raw);
  });
});
