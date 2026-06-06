/** Tests for advisory-aware cross-section presets (#219): config integrity,
 *  method resolution, clean-slate behaviour, and the FIKI per-advisory override.
 */

import { describe, it, expect } from 'vitest';
import {
  ADVISORY_PRESETS, ADVISORY_TO_PRESET,
  getAdvisoryPreset, getAdvisoryPresets, isAdvisoryPreset,
  getPresetForAdvisory, resolveAdvisoryPreset,
} from '../../ts/visualization/cross-section/advisory-presets';
import { getAllLayers } from '../../ts/visualization/cross-section/layer-registry';

const ALL_IDS = new Set(getAllLayers().map((l) => l.id));

describe('config integrity', () => {
  it('has the six Phase-1 presets', () => {
    expect(Object.keys(ADVISORY_PRESETS).sort())
      .toEqual(['clouds', 'convective', 'icing', 'ifr', 'turbulence', 'vfr']);
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
    // explicit line + always-on context
    expect(en['freezing-level']).toBe(true);
    expect(en['terrain']).toBe(true);
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
});
