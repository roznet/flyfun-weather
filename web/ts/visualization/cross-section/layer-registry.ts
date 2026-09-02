/** Registry of all available cross-section layers. */

import type { CrossSectionLayer, LayerGroup } from '../types';
import { t } from '../../i18n/i18n';
import { freezingLevelLayer, minus10cLayer, minus20cLayer } from './layers/temperature-lines';
import { cruiseAltitudeLayer } from './layers/reference-lines';
import { icingBandsLayer } from './layers/icing-bands';
import { icingOgimetNwpBandsLayer } from './layers/icing-ogimet-nwp-bands';
import { sfipBandsLayer } from './layers/sfip-bands';
import { lclLayer, lfcLayer, elLayer } from './layers/stability-lines';
import { catBandsLayer } from './layers/cat-bands';
import { inversionBandsLayer } from './layers/inversion-bands';
import { thermoConvectiveBgLayer } from './layers/thermo-convective-bg';
import { nwpConvectiveBgLayer } from './layers/nwp-convective-bg';
import { terrainFillLayer } from './layers/terrain-fill';
import {
  cloudBandsLayer,
  nwpCloudBandsLayer,
  softCloudBandsLayer,
  softNwpCloudBandsLayer,
  squareCloudBandsLayer,
  squareNwpCloudBandsLayer,
  CLOUD_LAYER_BY_AXES,
  ALL_CLOUD_LAYER_IDS,
  parseCloudLayerId,
  type CloudSource,
  type CloudStyle,
} from './layers/cloud-bands-factory';
import { iengIcingBandsLayer } from './layers/ieng-icing-bands';
import { sldBandsLayer } from './layers/sld-bands';
import { eShearBandsLayer } from './layers/e-shear-bands';
import { surfaceObscurationBandsLayer } from './layers/surface-obscuration-bands';
import { currentConditionsLayer } from './layers/current-conditions';
import { observedTopsLayer } from './layers/observed-tops';
import { observedSurfaceLayer } from './layers/observed-surface';
import { frontsMarkersLayer } from './layers/fronts-markers';
import { nightShadingLayer } from './layers/night-shading';
import { highlightLayer } from './layers/highlight-layer';

const ALL_LAYERS: CrossSectionLayer[] = [
  // Rendering order: night → obscuration → clouds → convection → icing → other bands → terrain → lines → reference.
  // Night/twilight shading is first (very back of the stack) so it tints the
  // whole column behind the weather; terrain later masks the below-surface tint.
  nightShadingLayer,
  // Obscuration sits at the bottom of the stack so any DD/NWP cloud
  // bands that extend above the boundary layer overlay it cleanly.
  surfaceObscurationBandsLayer,
  softNwpCloudBandsLayer,
  softCloudBandsLayer,
  squareNwpCloudBandsLayer,
  squareCloudBandsLayer,
  nwpCloudBandsLayer,
  cloudBandsLayer,
  // Observed cloud tops draw over the NWP cloud bands on purpose: that
  // overlap IS the cross-check (#574). Nothing computes the comparison in
  // phase 1 — it is read off the picture.
  observedTopsLayer,
  thermoConvectiveBgLayer,
  nwpConvectiveBgLayer,
  icingBandsLayer,
  icingOgimetNwpBandsLayer,
  sfipBandsLayer,
  iengIcingBandsLayer,
  sldBandsLayer,
  catBandsLayer,
  eShearBandsLayer,
  inversionBandsLayer,
  terrainFillLayer,
  // Current conditions sits above terrain (columns rest on the surface) but
  // below the temperature/stability/reference lines so those stay readable.
  currentConditionsLayer,
  // Observed radar/lightning hugs the terrain, so it sits with the other
  // surface-referenced overlays rather than in the cloud stack.
  observedSurfaceLayer,
  // Front markers are vertical lines (not bands) — draw above terrain fill so
  // the marker stays visible over mountainous cross-sections.
  frontsMarkersLayer,
  freezingLevelLayer,
  minus10cLayer,
  minus20cLayer,
  lclLayer,
  lfcLayer,
  elLayer,
  cruiseAltitudeLayer,
  // Advisory highlight (scrim + verdict ribbon, #373) — registered last so it
  // sits at the very top of the stack (the dim wash must overlay everything).
  highlightLayer,
];

export function getAllLayers(): CrossSectionLayer[] {
  return ALL_LAYERS;
}

/** Where the cross-section is being rendered. Used for context-specific
 *  layer defaults: surface obscuration is high-signal in the airport-
 *  profile drawer (where users right-click for low-altitude detail) but
 *  off by default on the briefing page (avoids stacking with low-cloud /
 *  DD bands users already have visible). */
export type CrossSectionContext = 'briefing' | 'airport-profile';

const CONTEXT_OVERRIDES: Record<CrossSectionContext, Record<string, boolean>> = {
  briefing: {},
  'airport-profile': {
    'surface-obscuration-bands': true,
  },
};

export function getDefaultEnabled(context: CrossSectionContext = 'briefing'): Record<string, boolean> {
  const enabled: Record<string, boolean> = {};
  for (const layer of ALL_LAYERS) {
    enabled[layer.id] = layer.defaultEnabled;
  }
  Object.assign(enabled, CONTEXT_OVERRIDES[context]);
  return enabled;
}

export interface LayerGroupInfo {
  group: LayerGroup;
  label: string;
  layers: CrossSectionLayer[];
}

/** Maps preference values to layer IDs for groups that collapse in compact mode.
 *  Clouds are handled separately (see {@link getPreferredLayerForGroup}): their
 *  preferred value is a bare SOURCE composed with the client-only render STYLE
 *  (`vizSettings.cloudStyle`) into a concrete layer id (#410). */
const PREFERRED_METHOD_LAYER: Record<string, Record<string, string>> = {
  icing: { ogimet_dd: 'icing-bands', ogimet_nwp: 'icing-ogimet-nwp-bands', sfip_nwp: 'sfip-bands', ieng: 'ieng-icing-bands' },
  turbulence: { ri: 'cat-bands', e_shear: 'e-shear-bands' },
  convection: { thermo: 'thermo-convective-bg', nwp: 'nwp-convective-bg' },
};

/** The groups that are a CHOICE OF METHOD rather than a feature switch: each
 *  holds several ways of computing the same thing, so exactly one of them is
 *  "the preferred one" and the rest are alternatives.
 *
 *  Derived from {@link PREFERRED_METHOD_LAYER} rather than restated, plus
 *  clouds — whose preferred value is a bare source composed with the render
 *  style, so it is keyed separately. Compact mode collapses exactly these, and
 *  the Basic/Learn lens turns on exactly these; both read this list, so the two
 *  cannot drift apart into "compact shows one of each, Basic shows something
 *  slightly different".
 */
export const METHOD_GROUPS: readonly LayerGroup[] = [
  'clouds',
  ...(Object.keys(PREFERRED_METHOD_LAYER) as LayerGroup[]),
];

/** Map a preferred clouds value — a bare source (`dd` / `nwp`), including the
 *  backend's `nwp_synthesized` which renders on the NWP band — to a concrete
 *  {@link CloudSource}. Returns null for an empty/unknown value. */
function cloudSourceFromPreferred(preferred: string | undefined): CloudSource | null {
  if (preferred === 'dd') return 'dd';
  if (preferred === 'nwp' || preferred === 'nwp_synthesized') return 'nwp';
  return null;
}

/** Return the preferred layer for a group based on the user's preference method.
 *  For the clouds group the preferred value is a bare SOURCE that is fused with
 *  the client-only render STYLE (`cloudStyle`) to pick the concrete layer id —
 *  mirrors iOS `cloudLayerId(source:style:)` (#410). Other groups key directly
 *  on the method id. */
export function getPreferredLayerForGroup(
  group: LayerGroup,
  layers: CrossSectionLayer[],
  preferredMethod: string | undefined,
  cloudStyle: CloudStyle = 'square',
): CrossSectionLayer {
  if (group === 'clouds') {
    const source = cloudSourceFromPreferred(preferredMethod);
    if (source) {
      const layerId = CLOUD_LAYER_BY_AXES[source][cloudStyle];
      const found = layers.find(l => l.id === layerId);
      if (found) return found;
    }
    return layers.find(l => l.defaultEnabled) ?? layers[0];
  }
  const map = PREFERRED_METHOD_LAYER[group];
  if (map && preferredMethod) {
    const layerId = map[preferredMethod];
    const found = layers.find(l => l.id === layerId);
    if (found) return found;
  }
  return layers.find(l => l.defaultEnabled) ?? layers[0];
}

/**
 * Return layer overrides for entering compact mode: enable only the preferred
 * layer in each compact group, disable the rest. When a group has no known
 * preferred method (empty/missing/unknown), falls back to the group's
 * defaultEnabled layer so compact mode always shows something. The clouds group
 * additionally needs the current `cloudStyle` to fuse with its source (#410).
 */
export function getCompactLayerOverrides(
  preferredMethods: Record<string, string>,
  cloudStyle: CloudStyle = 'square',
): Record<string, boolean> {
  const overrides: Record<string, boolean> = {};
  // Clouds: collapse to the single (source × style) layer. The source comes from
  // the graded profile; the style is the client-only viz preference (#410).
  const cloudLayers = ALL_LAYERS.filter((l) => l.group === 'clouds');
  const preferredCloud = getPreferredLayerForGroup('clouds', cloudLayers, preferredMethods.clouds, cloudStyle);
  for (const layerId of ALL_CLOUD_LAYER_IDS) {
    overrides[layerId] = layerId === preferredCloud.id;
  }
  // Other method-bearing groups: bare method id → layer id.
  for (const [group, methodMap] of Object.entries(PREFERRED_METHOD_LAYER)) {
    const groupLayers = ALL_LAYERS.filter((l) => l.group === group);
    const preferred = getPreferredLayerForGroup(
      group as LayerGroup,
      groupLayers,
      preferredMethods[group],
    );
    for (const layerId of Object.values(methodMap)) {
      overrides[layerId] = layerId === preferred.id;
    }
  }
  return overrides;
}

// --- Presets ---

export interface LayerPreset {
  id: string;
  label: string;
  themeId: string;
  enabledLayers: Record<string, boolean>;
}

const GRAMET_ENABLED: Record<string, boolean> = {
  'soft-nwp-cloud-bands': false,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': false,
  'nwp-cloud-bands': true,        // Natural NWP clouds
  'cloud-bands': false,
  'thermo-convective-bg': false,
  'nwp-convective-bg': true,
  'icing-bands': false,
  'icing-ogimet-nwp-bands': true,
  'sfip-bands': false,
  'ieng-icing-bands': false,
  'sld-bands': false,
  'cat-bands': true,
  'e-shear-bands': false,
  'inversion-bands': false,
  'terrain': true,
  'freezing-level': true,
  'minus-10c': false,
  'minus-20c': false,
  // NB: stability-line layer ids here are 'lcl'/'lfc'/'el'. The '*-line' ids
  // belong to compare-layers.ts and match nothing in this registry — using them
  // silently left the parcel lines on (they are defaultEnabled and setVizPreset
  // merges rather than resets).
  'lcl': false,
  'lfc': false,
  'el': false,
  'cruise-altitude': true,
};

// Windy-style view: light cross-section theme, SFIP-NWP icing, Natural NWP
// clouds, NWP convection. Mirrors GRAMET's overall layer set (CAT, freezing
// level, terrain, cruise) but swaps the cloud/icing methods and the theme.
const WINDY_ENABLED: Record<string, boolean> = {
  'soft-nwp-cloud-bands': false,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': false,
  'nwp-cloud-bands': true,        // Natural NWP clouds
  'cloud-bands': false,
  'thermo-convective-bg': false,
  'nwp-convective-bg': true,      // NWP convection
  'icing-bands': false,
  'icing-ogimet-nwp-bands': false,
  'sfip-bands': true,             // SFIP-NWP icing
  'ieng-icing-bands': false,
  'sld-bands': false,
  'cat-bands': true,
  'e-shear-bands': false,
  'inversion-bands': false,
  'terrain': true,
  'freezing-level': true,
  'minus-10c': false,
  'minus-20c': false,
  'lcl': false,
  'lfc': false,
  'el': false,
  'cruise-altitude': true,
};

// ForeFlight-style view: high-contrast theme, Square DD clouds, Ogimet-DD
// icing, CAT (Ri) turbulence. Mirrors the other presets' base layers (freezing
// level, terrain, cruise) but uses the all-DD method set with square cloud cells.
const FOREFLIGHT_ENABLED: Record<string, boolean> = {
  'soft-nwp-cloud-bands': false,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': true,     // Square DD clouds
  'nwp-cloud-bands': false,
  'cloud-bands': false,
  'thermo-convective-bg': false,
  'nwp-convective-bg': true,
  'icing-bands': true,            // Ogimet-DD icing
  'icing-ogimet-nwp-bands': false,
  'sfip-bands': false,
  'ieng-icing-bands': false,
  'sld-bands': false,
  'cat-bands': true,              // CAT (Ri) turbulence
  'e-shear-bands': false,
  'inversion-bands': false,
  'terrain': true,
  'freezing-level': true,
  'minus-10c': false,
  'minus-20c': false,
  'lcl': false,
  'lfc': false,
  'el': false,
  'cruise-altitude': true,
};

// SYNC: the iOS app mirrors these layer presets (GRAMET / Windy / ForeFlight) in
// app/flyfun-weather/flyfun-weather/Views/CrossSection/Layers/CrossSectionPresets.swift
// (translated to iOS layer IDs). Keep the two in lockstep when editing presets.
const PRESETS: Record<string, LayerPreset> = {
  gramet: {
    id: 'gramet',
    label: 'GRAMET',
    themeId: 'gramet',
    enabledLayers: GRAMET_ENABLED,
  },
  windy: {
    id: 'windy',
    label: 'Windy',
    themeId: 'light',
    enabledLayers: WINDY_ENABLED,
  },
  foreflight: {
    id: 'foreflight',
    label: 'ForeFlight',
    themeId: 'high-contrast',
    enabledLayers: FOREFLIGHT_ENABLED,
  },
};

export function getPresets(): LayerPreset[] {
  return Object.values(PRESETS);
}

export function getPreset(id: string): LayerPreset | undefined {
  return PRESETS[id];
}

export function getLayerGroups(): LayerGroupInfo[] {
  const groupMap = new Map<LayerGroup, CrossSectionLayer[]>();
  for (const layer of ALL_LAYERS) {
    let arr = groupMap.get(layer.group);
    if (!arr) { arr = []; groupMap.set(layer.group, arr); }
    arr.push(layer);
  }

  const groupLabels: Record<LayerGroup, string> = {
    terrain: t('viz.group.terrain'),
    temperature: t('viz.group.temperature'),
    clouds: t('viz.group.clouds'),
    icing: t('viz.group.icing'),
    stability: t('viz.group.stability'),
    turbulence: t('viz.group.turbulence'),
    convection: t('viz.group.convection'),
    obscuration: t('viz.group.obscuration'),
    conditions: t('viz.group.conditions'),
    fronts: t('viz.group.fronts'),
    sun: t('viz.group.sun'),
    highlight: t('viz.group.highlight'),
    reference: t('viz.group.reference'),
  };

  // 'terrain' is intentionally omitted: terrain always renders (force-on at
  // render time), so it has no UI toggle. The terrainFillLayer stays in
  // ALL_LAYERS — only its panel group is dropped here.
  // 'highlight' is listed so its toggle CAN render, but the panel hides it via
  // `hiddenGroups` unless an advisory highlight is active with data (#373).
  const order: LayerGroup[] = ['reference', 'temperature', 'clouds', 'obscuration', 'icing', 'stability', 'turbulence', 'convection', 'conditions', 'sun', 'fronts', 'highlight'];

  return order
    .filter((g) => groupMap.has(g))
    .map((g) => ({
      group: g,
      label: groupLabels[g],
      layers: panelOrdered(g, groupMap.get(g)!),
    }));
}

/** Panel order within a group, which is NOT the drawing order.
 *
 * `ALL_LAYERS` is a z-stack: a layer's position there decides what it paints
 * over. The panel is a list a pilot reads. The two coincide for most groups,
 * but `observed-tops` has to draw early — before `terrainFill`, so a cloud top
 * below the terrain surface is masked rather than floating over a mountain —
 * while reading last in a list that runs airport → surface → tops. Encoding
 * that here keeps the z-stack free to be a z-stack.
 */
const PANEL_ORDER: Partial<Record<LayerGroup, string[]>> = {
  conditions: ['current-conditions', 'observed-surface', 'observed-tops'],
};

function panelOrdered(group: LayerGroup, layers: CrossSectionLayer[]): CrossSectionLayer[] {
  const wanted = PANEL_ORDER[group];
  if (!wanted) return layers;
  const rank = (id: string) => {
    const i = wanted.indexOf(id);
    return i === -1 ? wanted.length : i;  // unlisted layers keep registry order, at the end
  };
  return [...layers].sort((a, b) => rank(a.id) - rank(b.id));
}

// --- Families (#591) ---

/**
 * A family is one control on the layer bar: a question the pilot asks, which
 * may span more than one registry group. Clouds owns the cloud sources *and*
 * surface obscuration, because "is there cloud in the way" is one question.
 * Observed owns the instruments, the sun shading and the front markers,
 * because they are all "what is actually out there right now".
 *
 * `LayerGroup` stays the finer tier — a family renders one sub-heading per
 * group it owns, in the order listed in {@link FAMILY_GROUPS}.
 */
export type LayerFamily =
  | 'clouds'
  | 'convection'
  | 'icing'
  | 'turbulence'
  | 'levels'
  | 'stability'
  | 'observed';

/** Family → the groups it owns, in detail-row reading order.
 *
 *  `terrain` and `highlight` are deliberately absent: terrain force-renders and
 *  has no toggle at all, and the highlight is driven by an active advisory
 *  rather than by the user. Every *other* group must appear exactly once — a
 *  group missing from here would vanish from the bar with no error, so the
 *  tests assert this map covers them all. */
const FAMILY_GROUPS: Record<LayerFamily, LayerGroup[]> = {
  clouds: ['clouds', 'obscuration'],
  convection: ['convection'],
  icing: ['icing'],
  turbulence: ['turbulence'],
  levels: ['temperature', 'reference'],
  stability: ['stability'],
  observed: ['conditions', 'sun', 'fronts'],
};

/** Groups that intentionally belong to no family. Kept explicit so the
 *  completeness test can tell "excluded on purpose" from "forgotten". */
export const FAMILYLESS_GROUPS: readonly LayerGroup[] = ['terrain', 'highlight'];

/** Bar order, left to right. Sky first, then hazards, then the reference lines
 *  and ground truth — roughly the order a pilot interrogates a cross-section. */
const FAMILY_ORDER: readonly LayerFamily[] = [
  'clouds', 'convection', 'icing', 'turbulence', 'levels', 'stability', 'observed',
];

export interface LayerFamilyInfo {
  family: LayerFamily;
  label: string;
  /** Sub-questions, one per owned group, in detail-row order. Groups with no
   *  registered layers are dropped rather than rendering an empty heading. */
  groups: LayerGroupInfo[];
  /** Every layer in the family, flattened. The bar summarises over this. */
  layers: CrossSectionLayer[];
}

/** Localized family label, falling back to the English literal when the
 *  translation key is absent (mirrors `advisoryPresetLabel`). */
function familyLabel(family: LayerFamily): string {
  const key = `viz.family.${family}`;
  const s = t(key);
  if (s !== key) return s;
  return family.charAt(0).toUpperCase() + family.slice(1);
}

/** The layer bar's model: families in bar order, each carrying its groups. */
export function getLayerFamilies(): LayerFamilyInfo[] {
  const groups = getLayerGroups();
  const byGroup = new Map<LayerGroup, LayerGroupInfo>();
  for (const g of groups) byGroup.set(g.group, g);

  return FAMILY_ORDER.map((family) => {
    const owned = FAMILY_GROUPS[family]
      .map((g) => byGroup.get(g))
      .filter((g): g is LayerGroupInfo => g != null && g.layers.length > 0);
    return {
      family,
      label: familyLabel(family),
      groups: owned,
      layers: owned.reduce<CrossSectionLayer[]>((acc, g) => acc.concat(g.layers), []),
    };
  }).filter((f) => f.layers.length > 0);
}

/** The layers of a family that are currently on.
 *
 *  Reads `enabledLayers` exactly the way `layerTogglesHtml` renders its
 *  checkboxes — absent means ON — so the bar's summary can never disagree with
 *  the boxes underneath it. `getDefaultEnabled()` populates every id, so the
 *  sparse case only arises for a layer added since the state was persisted. */
export function enabledInFamily(
  info: LayerFamilyInfo,
  enabledLayers: Record<string, boolean>,
): CrossSectionLayer[] {
  return info.layers.filter((l) => enabledLayers[l.id] !== false);
}

/** Which family owns a group, or null for the deliberately family-less ones. */
export function familyForGroup(group: LayerGroup): LayerFamily | null {
  for (const family of FAMILY_ORDER) {
    if (FAMILY_GROUPS[family].includes(group)) return family;
  }
  return null;
}

// --- Emulation presets read as method choices (#591) ---

/** What an emulation preset (GRAMET / Windy / ForeFlight) actually chooses,
 *  once you stop reading its layer map as a result and start reading it as an
 *  intent. */
export interface PresetMethods {
  /** Group id → method id, in the vocabulary `getPreferredLayerForGroup` and
   *  `getCompactLayerOverrides` already take. Clouds carries a bare source. */
  preferredMethods: Record<string, string>;
  /** The render style the preset's cloud layer implies, if it enables one. */
  cloudStyle?: CloudStyle;
  /** The preset's theme, passed through for convenience. */
  themeId: string;
}

/**
 * Read a preset's explicit `enabledLayers` map back as the method choices it
 * represents.
 *
 * The two lens selectors compose because they answer different questions:
 * Emulate picks the METHODS (Ogimet-NWP icing, natural NWP cloud, the GRAMET
 * theme), Focus picks WHICH GROUPS are on. A focus lens never names a method —
 * it asks for "the preferred layer of this group" — so it resolves through
 * whatever Emulate chose, and the two cannot contradict each other.
 *
 * That only works if an emulation's method choice is legible, and today it is
 * not: `LayerPreset.enabledLayers` is a flat id→bool map, a *result* rather
 * than an intent. This derives the intent back out, inverting
 * `PREFERRED_METHOD_LAYER` rather than restating it so the two can never drift.
 *
 * A group the preset leaves entirely off yields no entry — the caller then
 * falls back to the group's `defaultEnabled`, exactly as
 * `getPreferredLayerForGroup` already does for a missing preference.
 */
export function methodsFromPreset(preset: LayerPreset): PresetMethods {
  const preferredMethods: Record<string, string> = {};

  for (const [group, methodMap] of Object.entries(PREFERRED_METHOD_LAYER)) {
    for (const [method, layerId] of Object.entries(methodMap)) {
      if (preset.enabledLayers[layerId]) {
        preferredMethods[group] = method;
        break;   // first enabled wins; presets enable one method per group
      }
    }
  }

  let cloudStyle: CloudStyle | undefined;
  for (const layerId of ALL_CLOUD_LAYER_IDS) {
    if (!preset.enabledLayers[layerId]) continue;
    const axes = parseCloudLayerId(layerId);
    if (!axes) continue;
    preferredMethods.clouds = axes.source;
    cloudStyle = axes.style;
    break;
  }

  return { preferredMethods, cloudStyle, themeId: preset.themeId };
}

// --- Bar summaries (#591) ---

/** Short label for the bar, where a family may have to name two layers and a
 *  render style inside one chip. Falls back to the full panel label, so a
 *  layer only needs a `viz.layerShort.*` key when its full label is too long
 *  to sit in a chip ("Cruise / Flight ceiling", "Freezing Level (0°C)"). */
export function shortLayerLabel(layer: CrossSectionLayer): string {
  const key = `viz.layerShort.${layer.id}`;
  const s = t(key);
  if (s !== key) return s;
  return t(`viz.layer.${layer.id}`);
}

/** What a family chip reads on the bar.
 *
 *  The rule is the same everywhere: name the answers while they fit — up to
 *  two, joined with "+" — then fall back to a count. A family with nothing on
 *  reads `off` rather than an empty chip, so "nothing here" and "I have not
 *  looked" cannot be confused.
 *
 *  Clouds is the one composite: its render style is a qualifier on the source,
 *  not a third layer, so it reads `NWP + DD · Square` rather than `3 on`. All
 *  six cloud band ids collapse to their source, since the style is carried
 *  separately in `vizSettings.cloudStyle`.
 */
export function familySummary(
  info: LayerFamilyInfo,
  enabledLayers: Record<string, boolean>,
  cloudStyle?: CloudStyle,
): { text: string; off: boolean } {
  const on = enabledInFamily(info, enabledLayers);
  if (on.length === 0) return { text: t('viz.familyOff'), off: true };

  const names: string[] = [];
  const sources: string[] = [];
  for (const layer of on) {
    const axes = parseCloudLayerId(layer.id);
    if (axes) {
      // Collapse the six cloud ids to their source; the style is a qualifier.
      const src = axes.source.toUpperCase();
      if (!sources.includes(src)) sources.push(src);
    } else {
      names.push(shortLayerLabel(layer));
    }
  }

  const parts: string[] = [];
  if (sources.length > 0) {
    const style = cloudStyle ?? parseCloudLayerId(on.find((l) => parseCloudLayerId(l.id))!.id)?.style;
    parts.push(sources.join(' + ') + (style ? ` · ${t(`viz.cloudStyle.${style}`)}` : ''));
  }
  if (names.length > 0 && names.length <= 2) parts.push(names.join(' + '));
  else if (names.length > 2) parts.push(t('viz.familyCount', { n: String(names.length) }));

  return { text: parts.join(' · '), off: false };
}
