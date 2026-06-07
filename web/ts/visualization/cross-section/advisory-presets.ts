/**
 * Advisory-aware cross-section presets (issue #219, Phase 1).
 *
 * One-click view configurations that select the cross-section layers (and
 * companion route-graph / route-map metrics) best illustrating a given
 * advisory category — respecting the profile's chosen method (NWP vs DD
 * clouds, Ogimet vs SFIP icing, ...).
 *
 * This file IS the config. To add or tune a preset, edit {@link ADVISORY_PRESETS};
 * to point an advisory at a different preset, edit {@link ADVISORY_TO_PRESET};
 * to layer per-advisory extras on a shared preset, edit {@link ADVISORY_OVERRIDES}.
 * No apply-logic changes are needed for any of those.
 *
 * The preset shape is a bag of OPTIONAL view directives; the resolver/applier
 * dispatches over whichever directives are present. Adding a *new kind* of
 * effect later (highlight a band, emphasize layers, pre-enable a Skew-T
 * overlay) is: add one optional field here + one field on {@link ResolvedView}
 * + one branch in the store applier — existing presets and call sites untouched.
 */

import type { LayerGroup } from '../types';
import { getAllLayers, getPreferredLayerForGroup } from './layer-registry';
import { t } from '../../i18n/i18n';

export interface AdvisoryPreset {
  id: string;                 // 'icing','clouds','convective','turbulence','vfr','ifr'
  label: string;              // dropdown + chip label
  caption: string;            // one-liner shown above the chart explaining the view

  // ---- view directives (all optional; applier dispatches over those present) ----
  groups?: LayerGroup[];      // method-resolved: enable the preferred layer of each
  lines?: string[];           // explicit layer IDs to force on (lines, obscuration, ...)
  routeGraph?: { left?: string; right?: string };  // metric ids ('none' clears right)
  map?: { metric?: string; altitude?: 'cruise' };  // route-map color metric + slider target

  // ---- reserved for later phases (documented now so the shape doesn't churn) ----
  // highlights?: HighlightDirective[]; // shade in-cloud band / affected segments (Phase 2)
  // emphasize?: string[];              // dim layers NOT in this list
  // skewtOverlays?: string[];          // pre-enable matching Skew-T overlay bands
}

/**
 * Groups the resolver wipes to a clean slate before applying a preset's
 * `groups`/`lines`. Every band/line group a preset might touch is reset to OFF
 * so the resulting view shows ONLY what the preset specifies (plus the always-on
 * terrain + cruise-altitude context). Groups intentionally NOT reset:
 * `terrain` (always rendered), `reference` (only holds cruise-altitude, set on
 * explicitly), `conditions` (D-0 METAR/SIGMET overlay — model-independent, user's
 * choice), and `fronts` (experimental overlay — user's choice).
 */
const RESET_GROUPS: ReadonlySet<LayerGroup> = new Set<LayerGroup>([
  'clouds', 'icing', 'convection', 'turbulence', 'stability', 'temperature', 'obscuration',
]);

/**
 * Optional per-advisory tweaks layered on top of the shared preset, keyed by
 * advisory_id. `groups`/`lines` are UNIONED with the base preset; scalar
 * directives (routeGraph/map) override. The dropdown applies bare presets; only
 * the card chip consults this (it knows which advisory it came from).
 */
export const ADVISORY_OVERRIDES: Record<string, Partial<AdvisoryPreset>> = {
  // FIKI: add layer-thickness / warm-nose context (−10/−20 °C + SLD warm-nose).
  fiki_icing: { lines: ['minus-10c', 'minus-20c', 'sld-bands'] },
};

export const ADVISORY_PRESETS: Record<string, AdvisoryPreset> = {
  icing: {
    id: 'icing',
    label: 'Icing',
    caption: 'Icing bands vs the 0 °C line and terrain — is there an ice-free descent?',
    groups: ['icing', 'clouds'],
    lines: ['freezing-level'],
    routeGraph: { left: 'freezing-level', right: 'ceiling-nwp' },
    map: { metric: 'icing-risk-at-level', altitude: 'cruise' },
  },
  clouds: {
    id: 'clouds',
    label: 'Clouds',
    caption: 'Cloud tops & coverage vs your cruise level.',
    groups: ['clouds'],
    lines: ['freezing-level'],
    routeGraph: { left: 'cloud-cover', right: 'ceiling-nwp' },
    map: { metric: 'cloud-at-level', altitude: 'cruise' },
  },
  convective: {
    id: 'convective',
    label: 'Convective',
    caption: 'Towers framed by LCL→LFC→EL and instability along route.',
    groups: ['convection', 'clouds'],
    // NB: stability-line layer ids are 'lcl'/'lfc'/'el' (not '*-line' — that
    // suffix is only used by the compare-mode display registry).
    lines: ['lcl', 'lfc', 'el', 'freezing-level', 'minus-10c', 'minus-20c'],
    routeGraph: { left: 'cape', right: 'precipitation' },
    map: { metric: 'convective-risk', altitude: 'cruise' },
  },
  turbulence: {
    id: 'turbulence',
    label: 'Turbulence',
    caption: 'CAT/shear layers near cruise; terrain + wind for orographic risk.',
    groups: ['turbulence'],
    lines: ['inversion-bands'],
    routeGraph: { left: 'headwind', right: 'crosswind' },
    map: { metric: 'cat-risk-at-level', altitude: 'cruise' },
  },
  vfr: {
    id: 'vfr',
    label: 'VFR feasibility',
    caption: 'VMC picture: clouds & obscuration vs cruise and airports.',
    groups: ['clouds'],
    lines: ['surface-obscuration-bands', 'freezing-level'],
    routeGraph: { left: 'cloud-cover', right: 'ceiling-nwp' },
    map: { metric: 'nwp-ceiling' },
  },
  ifr: {
    id: 'ifr',
    label: 'IFR feasibility',
    caption: 'IFR hazards: icing + convection + cloud along route.',
    groups: ['icing', 'convection', 'clouds'],
    lines: ['freezing-level', 'minus-10c'],
    routeGraph: { left: 'cape', right: 'freezing-level' },
    map: { metric: 'icing-risk-at-level', altitude: 'cruise' },
  },
};

/**
 * advisory_id -> preset id (used by the card chip). Only advisories listed here
 * get a chip. Phase 1 covers the cross-section-hazard advisories; airport,
 * model-quality, and fronts advisories are deferred (they need other action
 * types — see the issue's "Out of scope").
 */
export const ADVISORY_TO_PRESET: Record<string, string> = {
  icing_escape: 'icing', fiki_icing: 'icing',
  cloud_top: 'clouds', vmc_cruise: 'clouds',
  convective: 'convective',
  turbulence: 'turbulence', mountain_wind: 'turbulence',
  vfr_feasibility: 'vfr', ifr_feasibility: 'ifr',
};

export function getAdvisoryPreset(id: string): AdvisoryPreset | undefined {
  return ADVISORY_PRESETS[id];
}

export function getAdvisoryPresets(): AdvisoryPreset[] {
  return Object.values(ADVISORY_PRESETS);
}

/** True when `id` names an advisory preset (vs GRAMET / null / a layer preset). */
export function isAdvisoryPreset(id: string | null | undefined): boolean {
  return !!id && id in ADVISORY_PRESETS;
}

/**
 * Localized dropdown label for a preset. Looks up `viz.advisoryPreset.<id>.label`
 * and falls back to the preset's English `label` literal when no translation key
 * exists (so a missing key shows readable English, never the raw key).
 */
export function advisoryPresetLabel(p: AdvisoryPreset): string {
  const key = `viz.advisoryPreset.${p.id}.label`;
  const s = t(key);
  return s === key ? p.label : s;
}

/** Localized chart caption for a preset; same key/fallback scheme as the label. */
export function advisoryPresetCaption(p: AdvisoryPreset): string {
  const key = `viz.advisoryPreset.${p.id}.caption`;
  const s = t(key);
  return s === key ? p.caption : s;
}

/**
 * Resolve the preset the card chip should apply for a given advisory_id:
 * the base preset from {@link ADVISORY_TO_PRESET}, with any per-advisory
 * {@link ADVISORY_OVERRIDES} merged in (groups/lines unioned, scalar directives
 * overridden). Returns `undefined` when the advisory has no chip.
 */
export function getPresetForAdvisory(advisoryId: string): AdvisoryPreset | undefined {
  const presetId = ADVISORY_TO_PRESET[advisoryId];
  if (!presetId) return undefined;
  const base = ADVISORY_PRESETS[presetId];
  if (!base) return undefined;
  const override = ADVISORY_OVERRIDES[advisoryId];
  if (!override) return base;
  return {
    ...base,
    ...override,
    // arrays union (so FIKI adds to icing's lines rather than replacing them)
    groups: [...(base.groups ?? []), ...(override.groups ?? [])],
    lines: [...(base.lines ?? []), ...(override.lines ?? [])],
    routeGraph: override.routeGraph ?? base.routeGraph,
    map: override.map ?? base.map,
    // identity stays the base preset's so the dropdown reflects 'icing', not a
    // per-advisory pseudo-id, and the caption is the shared one.
    id: base.id,
    label: base.label,
    caption: base.caption,
  };
}

/** A resolved, concrete view the store applies field-by-field. Adding a future
 *  directive = add a field here + a branch in the store applier; the resolver
 *  loop below is otherwise unchanged. */
export interface ResolvedView {
  enabledLayers?: Record<string, boolean>;
  routeGraph?: { left?: string; right?: string };
  map?: { metric?: string; altitudeFt?: number | null };
  // future: highlights?, emphasize?, skewtOverlays? — added alongside, dispatched independently
}

/**
 * Method resolution: turn an {@link AdvisoryPreset} into concrete layer IDs and
 * companion metrics, given the profile's preferred methods. Clean-slate model:
 * every band/line group is reset OFF, then the preset's method-resolved groups
 * and explicit lines are enabled (plus always-on terrain + cruise-altitude), so
 * the resulting view shows only what the advisory needs.
 *
 * `turbulence` has no `preferredMethods` key → `getPreferredLayerForGroup` falls
 * back to the group's default (CAT/Ri). `inversion-bands` lives in the
 * `stability` group, so the `turbulence` preset enables it via `lines`.
 */
export function resolveAdvisoryPreset(
  preset: AdvisoryPreset,
  preferredMethods: Record<string, string>,
): ResolvedView {
  const view: ResolvedView = {};
  const allLayers = getAllLayers();

  if (preset.groups || preset.lines) {
    const enabled: Record<string, boolean> = {};
    // 1. clean slate: OFF every band/line in each resettable group.
    for (const l of allLayers) {
      if (RESET_GROUPS.has(l.group)) enabled[l.id] = false;
    }
    // 2. ON the method-resolved preferred layer of each named group.
    for (const g of preset.groups ?? []) {
      const groupLayers = allLayers.filter(x => x.group === g);
      enabled[getPreferredLayerForGroup(g, groupLayers, preferredMethods[g]).id] = true;
    }
    // 3. explicit lines / extras.
    for (const id of preset.lines ?? []) enabled[id] = true;
    // 4. always-on context. Terrain is force-rendered at draw time
    //    (briefing-main sets effectiveEnabled['terrain']=true regardless of
    //    enabledLayers), so it needs no entry here. cruise-altitude IS
    //    toggle-controlled, so force it on.
    enabled['cruise-altitude'] = true;
    view.enabledLayers = enabled;
  }

  if (preset.routeGraph) view.routeGraph = preset.routeGraph;
  if (preset.map) {
    // Only carry an `altitudeFt` directive when the preset asks to retarget the
    // slider (altitude: 'cruise' → null = cruise). For level-independent map
    // metrics (no `altitude`), omit the key entirely so the store leaves the
    // user's current slider position untouched.
    const m: ResolvedView['map'] = { metric: preset.map.metric };
    if (preset.map.altitude === 'cruise') m!.altitudeFt = null;
    view.map = m;
  }
  return view;
}
