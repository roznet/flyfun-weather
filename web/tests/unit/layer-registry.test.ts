/** Tests for cross-section layer registry: compact-mode collapsing,
 *  preferred-method resolution, preset application.
 */

import { describe, it, expect } from 'vitest';
import {
  getAllLayers, getDefaultEnabled, getLayerGroups,
  getPreferredLayerForGroup, getCompactLayerOverrides,
  getPreset, getPresets,
  getLayerFamilies, enabledInFamily, familyForGroup, FAMILYLESS_GROUPS,
  methodsFromPreset,
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
    // Clouds preferred value is now a bare SOURCE fused with the cloudStyle arg
    // (#410) — here source 'nwp' × style 'soft' → soft-nwp-cloud-bands.
    const overrides = getCompactLayerOverrides({
      clouds: 'nwp',
      icing: 'ogimet_nwp',
      turbulence: 'ri',
      convection: 'nwp',
    }, 'soft');

    // Clouds: only soft-nwp-cloud-bands enabled
    expect(overrides['soft-nwp-cloud-bands']).toBe(true);
    expect(overrides['soft-cloud-bands']).toBe(false);
    expect(overrides['nwp-cloud-bands']).toBe(false);
    expect(overrides['cloud-bands']).toBe(false);
    expect(overrides['square-nwp-cloud-bands']).toBe(false);

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

  it('unknown preferred method falls back to group default-enabled layer', () => {
    const overrides = getCompactLayerOverrides({
      clouds: 'unknown-method',
      icing: 'ogimet_nwp',
      turbulence: 'ri',
      convection: 'nwp',
    });
    // square-nwp-cloud-bands is the defaultEnabled cloud layer
    expect(overrides['square-nwp-cloud-bands']).toBe(true);
    expect(overrides['soft-nwp-cloud-bands']).toBe(false);
    expect(overrides['soft-cloud-bands']).toBe(false);
    expect(overrides['nwp-cloud-bands']).toBe(false);
    expect(overrides['cloud-bands']).toBe(false);
  });

  it('empty preferredMethods enables each group default (race-condition path)', () => {
    // Reproduces the path where compact mode is entered before fetchPreferences
    // resolves — must still enable a sensible single layer per group, not
    // silently leave stale extras enabled.
    const overrides = getCompactLayerOverrides({});
    expect(overrides['square-nwp-cloud-bands']).toBe(true);
    expect(overrides['icing-ogimet-nwp-bands']).toBe(true);
    expect(overrides['nwp-convective-bg']).toBe(true);
    // turbulence has no defaultEnabled layer; falls back to layers[0] = cat-bands
    expect(overrides['cat-bands']).toBe(true);
    expect(overrides['e-shear-bands']).toBe(false);
    // Non-preferred siblings stay off
    expect(overrides['cloud-bands']).toBe(false);
    expect(overrides['soft-nwp-cloud-bands']).toBe(false);
    expect(overrides['icing-bands']).toBe(false);
    expect(overrides['thermo-convective-bg']).toBe(false);
  });
});

describe('getPreferredLayerForGroup', () => {
  const allLayers = getAllLayers();
  const cloudGroupLayers = allLayers.filter((l) => l.group === 'clouds');
  const icingGroupLayers = allLayers.filter((l) => l.group === 'icing');

  it('returns the layer matching the preferred method', () => {
    // Clouds fuse a bare source with the cloudStyle arg (#410).
    expect(getPreferredLayerForGroup('clouds', cloudGroupLayers, 'nwp', 'soft').id)
      .toBe('soft-nwp-cloud-bands');
    expect(getPreferredLayerForGroup('clouds', cloudGroupLayers, 'dd', 'square').id)
      .toBe('square-cloud-bands');
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
    expect(groups.indexOf('clouds')).toBeLessThan(groups.indexOf('icing'));
    expect(groups.indexOf('icing')).toBeLessThan(groups.indexOf('turbulence'));
    expect(groups.indexOf('turbulence')).toBeLessThan(groups.indexOf('convection'));
  });

  it('omits the terrain group (terrain always renders, no toggle)', () => {
    const groups = getLayerGroups().map((g) => g.group);
    expect(groups).not.toContain('terrain');
    // The terrain layer itself is still registered — it just has no UI toggle.
    expect(getAllLayers().some((l) => l.id === 'terrain')).toBe(true);
  });

  it('every group entry has at least one layer', () => {
    for (const g of getLayerGroups()) {
      expect(g.layers.length).toBeGreaterThan(0);
    }
  });

  it("'fronts' and 'conditions' are registered and non-empty", () => {
    // panel.ts hides these whole groups only when EVERY layer in them is
    // unavailable (HIDE_GROUP_WHEN_ALL_UNAVAILABLE). They used to be assumed
    // single-layer; adding observed-surface to 'conditions' (#574) broke that
    // and would have hidden a working radar layer whenever METAR was absent.
    const groups = getLayerGroups();
    for (const id of ['fronts', 'conditions'] as const) {
      const grp = groups.find((g) => g.group === id);
      expect(grp, `group ${id} not registered`).toBeDefined();
      expect(grp!.layers.length, `group ${id} must have layers`).toBeGreaterThan(0);
    }
  });

  it("registers both observed layers with their intended groups and defaults", () => {
    const layers = getAllLayers();
    const tops = layers.find((l) => l.id === 'observed-tops');
    const surface = layers.find((l) => l.id === 'observed-surface');
    expect(tops, 'observed-tops must be registered').toBeDefined();
    expect(surface, 'observed-surface must be registered').toBeDefined();
    // Grouped by provenance: every measured layer sits under "Observed
    // conditions", so there is one place to find them. The cross-check still
    // works because the tops DRAW over the NWP bands — panel grouping and
    // z-order are deliberately independent (see PANEL_ORDER).
    expect(tops!.group).toBe('conditions');
    expect(tops!.defaultEnabled).toBe(true);
    expect(surface!.group).toBe('conditions');
    expect(surface!.defaultEnabled).toBe(false);
    // The pre-existing current-conditions layer is untouched: these are
    // siblings, not a replacement.
    expect(layers.some((l) => l.id === 'current-conditions')).toBe(true);
  });

  it('draws observed cloud tops above the NWP cloud bands', () => {
    // Render order is the cross-check: "model says FL120, satellite saw
    // FL280" is only legible if the measured line sits over the modelled band.
    const ids = getAllLayers().map((l) => l.id);
    expect(ids.indexOf('observed-tops')).toBeGreaterThan(ids.indexOf('nwp-cloud-bands'));
    expect(ids.indexOf('observed-tops')).toBeGreaterThan(ids.indexOf('cloud-bands'));
  });
});

describe('presets', () => {
  it('GRAMET preset is registered', () => {
    const preset = getPreset('gramet');
    expect(preset).toBeDefined();
    expect(preset!.id).toBe('gramet');
    expect(preset!.themeId).toBe('gramet');
  });

  it('GRAMET enables Natural NWP clouds and Ogimet-NWP icing', () => {
    const preset = getPreset('gramet')!;
    expect(preset.enabledLayers['nwp-cloud-bands']).toBe(true);
    expect(preset.enabledLayers['soft-nwp-cloud-bands']).toBe(false);
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

  it('Windy uses light theme, Natural NWP clouds and SFIP-NWP icing', () => {
    const preset = getPreset('windy')!;
    expect(preset.themeId).toBe('light');
    expect(preset.enabledLayers['nwp-cloud-bands']).toBe(true);
    expect(preset.enabledLayers['sfip-bands']).toBe(true);
    expect(preset.enabledLayers['icing-ogimet-nwp-bands']).toBe(false);
    expect(preset.enabledLayers['nwp-convective-bg']).toBe(true);
  });

  it('ForeFlight uses high-contrast theme, Square DD clouds and Ogimet-DD icing', () => {
    const preset = getPreset('foreflight')!;
    expect(preset.themeId).toBe('high-contrast');
    expect(preset.enabledLayers['square-cloud-bands']).toBe(true);
    expect(preset.enabledLayers['icing-bands']).toBe(true);
    expect(preset.enabledLayers['cat-bands']).toBe(true);
    // not the NWP cloud/icing layers
    expect(preset.enabledLayers['nwp-cloud-bands']).toBe(false);
    expect(preset.enabledLayers['sfip-bands']).toBe(false);
  });

  it('getPreset returns undefined for unknown preset', () => {
    expect(getPreset('does-not-exist')).toBeUndefined();
  });

  it('getPresets includes GRAMET, Windy and ForeFlight', () => {
    const ids = getPresets().map((p) => p.id);
    expect(ids).toContain('gramet');
    expect(ids).toContain('windy');
    expect(ids).toContain('foreflight');
  });
});

describe('layer families (#591)', () => {
  it('covers every toggleable group exactly once', () => {
    // The bar renders families, not groups. A group that belongs to no family
    // vanishes from the panel with no error at all — which is how a whole
    // group could ship invisible. Assert coverage rather than trusting review.
    const owned = getLayerFamilies().flatMap((f) => f.groups.map((g) => g.group));
    const excluded = new Set<string>(FAMILYLESS_GROUPS);

    for (const g of getLayerGroups()) {
      if (excluded.has(g.group)) continue;
      expect(owned, `group '${g.group}' belongs to no family`).toContain(g.group);
    }
    expect(new Set(owned).size, 'a group is claimed by two families').toBe(owned.length);
  });

  it('flattens each family to the layers of the groups it owns', () => {
    const clouds = getLayerFamilies().find((f) => f.family === 'clouds')!;
    const ids = clouds.layers.map((l) => l.id);
    expect(ids).toContain('nwp-cloud-bands');
    // Obscuration rides with clouds: "is there cloud in the way" is one question.
    expect(ids).toContain('surface-obscuration-bands');
    // Cloud tops is an observation, not a cloud source — it stays in Observed.
    expect(ids).not.toContain('observed-tops');

    const observed = getLayerFamilies().find((f) => f.family === 'observed')!;
    expect(observed.layers.map((l) => l.id)).toContain('observed-tops');
  });

  it('reads enabled state the way the checkboxes render it', () => {
    const levels = getLayerFamilies().find((f) => f.family === 'levels')!;
    // Absent means ON, matching `enabledLayers[id] !== false` in layerTogglesHtml —
    // if these two ever disagree the bar's summary lies about the boxes below it.
    expect(enabledInFamily(levels, {}).map((l) => l.id)).toContain('freezing-level');
    expect(enabledInFamily(levels, { 'freezing-level': false }).map((l) => l.id))
      .not.toContain('freezing-level');
  });

  it('maps a group back to its owning family', () => {
    expect(familyForGroup('obscuration')).toBe('clouds');
    expect(familyForGroup('sun')).toBe('observed');
    expect(familyForGroup('reference')).toBe('levels');
    for (const g of FAMILYLESS_GROUPS) expect(familyForGroup(g)).toBeNull();
  });
});

describe('methodsFromPreset (#591)', () => {
  it('reads each emulation preset back as the methods it chose', () => {
    // GRAMET: natural NWP cloud, Ogimet-NWP icing, Ri turbulence, NWP convection.
    const gramet = methodsFromPreset(getPreset('gramet')!);
    expect(gramet.preferredMethods).toEqual({
      clouds: 'nwp', icing: 'ogimet_nwp', turbulence: 'ri', convection: 'nwp',
    });
    expect(gramet.cloudStyle).toBe('natural');
    expect(gramet.themeId).toBe('gramet');

    // Windy differs from GRAMET only in the icing model.
    const windy = methodsFromPreset(getPreset('windy')!);
    expect(windy.preferredMethods.icing).toBe('sfip_nwp');
    expect(windy.preferredMethods.clouds).toBe('nwp');

    // ForeFlight is the all-DD set with square cells.
    const ff = methodsFromPreset(getPreset('foreflight')!);
    expect(ff.preferredMethods.clouds).toBe('dd');
    expect(ff.preferredMethods.icing).toBe('ogimet_dd');
    expect(ff.cloudStyle).toBe('square');
  });

  it('feeds straight back into the resolver the focus lens uses', () => {
    // This is the whole point of the derivation: a focus lens asks for "the
    // preferred layer of the icing group" and must land on the emulation's
    // choice, so Emulate and Focus compose instead of fighting.
    const { preferredMethods, cloudStyle } = methodsFromPreset(getPreset('foreflight')!);
    const icing = getAllLayers().filter((l) => l.group === 'icing');
    expect(getPreferredLayerForGroup('icing', icing, preferredMethods.icing).id).toBe('icing-bands');

    const clouds = getAllLayers().filter((l) => l.group === 'clouds');
    expect(getPreferredLayerForGroup('clouds', clouds, preferredMethods.clouds, cloudStyle).id)
      .toBe('square-cloud-bands');
  });

  it('omits a group the preset switches off entirely', () => {
    // No entry means "no preference" — the resolver then falls back to the
    // group's defaultEnabled, which is what it already does for a missing key.
    const stub = { id: 'stub', label: 'Stub', themeId: 'light', enabledLayers: { terrain: true } };
    expect(methodsFromPreset(stub).preferredMethods).toEqual({});
    expect(methodsFromPreset(stub).cloudStyle).toBeUndefined();
  });
});
