/** Tests for advisory-aware cross-section presets (#219): config integrity,
 *  method resolution, clean-slate behaviour, and the FIKI per-advisory override.
 */

import { describe, it, expect } from 'vitest';
import {
  ADVISORY_PRESETS, ADVISORY_TO_PRESET, ADVISORY_OVERRIDES,
  getAdvisoryPreset, getAdvisoryPresets, isAdvisoryPreset,
  getPresetForAdvisory, resolveAdvisoryPreset, advisoryPresetInterpretation,
} from '../../ts/visualization/cross-section/advisory-presets';
import { getAllLayers } from '../../ts/visualization/cross-section/layer-registry';
import { ROUTE_GRAPH_METRICS, METRIC_NONE } from '../../ts/visualization/route-graph/metrics';
import { MAP_METRICS, MAP_METRIC_NONE } from '../../ts/visualization/route-map/metrics';
import { SKEWT_OVERLAYS } from '../../ts/visualization/skewt/overlay-bands';
import { VARIABLE_REGISTRY } from '../../ts/visualization/skewt/variable-panel';

const ALL_IDS = new Set(getAllLayers().map((l) => l.id));
const SKEWT_OVERLAY_IDS = new Set(SKEWT_OVERLAYS.map((o) => o.id));
const SKEWT_VAR_IDS = new Set(VARIABLE_REGISTRY.map((v) => v.id));
const ROUTE_GRAPH_IDS = new Set<string>([METRIC_NONE, ...ROUTE_GRAPH_METRICS.map((m) => m.id)]);
const MAP_METRIC_IDS = new Set<string>([MAP_METRIC_NONE, ...MAP_METRICS.map((m) => m.id)]);

describe('config integrity', () => {
  it('has the Phase-1 presets plus the Basic/Learn view (#308)', () => {
    expect(Object.keys(ADVISORY_PRESETS).sort())
      .toEqual(['basic', 'clouds', 'convective', 'icing', 'ifr', 'turbulence', 'vfr']);
  });

  it('each preset id matches its key, and has label + caption', () => {
    for (const [key, p] of Object.entries(ADVISORY_PRESETS)) {
      expect(p.id).toBe(key);
      expect(p.label.length).toBeGreaterThan(0);
      expect(p.caption.length).toBeGreaterThan(0);
    }
  });

  it('every preset.lines id is a real layer', () => {
    for (const p of getAdvisoryPresets()) {
      for (const id of p.lines ?? []) {
        expect(ALL_IDS.has(id), `preset ${p.id} references unknown layer ${id}`).toBe(true);
      }
    }
  });

  it('every ADVISORY_OVERRIDES line id is a real layer', () => {
    // getAdvisoryPresets() only covers the base presets — the per-advisory
    // overrides (e.g. FIKI's extra warm-nose layers) need their own check, or a
    // typo would silently no-op at render time.
    for (const [advId, ovr] of Object.entries(ADVISORY_OVERRIDES)) {
      for (const id of ovr.lines ?? []) {
        expect(ALL_IDS.has(id), `ADVISORY_OVERRIDES[${advId}] references unknown layer ${id}`).toBe(true);
      }
      for (const g of ovr.groups ?? []) {
        expect(getAllLayers().some((l) => l.group === g),
          `ADVISORY_OVERRIDES[${advId}] references empty group ${g}`).toBe(true);
      }
    }
  });

  it('every preset routeGraph metric id is a real route-graph metric', () => {
    for (const p of getAdvisoryPresets()) {
      for (const id of [p.routeGraph?.left, p.routeGraph?.right]) {
        if (id === undefined) continue;
        expect(ROUTE_GRAPH_IDS.has(id), `preset ${p.id} references unknown route-graph metric ${id}`).toBe(true);
      }
    }
  });

  it('every preset map metric id is a real route-map metric', () => {
    for (const p of getAdvisoryPresets()) {
      const id = p.map?.metric;
      if (id === undefined) continue;
      expect(MAP_METRIC_IDS.has(id), `preset ${p.id} references unknown route-map metric ${id}`).toBe(true);
    }
  });

  it('every ADVISORY_TO_PRESET target is a real preset', () => {
    for (const [advId, presetId] of Object.entries(ADVISORY_TO_PRESET)) {
      expect(ADVISORY_PRESETS[presetId], `advisory ${advId} → unknown preset ${presetId}`).toBeDefined();
    }
  });

  it('isAdvisoryPreset distinguishes advisory presets from gramet/null', () => {
    expect(isAdvisoryPreset('icing')).toBe(true);
    expect(isAdvisoryPreset('gramet')).toBe(false);
    expect(isAdvisoryPreset(null)).toBe(false);
    expect(isAdvisoryPreset(undefined)).toBe(false);
  });

  it('opts hazard presets into all focus surfaces and emphasis, but not Basic', () => {
    const surfaces = ['cross-section', 'route-graph', 'route-map'];
    for (const id of ['icing', 'clouds', 'convective', 'turbulence', 'vfr', 'ifr']) {
      const preset = getAdvisoryPreset(id)!;
      expect(preset.highlights).toEqual(surfaces);
      expect(preset.emphasize).toBe(true);
    }
    expect(getAdvisoryPreset('basic')!.highlights).toBeUndefined();
    expect(getAdvisoryPreset('basic')!.emphasize).toBeUndefined();
  });
});

describe('resolveAdvisoryPreset — method resolution', () => {
  it('icing preset enables the method-resolved icing + cloud layer', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('icing')!, {
      clouds: 'soft_nwp', icing: 'sfip_nwp', convection: 'nwp',
    });
    const en = view.enabledLayers!;
    // icing method → sfip-bands on, other icing methods off
    expect(en['sfip-bands']).toBe(true);
    expect(en['icing-ogimet-nwp-bands']).toBe(false);
    expect(en['icing-bands']).toBe(false);
    // clouds method → soft-nwp on, other cloud styles off
    expect(en['soft-nwp-cloud-bands']).toBe(true);
    expect(en['cloud-bands']).toBe(false);
    // explicit line + always-on context. terrain is NOT in the resolved view
    // (force-rendered at draw time); cruise-altitude IS toggle-controlled.
    expect(en['freezing-level']).toBe(true);
    expect('terrain' in en).toBe(false);
    expect(en['cruise-altitude']).toBe(true);
  });

  it('switching icing_method changes which icing layer the preset enables', () => {
    const icing = getAdvisoryPreset('icing')!;
    const ogimet = resolveAdvisoryPreset(icing, { icing: 'ogimet_nwp' }).enabledLayers!;
    const ieng = resolveAdvisoryPreset(icing, { icing: 'ieng' }).enabledLayers!;
    expect(ogimet['icing-ogimet-nwp-bands']).toBe(true);
    expect(ogimet['ieng-icing-bands']).toBe(false);
    expect(ieng['ieng-icing-bands']).toBe(true);
    expect(ieng['icing-ogimet-nwp-bands']).toBe(false);
  });

  it('turbulence preset falls back to cat-bands (no turbulence method key) and re-enables inversions', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('turbulence')!, {});
    const en = view.enabledLayers!;
    expect(en['cat-bands']).toBe(true);
    expect(en['e-shear-bands']).toBe(false);
    // inversion-bands lives in the 'stability' group, re-enabled via `lines`
    expect(en['inversion-bands']).toBe(true);
  });

  it('clean slate: layers in reset groups not named by the preset are turned OFF', () => {
    // clouds preset names only clouds; icing/convection/turbulence bands must be off.
    const en = resolveAdvisoryPreset(getAdvisoryPreset('clouds')!, { clouds: 'soft_nwp' }).enabledLayers!;
    expect(en['icing-ogimet-nwp-bands']).toBe(false);
    expect(en['sfip-bands']).toBe(false);
    expect(en['nwp-convective-bg']).toBe(false);
    expect(en['cat-bands']).toBe(false);
    expect(en['inversion-bands']).toBe(false);
  });

  it('sets companion route-graph and map directives', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('icing')!, {});
    expect(view.routeGraph).toEqual({ left: 'freezing-level', right: 'ceiling-nwp' });
    expect(view.map).toEqual({ metric: 'icing-risk-at-level', altitudeFt: null });
  });

  it('vfr map has no cruise altitude target (level-independent metric)', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('vfr')!, { clouds: 'soft_nwp' });
    expect(view.map?.metric).toBe('nwp-ceiling');
    // altitude not 'cruise' → altitudeFt left undefined (store leaves slider as-is)
    expect(view.map && 'altitudeFt' in view.map ? view.map.altitudeFt : 'absent').toBe('absent');
  });

  it('copies focus surfaces and derives a deduplicated emphasis allow-list', () => {
    const preset = getAdvisoryPreset('icing')!;
    const view = resolveAdvisoryPreset(preset, { clouds: 'soft_nwp', icing: 'sfip_nwp' });
    expect(view.highlightSurfaces).toEqual(['cross-section', 'route-graph', 'route-map']);
    expect(view.highlightSurfaces).not.toBe(preset.highlights);

    const expected = new Set([
      ...Object.entries(view.enabledLayers!)
        .filter(([, enabled]) => enabled)
        .map(([id]) => id),
      'terrain',
      'cruise-altitude',
    ]);
    expect(new Set(view.emphasizeLayers)).toEqual(expected);
    expect(view.emphasizeLayers?.filter((id) => id === 'terrain')).toHaveLength(1);
    expect(view.emphasizeLayers?.filter((id) => id === 'cruise-altitude')).toHaveLength(1);
  });

  it('does not emit focus directives for the Basic preset', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('basic')!, {});
    expect(view.highlightSurfaces).toBeUndefined();
    expect(view.emphasizeLayers).toBeUndefined();
  });
});

describe('getPresetForAdvisory — chip resolution + FIKI override', () => {
  it('maps both icing advisories to the icing preset', () => {
    expect(getPresetForAdvisory('icing_escape')!.id).toBe('icing');
    expect(getPresetForAdvisory('fiki_icing')!.id).toBe('icing');
  });

  it('FIKI unions extra warm-nose lines onto the shared icing preset', () => {
    const fiki = getPresetForAdvisory('fiki_icing')!;
    // base icing line preserved + FIKI extras added
    expect(fiki.lines).toContain('freezing-level');
    expect(fiki.lines).toContain('minus-10c');
    expect(fiki.lines).toContain('minus-20c');
    expect(fiki.lines).toContain('sld-bands');
    // identity stays the base preset so the dropdown reflects 'icing'
    expect(fiki.id).toBe('icing');

    // plain icing_escape does NOT get the FIKI extras
    const plain = getPresetForAdvisory('icing_escape')!;
    expect(plain.lines).not.toContain('sld-bands');
  });

  it('resolving the FIKI preset enables the warm-nose layers', () => {
    const en = resolveAdvisoryPreset(getPresetForAdvisory('fiki_icing')!, { icing: 'ogimet_nwp' }).enabledLayers!;
    expect(en['minus-10c']).toBe(true);
    expect(en['minus-20c']).toBe(true);
    expect(en['sld-bands']).toBe(true);
    expect(en['icing-ogimet-nwp-bands']).toBe(true);
  });

  it('returns undefined for an advisory with no chip mapping', () => {
    expect(getPresetForAdvisory('flight_category')).toBeUndefined();
    expect(getPresetForAdvisory('airport_wind')).toBeUndefined();
  });

  it('maps freezing precipitation to icing with SLD and freezing-level context', () => {
    const preset = getPresetForAdvisory('freezing_precip')!;
    expect(preset.id).toBe('icing');
    expect(preset.lines).toContain('sld-bands');
    expect(preset.lines).toContain('freezing-level');

    const enabled = resolveAdvisoryPreset(preset, {}).enabledLayers!;
    expect(enabled['sld-bands']).toBe(true);
    expect(enabled['freezing-level']).toBe(true);
  });

  it('does not map model, DD/NWP, airport, or fronts advisories to presets', () => {
    for (const advisoryId of [
      'model_agreement', 'dd_nwp_agreement', 'flight_category', 'airport_wind', 'fronts',
    ]) {
      expect(getPresetForAdvisory(advisoryId)).toBeUndefined();
    }
  });
});

describe('Skew-T directives (#308)', () => {
  it('every preset.skewtOverlays id is a real overlay band', () => {
    for (const p of getAdvisoryPresets()) {
      for (const id of p.skewtOverlays ?? []) {
        expect(SKEWT_OVERLAY_IDS.has(id), `preset ${p.id} references unknown overlay ${id}`).toBe(true);
      }
    }
  });

  it('every preset.skewtSidePanel id is a real side-panel variable', () => {
    for (const p of getAdvisoryPresets()) {
      if (p.skewtSidePanel === undefined) continue;
      expect(SKEWT_VAR_IDS.has(p.skewtSidePanel), `preset ${p.id} references unknown variable ${p.skewtSidePanel}`).toBe(true);
    }
  });

  it('resolves a full clean-slate overlay map (lens bands on, all others off)', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('icing')!, {});
    const ov = view.skewtOverlays!;
    // every known overlay id is present in the resolved map
    for (const id of SKEWT_OVERLAY_IDS) expect(id in ov).toBe(true);
    // icing lens turns on its bands, leaves the unrelated ones off
    expect(ov['icing-nwp']).toBe(true);
    expect(ov['clouds-nwp']).toBe(true);
    expect(ov['inversions']).toBe(false);
    expect(ov['convective']).toBe(false);
  });

  it('convective puts omega (w_fpm) on the side panel and shades the convective band', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('convective')!, {});
    expect(view.skewtSidePanel).toBe('vertical_velocity');
    expect(view.skewtOverlays!['convective']).toBe(true);
  });

  it('Basic/Learn resolves to all overlay bands OFF (no hazard shading)', () => {
    const view = resolveAdvisoryPreset(getAdvisoryPreset('basic')!, {});
    // skewtOverlays is present (empty array → clean slate), every band off
    expect(view.skewtOverlays).toBeDefined();
    for (const id of SKEWT_OVERLAY_IDS) expect(view.skewtOverlays![id]).toBe(false);
    // no side-panel directive — basic leaves the user's choice
    expect(view.skewtSidePanel).toBeUndefined();
  });

  it('interpretation text is non-empty and falls back to caption when absent', () => {
    for (const p of getAdvisoryPresets()) {
      expect(advisoryPresetInterpretation(p).length).toBeGreaterThan(0);
    }
    // a preset with no interpretation literal falls back to its caption
    const stub = { id: 'x', label: 'X', caption: 'fallback caption' };
    expect(advisoryPresetInterpretation(stub)).toBe('fallback caption');
  });
});
