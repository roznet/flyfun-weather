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
 * effect later (pre-enable a Skew-T overlay, retarget the map metric, ...) is:
 * add one optional field here + one field on {@link ResolvedView} + one branch
 * in the store applier — existing presets and call sites untouched.
 *
 * NOTE: the advisory HIGHLIGHT mechanism (scrim + verdict ribbon, #373) does NOT
 * flow through this static preset config. Geometry depends on a concrete advisory
 * instance (evaluator thresholds / altitude / user params), which a bare dropdown
 * lens does not have — so highlights travel the chip/deep-link path instead, via
 * `vizSettings.activeHighlightAdvisoryId` (set in briefing-main's chip handler and
 * derived reactively in `cross-section/advisory-highlights.ts`), never as a preset
 * directive.
 *
 * SYNC: the iOS app mirrors ADVISORY_PRESETS + ADVISORY_TO_PRESET (cross-section
 * directives only) in
 * app/flyfun-weather/flyfun-weather/Views/CrossSection/Layers/CrossSectionPresets.swift.
 * Keep the lens set, labels/captions, groups/lines, and advisory→lens mapping in
 * lockstep when editing here.
 */

import type { LayerGroup } from '../types';
import { getAllLayers, getPreferredLayerForGroup } from './layer-registry';
import type { CloudStyle } from './layers/cloud-bands-factory';
import { SKEWT_OVERLAYS } from '../skewt/overlay-bands';
import { t } from '../../i18n/i18n';

export interface AdvisoryPreset {
  id: string;                 // 'icing','clouds','convective','turbulence','vfr','ifr','basic'
  label: string;              // dropdown + chip label
  caption: string;            // one-liner shown above the chart explaining the view
  /** Short "how to read it" interpretation blurb (#308 Phase B). Surfaced
   *  behind the chart's (i) / "Help me read this graph" button and reused
   *  verbatim as MCP explanation context — write once, human + assistant read
   *  the same words. Falls back to `caption` when absent. */
  interpretation?: string;

  // ---- view directives (all optional; applier dispatches over those present) ----
  groups?: LayerGroup[];      // method-resolved: enable the preferred layer of each
  lines?: string[];           // explicit layer IDs to force on (lines, obscuration, ...)
  routeGraph?: { left?: string; right?: string };  // metric ids ('none' clears right)
  map?: { metric?: string; altitude?: 'cruise' };  // route-map color metric + slider target

  // ---- Skew-T directives (#308 Phase A; same dispatch-over-present pattern) ----
  /** Overlay bands to pre-enable on the Skew-T (ids from {@link SKEWT_OVERLAYS}:
   *  clouds-nwp/clouds-dd/icing-nwp/icing-dd/icing-sfip/inversions/convective).
   *  Clean-slate like cross-section `groups`: listed bands ON, all others OFF —
   *  so the lens shows only what the hazard needs. An empty array means "all
   *  overlay bands off" (the Basic/Learn view). */
  skewtOverlays?: string[];
  /** Which dual-axis side-panel variable to pre-select as the PRIMARY axis
   *  (a `VariableDef.id` from the skewt variable registry, e.g.
   *  'vertical_velocity', 'relative_humidity', 'richardson'). */
  skewtSidePanel?: string;

  // NB: there is deliberately no `highlights`/`emphasize` directive here. The
  // scrim + verdict ribbon (#373) need a concrete advisory instance and so ride
  // the chip/deep-link path via `activeHighlightAdvisoryId`, not the static
  // preset config (see the file header). The scrim also subsumes the old
  // `emphasize` idea — it dims by region, de-emphasizing even the relevant
  // layer's non-affected extent, which per-layer alpha could not.
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
  // Freezing precip: the SLD/warm-nose bands ARE the hazard signature (#375).
  freezing_precip: { lines: ['sld-bands'] },
  // En-route precip: swap the route graph to the precipitation trace (#375).
  enroute_precip: { routeGraph: { left: 'precipitation', right: 'ceiling-nwp' } },
};

export const ADVISORY_PRESETS: Record<string, AdvisoryPreset> = {
  basic: {
    id: 'basic',
    label: 'Basic / Learn',
    caption: 'Temperature, dewpoint, and the parcel path with LCL/LFC/EL — no hazard bands.',
    interpretation:
      'The two solid lines are temperature (right) and dewpoint (left); where they '
      + 'pinch together the air is near saturation (cloud). The black dashed line is the '
      + 'parcel — air lifted from the surface. LCL is where it first saturates (cloud base), '
      + 'LFC where it becomes buoyant, EL where it stops rising. The wider the gap between '
      + 'temperature and dewpoint, the drier and clearer that layer. Start here, then switch '
      + 'to a hazard lens (Icing / Clouds / Convective / Turbulence) to shade what matters.',
    // Cross-section: a clean slate — just terrain + cruise + the 0 °C line for orientation.
    lines: ['freezing-level'],
    // Skew-T: no hazard bands; the always-drawn T/Td/parcel + LCL/LFC/EL markers carry it.
    skewtOverlays: [],
  },
  icing: {
    id: 'icing',
    label: 'Icing',
    caption: 'Icing bands vs the 0 °C line and terrain — is there an ice-free descent?',
    interpretation:
      'Ice forms where the air is in cloud AND between 0 °C and about −20 °C. The shaded '
      + 'icing bands mark those layers; the 0 °C (freezing-level) marker is the top of any '
      + 'ice-free air below. Look for a gap — a level that is either above freezing or out of '
      + 'cloud — that gives an ice-free climb or descent. RH on the side panel confirms whether '
      + 'a band is genuinely saturated (real ice) or just cold dry air (no ice).',
    groups: ['icing', 'clouds'],
    lines: ['freezing-level'],
    routeGraph: { left: 'freezing-level', right: 'ceiling-nwp' },
    map: { metric: 'icing-risk-at-level', altitude: 'cruise' },
    skewtOverlays: ['icing-nwp', 'clouds-nwp'],
    skewtSidePanel: 'relative_humidity',
  },
  clouds: {
    id: 'clouds',
    label: 'Clouds',
    caption: 'Cloud tops & coverage vs your cruise level.',
    interpretation:
      'The shaded cloud bands are layers the model (or the dewpoint-depression method) '
      + 'reports as cloudy; their top and base are the tops and bases you would fly through. '
      + 'On the Skew-T, cloud sits where temperature and dewpoint nearly touch. RH on the side '
      + 'panel shows how solidly each layer is saturated — a thin near-saturated layer is haze '
      + 'or thin stratus; a deep saturated column is solid overcast.',
    groups: ['clouds'],
    lines: ['freezing-level'],
    routeGraph: { left: 'cloud-cover', right: 'ceiling-nwp' },
    map: { metric: 'cloud-at-level', altitude: 'cruise' },
    skewtOverlays: ['clouds-nwp'],
    skewtSidePanel: 'relative_humidity',
  },
  convective: {
    id: 'convective',
    label: 'Convective',
    caption: 'Towers framed by LCL→LFC→EL and instability along route.',
    interpretation:
      'The black dashed line is the parcel; where it sits to the right of the temperature '
      + 'line, that shaded area is CAPE — the energy available for towers, framed by LCL (base), '
      + 'LFC (where it runs away) and EL (the top it can reach). Omega on the right shows whether '
      + 'the air is being lifted (positive w, up) or held down (subsiding): high CAPE with lift = '
      + 'going off; high CAPE while subsiding/capped = energy that may never release.',
    groups: ['convection', 'clouds'],
    // NB: stability-line layer ids are 'lcl'/'lfc'/'el' (not '*-line' — that
    // suffix is only used by the compare-mode display registry).
    lines: ['lcl', 'lfc', 'el', 'freezing-level', 'minus-10c', 'minus-20c'],
    routeGraph: { left: 'cape', right: 'precipitation' },
    map: { metric: 'convective-risk', altitude: 'cruise' },
    skewtOverlays: ['convective', 'clouds-nwp'],
    // omega belongs on Convective specifically — w_fpm, positive = up (#308).
    skewtSidePanel: 'vertical_velocity',
  },
  turbulence: {
    id: 'turbulence',
    label: 'Turbulence',
    caption: 'CAT/shear layers near cruise; terrain + wind for orographic risk.',
    interpretation:
      'Bumps come from shear (wind changing fast with height) and from inversions (a warm cap '
      + 'where the temperature line kinks back to the right). The shaded inversion bands mark those '
      + 'caps; the Richardson number on the side panel is low where shear wins over stability — that '
      + 'is the turbulent layer. A strong inversion with wind shear just above it is the classic '
      + 'mountain-wave / clear-air-turbulence setup.',
    groups: ['turbulence'],
    lines: ['inversion-bands'],
    routeGraph: { left: 'headwind', right: 'crosswind' },
    map: { metric: 'cat-risk-at-level', altitude: 'cruise' },
    skewtOverlays: ['inversions'],
    skewtSidePanel: 'richardson',
  },
  vfr: {
    id: 'vfr',
    label: 'VFR feasibility',
    caption: 'VMC picture: clouds & obscuration vs cruise and airports.',
    interpretation:
      'For VFR the question is staying clear of cloud with the surface in sight. The cloud bands '
      + 'show where you would lose VMC; watch for a continuous layer (no VFR-on-top gap) and for '
      + 'cloud sitting near the surface (low ceilings / obscuration). RH near the surface rising '
      + 'toward 100 % is the sign of forming or lowering cloud.',
    groups: ['clouds'],
    lines: ['surface-obscuration-bands', 'freezing-level'],
    routeGraph: { left: 'cloud-cover', right: 'ceiling-nwp' },
    map: { metric: 'nwp-ceiling' },
    skewtOverlays: ['clouds-nwp'],
    skewtSidePanel: 'relative_humidity',
  },
  ifr: {
    id: 'ifr',
    label: 'IFR feasibility',
    caption: 'IFR hazards: icing + convection + cloud along route.',
    interpretation:
      'In IMC the two things that bite are ice (cloud between 0 °C and −20 °C) and embedded '
      + 'convection (CAPE you cannot see and avoid). The icing and convective bands shade both; '
      + 'the parcel path and CAPE shading show whether the cloud is benign stratiform or has '
      + 'buoyant energy behind it. Look for an icing-free altitude you could hold or divert to.',
    groups: ['icing', 'convection', 'clouds'],
    lines: ['freezing-level', 'minus-10c'],
    routeGraph: { left: 'cape', right: 'freezing-level' },
    map: { metric: 'icing-risk-at-level', altitude: 'cruise' },
    skewtOverlays: ['icing-nwp', 'convective', 'clouds-nwp'],
    skewtSidePanel: 'relative_humidity',
  },
};

/**
 * advisory_id -> preset id (used by the card chip). Only advisories listed here
 * get a chip. Phase 1 covers the cross-section-hazard advisories; airport,
 * model-quality, and fronts advisories are deferred (they need other action
 * types — see the issue's "Out of scope").
 */
export const ADVISORY_TO_PRESET: Record<string, string> = {
  icing_escape: 'icing', fiki_icing: 'icing', freezing_precip: 'icing',
  cloud_top: 'clouds', vmc_cruise: 'clouds',
  convective: 'convective',
  turbulence: 'turbulence', mountain_wind: 'turbulence',
  vfr_feasibility: 'vfr', ifr_feasibility: 'ifr',
  // enroute_precip is a visibility proxy → the VFR lens (clouds + obscuration
  // + ceilings) reads better than bare Clouds; the override above swaps the
  // route graph to precipitation (#375).
  enroute_precip: 'vfr',
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
 * Localized "how to read it" interpretation blurb for a preset (#308 Phase B).
 * Same key/fallback scheme as the label/caption; falls back to the preset's
 * `interpretation` literal, then to its `caption` when no longer blurb exists.
 * This is the single source of text shared by the human (via the (i) /
 * "Help me read this graph" button) and the MCP explanation.
 */
export function advisoryPresetInterpretation(p: AdvisoryPreset): string {
  const key = `viz.advisoryPreset.${p.id}.interpretation`;
  const s = t(key);
  if (s !== key) return s;
  return p.interpretation ?? advisoryPresetCaption(p);
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
    // arrays union (so FIKI adds to icing's lines/bands rather than replacing them)
    groups: [...(base.groups ?? []), ...(override.groups ?? [])],
    lines: [...(base.lines ?? []), ...(override.lines ?? [])],
    skewtOverlays: override.skewtOverlays
      ? [...new Set([...(base.skewtOverlays ?? []), ...override.skewtOverlays])]
      : base.skewtOverlays,
    routeGraph: override.routeGraph ?? base.routeGraph,
    map: override.map ?? base.map,
    skewtSidePanel: override.skewtSidePanel ?? base.skewtSidePanel,
    // identity stays the base preset's so the dropdown reflects 'icing', not a
    // per-advisory pseudo-id, and the caption/interpretation are the shared ones.
    id: base.id,
    label: base.label,
    caption: base.caption,
    interpretation: base.interpretation,
  };
}

/** A resolved, concrete view the store applies field-by-field. Adding a future
 *  directive = add a field here + a branch in the store applier; the resolver
 *  loop below is otherwise unchanged. */
export interface ResolvedView {
  enabledLayers?: Record<string, boolean>;
  routeGraph?: { left?: string; right?: string };
  map?: { metric?: string; altitudeFt?: number | null };
  /** Full clean-slate overlay state for the Skew-T (every known overlay id set
   *  to on/off), or undefined when the preset has no Skew-T directive. */
  skewtOverlays?: Record<string, boolean>;
  /** Primary side-panel variable id to select on the Skew-T. */
  skewtSidePanel?: string;
  // No highlight/emphasize field: the scrim + ribbon (#373) are keyed to a live
  // advisory instance and flow through `activeHighlightAdvisoryId`, not a
  // resolved preset view.
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
  cloudStyle: CloudStyle = 'square',
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
      enabled[getPreferredLayerForGroup(g, groupLayers, preferredMethods[g], cloudStyle).id] = true;
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

  // Skew-T overlay bands: clean-slate like the cross-section `groups` — every
  // known overlay id reset OFF, then the preset's listed bands turned ON, so the
  // lens shades only what the hazard needs. An empty `skewtOverlays` array
  // (Basic/Learn) therefore resolves to "all bands off". The always-drawn
  // T/Td/parcel curves and LCL/LFC/EL markers are not overlays, so they survive.
  if (preset.skewtOverlays) {
    const overlays: Record<string, boolean> = {};
    for (const o of SKEWT_OVERLAYS) overlays[o.id] = false;
    for (const id of preset.skewtOverlays) overlays[id] = true;
    view.skewtOverlays = overlays;
  }
  if (preset.skewtSidePanel) view.skewtSidePanel = preset.skewtSidePanel;

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
