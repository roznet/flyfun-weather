/** Tests for the advisory highlight mechanism (#373): the representative-model
 *  helper, the derive-from-state selector, and the store clearing rules.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  advisoryMethodOverrides,
  peakPointPosition,
  deriveGradedMethods,
  representativeModel,
  deriveHighlights,
  findAdvisory,
  ribbonSeverityAt,
  reasonCodeAt,
  reasonLabelKey,
  HIGHLIGHT_LAYER_ID,
} from '../../ts/visualization/cross-section/advisory-highlights';
import type {
  AdvisoryHighlights,
  HighlightRegion,
  ModelAdvisoryResult,
  RibbonSegment,
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

  it('resolves the clouds axis to a bare source (render style is client-only now)', () => {
    // #410: the profile/advisory carries only a source; the render style lives in
    // vizSettings.cloudStyle and is fused at layer-composition time.
    const prefs = { clouds: 'dd', icing: 'sfip_nwp' };
    const out = advisoryMethodOverrides(adv('vmc_cruise', 'ecmwf', 'nwp'), 'ecmwf', prefs);
    expect(out.clouds).toBe('nwp');
  });

  it('treats nwp_synthesized as the nwp cloud source', () => {
    const prefs = { clouds: 'dd' };
    const out = advisoryMethodOverrides(adv('cloud_top', 'gfs', 'nwp_synthesized'), 'gfs', prefs);
    expect(out.clouds).toBe('nwp');
  });

  it('keeps a dd source as dd', () => {
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

describe('peakPointPosition — jump to the worst point (#223)', () => {
  const ANALYSES = [
    { distance_from_origin_nm: 0 },
    { distance_from_origin_nm: 25 },
    { distance_from_origin_nm: 50 },
    { distance_from_origin_nm: 75 },
    { distance_from_origin_nm: 100 },
  ];
  const adv = (model: string, peak: number | null) => ({
    advisory_id: 'convective',
    representative_model: model,
    per_model: [{
      model, status: 'red', detail: '',
      highlights: { ribbon: [], regions: [], peak_dist_nm: peak },
    }],
  }) as any;

  it('picks the route point nearest the backend peak', () => {
    // peak at 52 nm -> nearest analysis is index 2 (50 nm), not 3 (75 nm)
    expect(peakPointPosition(adv('gfs', 52), 'gfs', ANALYSES)).toBe(2);
  });

  it('resolves an exact match', () => {
    expect(peakPointPosition(adv('gfs', 75), 'gfs', ANALYSES)).toBe(3);
  });

  it('clamps to the ends', () => {
    expect(peakPointPosition(adv('gfs', -10), 'gfs', ANALYSES)).toBe(0);
    expect(peakPointPosition(adv('gfs', 999), 'gfs', ANALYSES)).toBe(4);
  });

  it('returns null with no peak — a GREEN advisory has nothing to point at', () => {
    // The cursor must be left alone rather than moved somewhere arbitrary.
    expect(peakPointPosition(adv('gfs', null), 'gfs', ANALYSES)).toBeNull();
  });

  it('returns null when the model has no highlights', () => {
    const a = { advisory_id: 'x', per_model: [{ model: 'gfs', status: 'green', detail: '' }] } as any;
    expect(peakPointPosition(a, 'gfs', ANALYSES)).toBeNull();
  });

  it('uses the peak of the model being shown, not another', () => {
    const a = {
      advisory_id: 'convective',
      representative_model: 'gfs',
      per_model: [
        { model: 'gfs', status: 'red', detail: '', highlights: { ribbon: [], regions: [], peak_dist_nm: 10 } },
        { model: 'ecmwf', status: 'red', detail: '', highlights: { ribbon: [], regions: [], peak_dist_nm: 90 } },
      ],
    } as any;
    expect(peakPointPosition(a, 'ecmwf', ANALYSES)).toBe(4);   // 90 -> 100nm
    expect(peakPointPosition(a, 'gfs', ANALYSES)).toBe(0);     // 10 -> 0nm
  });

  it('returns null with no analyses', () => {
    expect(peakPointPosition(adv('gfs', 50), 'gfs', [])).toBeNull();
  });
});

describe('deriveGradedMethods — graded methods come from the manifest, not account prefs (#410)', () => {
  const DEFAULTS = { cloud_source: 'nwp', icing_method: 'ogimet_nwp', convective_method: 'nwp' };
  const manifest = (advisories: any[]) => ({ advisories } as any);
  const adv = (id: string, model: string, method: string | null) => ({
    advisory_id: id,
    representative_model: model,
    per_model: [{ model, status: 'amber', detail: '', primary_method_id: method }],
  });

  it('null manifest falls back to the catalog engine-method defaults', () => {
    expect(deriveGradedMethods(null, DEFAULTS)).toEqual({
      clouds: 'nwp', icing: 'ogimet_nwp', convection: 'nwp',
    });
  });

  it('a DD-graded profile yields DD cloud / DD icing / thermo convective', () => {
    // The core acceptance criterion: compact mode must pick the DD layers.
    const out = deriveGradedMethods(manifest([
      adv('vmc_cruise', 'gfs', 'dd'),
      adv('icing_escape', 'gfs', 'ogimet_dd'),
      adv('convective', 'gfs', 'thermo'),
    ]), DEFAULTS);
    expect(out).toEqual({ clouds: 'dd', icing: 'ogimet_dd', convection: 'thermo' });
  });

  it('collapses nwp_synthesized to a bare nwp cloud source', () => {
    const out = deriveGradedMethods(manifest([adv('cloud_top', 'gfs', 'nwp_synthesized')]), DEFAULTS);
    expect(out.clouds).toBe('nwp');
  });

  it('uses the representative model per-model method', () => {
    const out = deriveGradedMethods(manifest([{
      advisory_id: 'icing_escape',
      representative_model: 'ecmwf',
      per_model: [
        { model: 'gfs', status: 'amber', detail: '', primary_method_id: 'ogimet_dd' },
        { model: 'ecmwf', status: 'amber', detail: '', primary_method_id: 'sfip_nwp' },
      ],
    }]), DEFAULTS);
    expect(out.icing).toBe('sfip_nwp');
  });

  it('falls back to defaults for groups the pack has no signal for', () => {
    // Only an icing advisory present, and it carries no method (GREEN) → all
    // three groups keep the catalog defaults.
    const out = deriveGradedMethods(manifest([adv('icing_escape', 'gfs', null)]), DEFAULTS);
    expect(out).toEqual({ clouds: 'nwp', icing: 'ogimet_nwp', convection: 'nwp' });
  });

  it('honours a DD default cloud source when the pack is silent', () => {
    const out = deriveGradedMethods(null, { ...DEFAULTS, cloud_source: 'dd' });
    expect(out.clouds).toBe('dd');
  });
});

// --- ribbon-hover verdict/reason lookups (#412) ------------------------------

describe('ribbonSeverityAt', () => {
  const ribbon: RibbonSegment[] = [
    { dist_from_nm: 0, dist_to_nm: 30, severity: 'green' },
    { dist_from_nm: 30, dist_to_nm: 70, severity: 'amber' },
    { dist_from_nm: 70, dist_to_nm: 100, severity: 'red' },
  ];

  it('resolves a distance to the segment covering it', () => {
    expect(ribbonSeverityAt(ribbon, 10)).toBe('green');
    expect(ribbonSeverityAt(ribbon, 50)).toBe('amber');
    expect(ribbonSeverityAt(ribbon, 85)).toBe('red');
  });

  it('assigns a boundary to the segment whose half-open [from, to) owns it', () => {
    // 30 nm belongs to the amber run [30, 70), not the green run that ends at 30.
    expect(ribbonSeverityAt(ribbon, 30)).toBe('amber');
    expect(ribbonSeverityAt(ribbon, 70)).toBe('red');
  });

  it('includes the final segment right edge (the arrival point)', () => {
    expect(ribbonSeverityAt(ribbon, 100)).toBe('red');
  });

  it('returns null outside the ribbon range and for an empty ribbon', () => {
    expect(ribbonSeverityAt(ribbon, 150)).toBeNull();
    expect(ribbonSeverityAt(ribbon, -1)).toBeNull();
    expect(ribbonSeverityAt([], 10)).toBeNull();
  });
});

describe('reasonCodeAt', () => {
  const regions: HighlightRegion[] = [
    { dist_from_nm: 20, dist_to_nm: 40, kind: 'icing_band', severity: 'amber', reason_code: 'tight_margin' },
    { dist_from_nm: 60, dist_to_nm: 90, kind: 'icing_band', severity: 'red', reason_code: 'no_escape' },
  ];

  it('returns the reason_code of the region covering the distance', () => {
    expect(reasonCodeAt(regions, 30)).toBe('tight_margin');
    expect(reasonCodeAt(regions, 75)).toBe('no_escape');
  });

  it('returns null where no flagged region covers the distance', () => {
    expect(reasonCodeAt(regions, 50)).toBeNull();
    expect(reasonCodeAt([], 30)).toBeNull();
  });

  it('ignores regions that carry no reason_code (their reason IS the definition)', () => {
    const noCode: HighlightRegion[] = [
      { dist_from_nm: 0, dist_to_nm: 100, kind: 'cruise_imc', severity: 'red' },
    ];
    expect(reasonCodeAt(noCode, 50)).toBeNull();
  });

  it('prefers the most severe region when several with codes overlap', () => {
    const overlap: HighlightRegion[] = [
      { dist_from_nm: 0, dist_to_nm: 100, kind: 'a', severity: 'amber', reason_code: 'transit_exposure' },
      { dist_from_nm: 40, dist_to_nm: 60, kind: 'b', severity: 'red', reason_code: 'sld' },
    ];
    expect(reasonCodeAt(overlap, 50)).toBe('sld');
    expect(reasonCodeAt(overlap, 20)).toBe('transit_exposure');
  });

  it('matches the reason to the displayed verdict at a touching RED/AMBER boundary', () => {
    // Adjacent same-kind runs of differing severity share an exact boundary by
    // construction (build_regions/build_ribbon cut from the same midpoints). At
    // the boundary the ribbon assigns the point to the later (amber) segment, so
    // the reason must be the amber one — not the red region's, which an
    // unfiltered inclusive-both-ends test would pick via the severity tie-break.
    const touching: HighlightRegion[] = [
      { dist_from_nm: 0, dist_to_nm: 50, kind: 'icing_band', severity: 'red', reason_code: 'no_escape' },
      { dist_from_nm: 50, dist_to_nm: 100, kind: 'icing_band', severity: 'amber', reason_code: 'tight_margin' },
    ];
    // Ribbon gives amber at the shared boundary (50) → reason must be amber's.
    expect(ribbonSeverityAt([
      { dist_from_nm: 0, dist_to_nm: 50, severity: 'red' },
      { dist_from_nm: 50, dist_to_nm: 100, severity: 'amber' },
    ], 50)).toBe('amber');
    expect(reasonCodeAt(touching, 50, 'amber')).toBe('tight_margin');
    // Interiors resolve to their own region's reason under the matching verdict.
    expect(reasonCodeAt(touching, 25, 'red')).toBe('no_escape');
    expect(reasonCodeAt(touching, 75, 'amber')).toBe('tight_margin');
    // A verdict with no matching-severity region under the cursor → no reason
    // (just the verdict), never a mismatched one.
    expect(reasonCodeAt(touching, 25, 'amber')).toBeNull();
  });
});

describe('reasonLabelKey', () => {
  it('maps every known code to its i18n key', () => {
    for (const code of [
      'no_escape', 'no_escape_unknown', 'tight_margin', 'warm_escape',
      'sld', 'severe_icing', 'thick_transit', 'icing_at_cruise', 'transit_exposure',
      'active_track', 'thermo_floor',
    ]) {
      expect(reasonLabelKey(code)).toBe(`advisories.reason.${code}`);
    }
  });

  it('returns null for an unknown or absent code (show just the verdict)', () => {
    expect(reasonLabelKey('cruise_imc')).toBeNull();
    expect(reasonLabelKey(null)).toBeNull();
    expect(reasonLabelKey(undefined)).toBeNull();
    expect(reasonLabelKey('')).toBeNull();
  });
});
