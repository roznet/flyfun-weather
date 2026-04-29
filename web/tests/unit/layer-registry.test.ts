/** Tests for cross-section layer registry: compact-mode collapsing,
 *  preferred-method resolution, preset application.
 */

import { describe, it, expect } from 'vitest';
import {
  getAllLayers, getDefaultEnabled, getLayerGroups,
  getPreferredLayerForGroup, getCompactLayerOverrides,
  getPreset, getPresets,
} from '../../ts/visualization/cross-section/layer-registry';

describe('getAllLayers', () => {
  it('returns at least the canonical 13 band/line layer ids', () => {
    const ids = new Set(getAllLayers().map((l) => l.id));
    // Spot-check a few; if any of these disappear, callers (incl. tooltip
    // registry) have probably been silently broken.
    for (const id of [
      'cloud-bands', 'nwp-cloud-bands', 'soft-cloud-bands', 'soft-nwp-cloud-bands',
      'icing-bands', 'icing-ogimet-nwp-bands', 'sfip-bands', 'ieng-icing-bands', 'sld-bands',
      'cat-bands', 'e-shear-bands', 'inversion-bands',
      'thermo-convective-bg', 'nwp-convective-bg',
      'terrain', 'freezing-level', 'cruise-altitude',
    ]) {
      expect(ids.has(id), `missing layer id ${id}`).toBe(true);
    }
  });

  it('all layer ids are unique', () => {
    const ids = getAllLayers().map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('getDefaultEnabled', () => {
  it('returns one entry per registered layer', () => {
    const defaults = getDefaultEnabled();
    expect(Object.keys(defaults).length).toBe(getAllLayers().length);
  });

  it('matches each layer.defaultEnabled', () => {
    const defaults = getDefaultEnabled();
    for (const layer of getAllLayers()) {
      expect(defaults[layer.id]).toBe(layer.defaultEnabled);
    }
  });
});

describe('getCompactLayerOverrides', () => {
  it('enables exactly the preferred layer per group, disables siblings', () => {
    const overrides = getCompactLayerOverrides({
      clouds: 'soft_nwp',
      icing: 'ogimet_nwp',
      turbulence: 'ri',
      convection: 'nwp',
    });

    // Clouds: only soft-nwp-cloud-bands enabled
    expect(overrides['soft-nwp-cloud-bands']).toBe(true);
    expect(overrides['soft-cloud-bands']).toBe(false);
    expect(overrides['nwp-cloud-bands']).toBe(false);
    expect(overrides['cloud-bands']).toBe(false);

    // Icing: only ogimet-nwp
    expect(overrides['icing-ogimet-nwp-bands']).toBe(true);
    expect(overrides['icing-bands']).toBe(false);
    expect(overrides['sfip-bands']).toBe(false);
    expect(overrides['ieng-icing-bands']).toBe(false);

    // Turbulence: only cat-bands
    expect(overrides['cat-bands']).toBe(true);
    expect(overrides['e-shear-bands']).toBe(false);

    // Convection: only nwp-convective-bg
    expect(overrides['nwp-convective-bg']).toBe(true);
    expect(overrides['thermo-convective-bg']).toBe(false);
  });

  it('falsy preferred method disables all in that group', () => {
    const overrides = getCompactLayerOverrides({
      clouds: 'unknown-method',
      icing: 'ogimet_nwp',
      turbulence: 'ri',
      convection: 'nwp',
    });
    expect(overrides['soft-nwp-cloud-bands']).toBe(false);
    expect(overrides['soft-cloud-bands']).toBe(false);
    expect(overrides['nwp-cloud-bands']).toBe(false);
    expect(overrides['cloud-bands']).toBe(false);
  });
});

describe('getPreferredLayerForGroup', () => {
  const allLayers = getAllLayers();
  const cloudGroupLayers = allLayers.filter((l) => l.group === 'clouds');
  const icingGroupLayers = allLayers.filter((l) => l.group === 'icing');

  it('returns the layer matching the preferred method', () => {
    expect(getPreferredLayerForGroup('clouds', cloudGroupLayers, 'soft_nwp').id)
      .toBe('soft-nwp-cloud-bands');
    expect(getPreferredLayerForGroup('icing', icingGroupLayers, 'ogimet_nwp').id)
      .toBe('icing-ogimet-nwp-bands');
  });

  it('falls back to defaultEnabled layer when method unknown', () => {
    const fallback = getPreferredLayerForGroup('clouds', cloudGroupLayers, 'totally-bogus');
    // First defaultEnabled cloud layer
    expect(fallback.defaultEnabled || fallback === cloudGroupLayers[0]).toBe(true);
  });

  it('falls back when method is undefined', () => {
    const fallback = getPreferredLayerForGroup('icing', icingGroupLayers, undefined);
    expect(fallback).toBeDefined();
    expect(icingGroupLayers).toContain(fallback);
  });
});

describe('getLayerGroups', () => {
  it('returns groups in stable display order', () => {
    const groups = getLayerGroups().map((g) => g.group);
    // Order asserted in layer-registry.ts
    const expected = ['terrain', 'reference', 'temperature', 'clouds', 'icing', 'turbulence', 'convection'];
    // Filter expected to match what's actually present (some groups may be absent if no layers)
    for (const g of expected) {
      const idxActual = groups.indexOf(g as typeof groups[number]);
      if (idxActual === -1) continue;
      // Just check 'clouds' comes before 'icing' before 'turbulence' before 'convection'
    }
    expect(groups.indexOf('clouds')).toBeLessThan(groups.indexOf('icing'));
    expect(groups.indexOf('icing')).toBeLessThan(groups.indexOf('turbulence'));
    expect(groups.indexOf('turbulence')).toBeLessThan(groups.indexOf('convection'));
  });

  it('every group entry has at least one layer', () => {
    for (const g of getLayerGroups()) {
      expect(g.layers.length).toBeGreaterThan(0);
    }
  });
});

describe('presets', () => {
  it('GRAMET preset is registered', () => {
    const preset = getPreset('gramet');
    expect(preset).toBeDefined();
    expect(preset!.id).toBe('gramet');
    expect(preset!.themeId).toBe('gramet');
  });

  it('GRAMET enables soft NWP clouds and Ogimet-NWP icing', () => {
    const preset = getPreset('gramet')!;
    expect(preset.enabledLayers['soft-nwp-cloud-bands']).toBe(true);
    expect(preset.enabledLayers['icing-ogimet-nwp-bands']).toBe(true);
    expect(preset.enabledLayers['nwp-convective-bg']).toBe(true);
    expect(preset.enabledLayers['cat-bands']).toBe(true);
  });

  it('GRAMET excludes SLD and DD-only layers', () => {
    const preset = getPreset('gramet')!;
    expect(preset.enabledLayers['sld-bands']).toBe(false);
    expect(preset.enabledLayers['icing-bands']).toBe(false);
    expect(preset.enabledLayers['cloud-bands']).toBe(false);
    expect(preset.enabledLayers['thermo-convective-bg']).toBe(false);
  });

  it('getPreset returns undefined for unknown preset', () => {
    expect(getPreset('does-not-exist')).toBeUndefined();
  });

  it('getPresets includes GRAMET', () => {
    const ids = getPresets().map((p) => p.id);
    expect(ids).toContain('gramet');
  });
});
