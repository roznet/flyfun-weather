/** Tests for the advisory highlight mechanism (#373): the representative-model
 *  helper, the derive-from-state selector, and the store clearing rules.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  advisoryMethodOverrides,
  representativeModel,
  deriveHighlights,
  findAdvisory,
  HIGHLIGHT_LAYER_ID,
} from '../../ts/visualization/cross-section/advisory-highlights';
import type {
  AdvisoryHighlights,
  ModelAdvisoryResult,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../../ts/types/advisories';
// Store actions dispatch a `theme-changed` window event; stub just enough of the
// browser globals for the node test env (mirrors url-state.test.ts's approach).
const g = globalThis as unknown as { window?: { dispatchEvent: () => boolean }; Event?: unknown };
g.window = g.window ?? { dispatchEvent: () => true };
if (!g.window.dispatchEvent) g.window.dispatchEvent = () => true;
if (typeof g.Event === 'undefined') g.Event = class { constructor(public type: string) {} };

import { briefingStore } from '../../ts/store/briefing-store';

// --- fixtures ---------------------------------------------------------------

const HL: AdvisoryHighlights = {
  ribbon: [{ dist_from_nm: 0, dist_to_nm: 100, severity: 'amber' }],
  regions: [{ dist_from_nm: 20, dist_to_nm: 60, base_ft: 6000, top_ft: 12000, kind: 'cruise_imc', severity: 'amber' }],
  peak_dist_nm: 40,
};

function model(
  name: string,
  status: ModelAdvisoryResult['status'],
  highlights: AdvisoryHighlights | null = null,
): ModelAdvisoryResult {
  return {
    model: name, status, detail: '', affected_points: 0, total_points: 10,
    affected_pct: 0, affected_nm: 0, total_nm: 100, highlights,
  };
}

function advisory(
  id: string,
  aggStatus: RouteAdvisoryResult['aggregate_status'],
  perModel: ModelAdvisoryResult[],
  representativeModel?: string | null,
): RouteAdvisoryResult {
  return {
    advisory_id: id, aggregate_status: aggStatus, aggregate_detail: '',
    per_model: perModel, parameters_used: {},
    ...(representativeModel !== undefined ? { representative_model: representativeModel } : {}),
  };
}

function manifest(advisories: RouteAdvisoryResult[]): RouteAdvisoriesManifest {
  return {
    advisories, catalog: [], route_name: 'T', cruise_altitude_ft: 8000,
    flight_ceiling_ft: 18000, total_distance_nm: 100, models: [], airport_conditions: null,
  };
}

// --- representative model ----------------------------------------------------

describe('representativeModel', () => {
  it('reads the backend representative_model field verbatim (#393)', () => {
    const adv = advisory('vmc_cruise', 'amber', [
      model('gfs', 'green'), model('ecmwf', 'amber'), model('icon', 'red'),
    ], 'ecmwf');
    expect(representativeModel(adv)).toBe('ecmwf');
  });

  it('trusts the backend field even when it differs from a naive status match', () => {
    // The client no longer recomputes the rule — whatever the server picked wins.
    const adv = advisory('vmc_cruise', 'amber', [model('gfs', 'green'), model('ecmwf', 'amber')], 'gfs');
    expect(representativeModel(adv)).toBe('gfs');
  });

  it('falls back to the first per-model entry on old packs (no field)', () => {
    const adv = advisory('vmc_cruise', 'red', [model('gfs', 'green'), model('ecmwf', 'amber')]);
    expect(representativeModel(adv)).toBe('gfs');
  });

  it('returns null when there are no per-model entries', () => {
    expect(representativeModel(advisory('vmc_cruise', 'green', []))).toBeNull();
  });
});

// --- derive-from-state selector ---------------------------------------------

describe('deriveHighlights', () => {
  const m = manifest([
    advisory('vmc_cruise', 'amber', [model('gfs', 'amber', HL), model('ecmwf', 'green', null)]),
  ]);

  it('returns the per-model highlights for the selected model', () => {
    expect(deriveHighlights(m, 'vmc_cruise', 'gfs')).toBe(HL);
  });

  it('returns null when the model has no highlights (e.g. clean model)', () => {
    expect(deriveHighlights(m, 'vmc_cruise', 'ecmwf')).toBeNull();
  });

  it('returns null for a model with no per-model entry', () => {
    expect(deriveHighlights(m, 'vmc_cruise', 'icon')).toBeNull();
  });

  it('returns null when the advisory no longer exists (recalc dropped it)', () => {
    expect(deriveHighlights(m, 'convective', 'gfs')).toBeNull();
  });

  it('returns null on an old pack (no highlights field anywhere)', () => {
    const old = manifest([advisory('vmc_cruise', 'amber', [model('gfs', 'amber')])]);
    expect(deriveHighlights(old, 'vmc_cruise', 'gfs')).toBeNull();
  });

  it('returns null when no advisory id is tracked', () => {
    expect(deriveHighlights(m, null, 'gfs')).toBeNull();
    expect(deriveHighlights(null, 'vmc_cruise', 'gfs')).toBeNull();
  });

  it('findAdvisory locates by id and returns null on miss', () => {
    expect(findAdvisory(m, 'vmc_cruise')?.advisory_id).toBe('vmc_cruise');
    expect(findAdvisory(m, 'nope')).toBeNull();
    expect(findAdvisory(null, 'vmc_cruise')).toBeNull();
  });
});

// --- store clearing rules ----------------------------------------------------

describe('store clearing rules', () => {
  const store = briefingStore;

  beforeEach(() => {
    // Reset the highlight-relevant slice of vizSettings before each case.
    const s = store.getState();
    store.setState({
      vizSettings: {
        ...s.vizSettings,
        activeHighlightAdvisoryId: null,
        activePreset: null,
        enabledLayers: { ...s.vizSettings.enabledLayers, [HIGHLIGHT_LAYER_ID]: false },
      },
    });
  });

  it('setHighlightAdvisory sets the id and force-enables the Highlight toggle', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    const vs = store.getState().vizSettings;
    expect(vs.activeHighlightAdvisoryId).toBe('vmc_cruise');
    expect(vs.enabledLayers[HIGHLIGHT_LAYER_ID]).toBe(true);
  });

  it('toggling the Highlight layer does NOT clear the highlight or the preset', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    store.setState({ vizSettings: { ...store.getState().vizSettings, activePreset: 'clouds' } });
    store.getState().toggleVizLayer(HIGHLIGHT_LAYER_ID);  // off
    let vs = store.getState().vizSettings;
    expect(vs.activeHighlightAdvisoryId).toBe('vmc_cruise');
    expect(vs.activePreset).toBe('clouds');
    expect(vs.enabledLayers[HIGHLIGHT_LAYER_ID]).toBe(false);
    store.getState().toggleVizLayer(HIGHLIGHT_LAYER_ID);  // on again
    vs = store.getState().vizSettings;
    expect(vs.activeHighlightAdvisoryId).toBe('vmc_cruise');
    expect(vs.enabledLayers[HIGHLIGHT_LAYER_ID]).toBe(true);
  });

  it('toggling any OTHER layer clears the highlight (manual lens edit)', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    store.getState().toggleVizLayer('cat-bands');
    expect(store.getState().vizSettings.activeHighlightAdvisoryId).toBeNull();
  });

  it('markVizCustom clears the highlight', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    store.setState({ vizSettings: { ...store.getState().vizSettings, activePreset: 'clouds' } });
    store.getState().markVizCustom();
    expect(store.getState().vizSettings.activeHighlightAdvisoryId).toBeNull();
  });

  it('applyAdvisoryPreset clears the highlight (bare dropdown lens)', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    store.getState().applyAdvisoryPreset('clouds', {});
    expect(store.getState().vizSettings.activeHighlightAdvisoryId).toBeNull();
    expect(store.getState().vizSettings.activePreset).toBe('clouds');
  });

  it('setVizPreset(null) clears the highlight', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    store.getState().setVizPreset(null);
    expect(store.getState().vizSettings.activeHighlightAdvisoryId).toBeNull();
  });

  it('setSelectedModel does NOT clear the highlight (it re-derives)', () => {
    store.getState().setHighlightAdvisory('vmc_cruise');
    store.getState().setSelectedModel('ecmwf');
    expect(store.getState().vizSettings.activeHighlightAdvisoryId).toBe('vmc_cruise');
  });
});

describe('advisoryMethodOverrides — show the config the advisory was graded under', () => {
  const adv = (id: string, model: string, method: string | null) => ({
    advisory_id: id,
    aggregate_status: 'amber',
    aggregate_detail: '',
    representative_model: model,
    per_model: [{ model, status: 'amber', detail: '', primary_method_id: method }],
    parameters_used: {},
  }) as any;

  it('overrides the icing axis with the method the evidence came from', () => {
    // User has SFIP selected, but this model fell back to Ogimet-NWP.
    const prefs = { clouds: 'soft_dd', icing: 'sfip_nwp', convection: 'nwp' };
    const out = advisoryMethodOverrides(adv('icing_escape', 'gfs', 'ogimet_nwp'), 'gfs', prefs);
    expect(out.icing).toBe('ogimet_nwp');
    // other axes untouched
    expect(out.clouds).toBe('soft_dd');
    expect(out.convection).toBe('nwp');
  });

  it('keeps the user cloud STYLE and swaps only the source', () => {
    const prefs = { clouds: 'square_dd', icing: 'sfip_nwp' };
    const out = advisoryMethodOverrides(adv('vmc_cruise', 'ecmwf', 'nwp'), 'ecmwf', prefs);
    expect(out.clouds).toBe('square_nwp');   // NOT bare 'nwp' — style preserved
  });

  it('treats nwp_synthesized as the nwp cloud band', () => {
    const prefs = { clouds: 'soft_dd' };
    const out = advisoryMethodOverrides(adv('cloud_top', 'gfs', 'nwp_synthesized'), 'gfs', prefs);
    expect(out.clouds).toBe('soft_nwp');
  });

  it('handles a style-less cloud pref', () => {
    const out = advisoryMethodOverrides(adv('vmc_cruise', 'gfs', 'dd'), 'gfs', { clouds: 'nwp' });
    expect(out.clouds).toBe('dd');
  });

  it('overrides the convection axis (thermo fallback)', () => {
    const prefs = { convection: 'nwp' };
    const out = advisoryMethodOverrides(adv('convective', 'gfs', 'thermo'), 'gfs', prefs);
    expect(out.convection).toBe('thermo');
  });

  it('changes nothing for an advisory with no method axis', () => {
    const prefs = { clouds: 'soft_dd', icing: 'sfip_nwp' };
    expect(advisoryMethodOverrides(adv('turbulence', 'gfs', null), 'gfs', prefs)).toEqual(prefs);
    expect(advisoryMethodOverrides(adv('mountain_wind', 'gfs', null), 'gfs', prefs)).toEqual(prefs);
  });

  it('changes nothing when the advisory carries no method (GREEN grade)', () => {
    const prefs = { icing: 'sfip_nwp' };
    expect(advisoryMethodOverrides(adv('icing_escape', 'gfs', null), 'gfs', prefs)).toEqual(prefs);
  });

  it('uses the method of the model actually being shown', () => {
    const a = {
      advisory_id: 'icing_escape',
      representative_model: 'gfs',
      per_model: [
        { model: 'gfs', status: 'amber', detail: '', primary_method_id: 'ogimet_nwp' },
        { model: 'ecmwf', status: 'amber', detail: '', primary_method_id: 'sfip_nwp' },
      ],
    } as any;
    expect(advisoryMethodOverrides(a, 'ecmwf', { icing: 'ogimet_dd' }).icing).toBe('sfip_nwp');
  });
});
