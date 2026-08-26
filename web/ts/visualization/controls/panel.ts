/** Visualization control panel: layout toggle, model selector, layer checkboxes, map controls. */

import type { VizLayout, VizSettings, CompareBandMode, LayerGroup } from '../types';
import type { DisplayMode } from '../../types/metrics';
import { getLayerGroups, getPreferredLayerForGroup, getPresets, getPreset } from '../cross-section/layer-registry';
import { getAdvisoryPresets, getAdvisoryPreset, isAdvisoryPreset, advisoryPresetLabel, advisoryPresetCaption, advisoryPresetInterpretation } from '../cross-section/advisory-presets';
import {
  CLOUD_LAYER_BY_AXES,
  ALL_CLOUD_LAYER_IDS,
  parseCloudLayerId,
} from '../cross-section/layers/cloud-bands-factory';
import { getDdSubstituteId } from '../cross-section/nwp-fallback';
import { getComparableLayerGroups, getComparableLayer } from '../cross-section/compare-layers';
import { showLayerInfo, showPopupContent } from '../../components/info-popup';
import { renderFrontsInfo } from '../../helpers/fronts-info';
import { modelLabel, escapeHtml } from '../../utils';
import { getMetricOptions } from '../route-graph/metrics';
import { getMapMetricOptions, MAP_METRIC_NONE } from '../route-map/metrics';
import { OBSERVED_OVERLAY_OPTIONS } from '../route-map/observed-overlay-geometry';
import { FORECAST_METRICS, METRIC_LABEL } from '../weather-map-format';
import { THEMES, getActiveThemeId, type ThemeId } from '../cross-section/theme';
import { showThemePreview } from '../cross-section/theme-preview';
import { t } from '../../i18n/i18n';

/** Groups that collapse to a single preferred-method toggle in compact mode. */
const COMPACT_GROUPS = new Set(['clouds', 'icing', 'turbulence', 'convection']);

/** Explanatory text for layer group info buttons. */
const GROUP_INFO: Record<string, () => string> = {
  clouds: () => t('viz.cloudMethods'),
  convection: () => t('viz.convectionMethods'),
  icing: () => t('viz.icingMethods'),
  obscuration: () => t('viz.obscurationMethod'),
};

/** Options shared between the main toolbar's layer-toggle block and the
 *  standalone {@link renderLayerToggles} helper. */
export interface LayerTogglesOptions {
  displayMode?: DisplayMode;
  preferredMethods?: Record<string, string>;
  unavailableLayers?: Set<string>;
  /** Layers auto-substituted at render time (DD standing in for unavailable
   *  NWP). Rendered as checked + dimmed with an explanatory tooltip so the
   *  panel state matches what's actually drawn. */
  substitutedLayers?: Set<string>;
  /** Persisted cloud-style preference; the compound control falls back to
   *  this when no cloud layers are enabled, so the dropdown remembers the
   *  user's last choice across re-renders and page reloads. */
  cloudStyle?: 'natural' | 'soft' | 'square';
  /** Fired when the user picks a different cloud style. Wire to the store
   *  so the choice persists alongside other viz settings. */
  onCloudStyleChange?: (style: 'natural' | 'soft' | 'square') => void;
  /** Layer groups to omit entirely from the toggle list. Used by the
   *  airport-profile drawer to drop the route-only `conditions` group. */
  hiddenGroups?: Set<LayerGroup>;
}

/** Source state derived from which cloud layer ids are currently enabled.
 *  When no cloud layers are enabled the style falls back to `persistedStyle`
 *  (the value remembered in `vizSettings.cloudStyle`) so re-checking a
 *  source restores the user's last choice instead of the 'square' default. */
function cloudState(
  enabledLayers: Record<string, boolean>,
  persistedStyle: 'natural' | 'soft' | 'square' = 'square',
): {
  ddEnabled: boolean;
  nwpEnabled: boolean;
  style: 'natural' | 'soft' | 'square';
} {
  let ddEnabled = false;
  let nwpEnabled = false;
  let style: 'natural' | 'soft' | 'square' = persistedStyle;
  for (const id of ALL_CLOUD_LAYER_IDS) {
    if (!enabledLayers[id]) continue;
    const axes = parseCloudLayerId(id);
    if (!axes) continue;
    if (axes.source === 'dd') ddEnabled = true;
    if (axes.source === 'nwp') nwpEnabled = true;
    style = axes.style;  // last-wins; all enabled cloud layers share the same style by construction
  }
  return { ddEnabled, nwpEnabled, style };
}

/** Render the compound clouds control: per-source checkboxes + shared style dropdown. */
function cloudCompoundHtml(
  enabledLayers: Record<string, boolean>,
  unavailable: Set<string> | undefined,
  persistedStyle: 'natural' | 'soft' | 'square' | undefined,
  substituted: Set<string> | undefined,
): string {
  const { ddEnabled, nwpEnabled, style } = cloudState(enabledLayers, persistedStyle);
  // A source is "unavailable" iff its natural-style id (the canonical
  // data signal) is in the unavailable set. DD is sounding-derived so
  // generally always available; NWP requires native cloud-cover data.
  const ddUnavail = unavailable?.has('cloud-bands') ?? false;
  const nwpUnavail = unavailable?.has('nwp-cloud-bands') ?? false;
  // DD is auto-on (standing in for NWP) when the current-style DD layer was
  // injected by the render-time fallback rather than picked by the user.
  const ddSubstituted = substituted?.has(CLOUD_LAYER_BY_AXES.dd[style]) ?? false;

  const sourceCheckbox = (
    source: 'dd' | 'nwp',
    label: string,
    checked: boolean,
    unavail: boolean,
    auto: boolean,
  ): string => {
    const dimClass = unavail ? ' viz-layer-unavailable' : (auto ? ' viz-layer-substituted' : '');
    const tooltip = unavail
      ? ` title="${t('viz.notAvailableModel')}"`
      : (auto ? ` title="${t('viz.substitutedNwp')}"` : '');
    const disabled = unavail ? 'disabled' : '';
    const checkedAttr = (checked || auto) && !unavail ? 'checked' : '';
    return `<label class="viz-layer-checkbox${dimClass}"${tooltip}>`
      + `<input type="checkbox" data-cloud-source="${source}" ${checkedAttr} ${disabled}>`
      + `<span>${label}</span>`
      + `</label>`;
  };

  let html = '';
  html += sourceCheckbox('nwp', 'NWP', nwpEnabled, nwpUnavail, false);
  html += sourceCheckbox('dd', 'DD', ddEnabled, ddUnavail, ddSubstituted);
  html += `<select class="viz-model-select" data-cloud-style>`;
  html += `<option value="square"${style === 'square' ? ' selected' : ''}>${t('viz.cloudStyle.square')}</option>`;
  html += `<option value="natural"${style === 'natural' ? ' selected' : ''}>${t('viz.cloudStyle.natural')}</option>`;
  html += `<option value="soft"${style === 'soft' ? ' selected' : ''}>${t('viz.cloudStyle.soft')}</option>`;
  html += `</select>`;
  return html;
}

/** Build the `<div class="viz-layer-toggles">...</div>` block for a set of
 *  enabled-layer states. Used inline by the main toolbar and by
 *  {@link renderLayerToggles} for callers that only want this section. */
/** Pure HTML builder for the layer panel. Exported so the group-composition
 *  rules can be tested without a DOM — notably that a group with a bespoke
 *  compound control still renders checkboxes for the layers that control does
 *  not own. */
export function layerTogglesHtml(
  enabledLayers: Record<string, boolean>,
  opts: LayerTogglesOptions = {},
): string {
  const { displayMode, preferredMethods, unavailableLayers, cloudStyle, substitutedLayers, hiddenGroups } = opts;
  // "Feature/context" groups hide entirely when EVERY layer in them is
  // unavailable — front detection off or no on-track crossing for this model;
  // no D-0 observations and no observed frames. A group of disabled checkboxes
  // is just noise. (Other groups keep their layers visible-but-dimmed so
  // alternative methods stay discoverable.)
  //
  // This used to name one layer per group and hide the group when that layer
  // was unavailable. Adding the observed-surface layer (#574) to `conditions`
  // made that wrong: with no METAR the whole group vanished, taking a
  // perfectly good radar layer with it. Now the group hides only when nothing
  // in it is available, and individual layers dim as everywhere else.
  const HIDE_GROUP_WHEN_ALL_UNAVAILABLE: readonly LayerGroup[] = ['fronts', 'conditions'];
  const groups = getLayerGroups();
  const effectiveHidden = new Set<LayerGroup>(hiddenGroups ?? []);
  for (const groupId of HIDE_GROUP_WHEN_ALL_UNAVAILABLE) {
    const group = groups.find((g) => g.group === groupId);
    if (!group || group.layers.length === 0) continue;
    if (group.layers.every((l) => unavailableLayers?.has(l.id))) effectiveHidden.add(groupId);
  }
  let html = '<div class="viz-layer-toggles">';
  for (const group of groups) {
    if (effectiveHidden.has(group.group)) continue;
    const isCompactCollapse = displayMode === 'compact' && COMPACT_GROUPS.has(group.group);
    const layersToRender = isCompactCollapse
      ? [getPreferredLayerForGroup(group.group, group.layers, preferredMethods?.[group.group], cloudStyle)]
      : group.layers;

    html += `<div class="viz-layer-group">`;
    html += `<span class="viz-group-label">${group.label}:</span>`;
    if (GROUP_INFO[group.group]) {
      html += `<button class="viz-layer-info-btn viz-group-info-btn" data-group-info="${group.group}" title="About ${group.label}" aria-label="About ${group.label}">ⓘ</button>`;
    }
    // Clouds group in non-compact mode: compound source-toggles + style dropdown.
    //
    // The compound control owns ONLY the NWP/DD cloud-band ids. Any other
    // layer that lives in this group still needs its own checkbox, so fall
    // through to the normal loop for the remainder rather than `continue`ing
    // past it — which is how `observed-tops` (#574) ended up rendering with no
    // way to switch it off at all.
    let layersForGroup = layersToRender;
    if (group.group === 'clouds' && !isCompactCollapse) {
      html += cloudCompoundHtml(enabledLayers, unavailableLayers, cloudStyle, substitutedLayers);
      layersForGroup = layersToRender.filter((l) => !ALL_CLOUD_LAYER_IDS.includes(l.id));
      if (layersForGroup.length === 0) {
        html += '</div>';
        continue;
      }
    }
    for (const layer of layersForGroup) {
      const isUnavailable = unavailableLayers?.has(layer.id) ?? false;
      // In compact mode the group collapses to its preferred layer; if that's
      // an NWP layer being served by a DD substitute, show the substitute state
      // (checked+dimmed) instead of "unavailable" / a plain checkbox.
      const compactSubId = isCompactCollapse ? getDdSubstituteId(layer.id) : null;
      const isSubstituted = (compactSubId != null && (substitutedLayers?.has(compactSubId) ?? false))
        || (!isUnavailable && (substitutedLayers?.has(layer.id) ?? false));
      const showUnavailable = isUnavailable && !isSubstituted;
      const checked = isSubstituted || (!showUnavailable && enabledLayers[layer.id] !== false) ? 'checked' : '';
      const disabled = showUnavailable ? 'disabled' : '';
      const dimClass = showUnavailable
        ? ' viz-layer-unavailable'
        : (isSubstituted ? ' viz-layer-substituted' : '');
      const tooltip = showUnavailable
        ? ` title="${t('viz.notAvailableModel')}"`
        : (isSubstituted ? ` title="${t('viz.substitutedNwp')}"` : '');
      html += `<label class="viz-layer-checkbox${dimClass}"${tooltip}>`;
      html += `<input type="checkbox" data-layer-id="${layer.id}" ${checked} ${disabled}>`;
      html += `<span>${isCompactCollapse ? group.label : t('viz.layer.' + layer.id)}</span>`;
      html += `</label>`;
      if (layer.metricId) {
        html += `<button class="viz-layer-info-btn" data-layer-info="${layer.id}" data-metric-id="${layer.metricId}" title="${t('viz.moreInfo')}" aria-label="${t('viz.moreInfo')}">ⓘ</button>`;
      } else if (layer.id === 'fronts-markers') {
        // Fronts have no metric registry entry — show the experimental-feature
        // explainer instead of metric info.
        html += `<button class="viz-layer-info-btn" data-front-info="1" title="${t('viz.moreInfo')}" aria-label="${t('viz.moreInfo')}">ⓘ</button>`;
      }
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

/** Wire the compound clouds control. Per-source checkboxes (DD, NWP) toggle
 *  each source independently; the shared style dropdown swaps the active
 *  layer-id for any source that's currently on. */
function wireCloudCompound(
  container: HTMLElement,
  enabledLayers: Record<string, boolean>,
  onToggle: (layerId: string) => void,
  onStyleChange?: (style: 'natural' | 'soft' | 'square') => void,
): void {
  const sourceCbs = container.querySelectorAll<HTMLInputElement>('[data-cloud-source]');
  const styleSel = container.querySelector<HTMLSelectElement>('[data-cloud-style]');
  if (sourceCbs.length === 0 || !styleSel) return;

  // Layer id (if any) currently enabled for a given source.
  const enabledIdFor = (source: 'dd' | 'nwp'): string | null => {
    for (const id of ALL_CLOUD_LAYER_IDS) {
      if (!enabledLayers[id]) continue;
      const axes = parseCloudLayerId(id);
      if (axes?.source === source) return id;
    }
    return null;
  };

  for (const cb of Array.from(sourceCbs)) {
    cb.addEventListener('change', () => {
      const source = cb.dataset.cloudSource as 'dd' | 'nwp';
      const style = styleSel.value as 'natural' | 'soft' | 'square';
      const currentId = enabledIdFor(source);
      if (cb.checked) {
        const targetId = CLOUD_LAYER_BY_AXES[source][style];
        if (currentId && currentId !== targetId) onToggle(currentId);
        if (currentId !== targetId) onToggle(targetId);
      } else if (currentId) {
        onToggle(currentId);
      }
    });
  }

  styleSel.addEventListener('change', () => {
    const newStyle = styleSel.value as 'natural' | 'soft' | 'square';
    // Persist the choice even if no sources are enabled — otherwise picking
    // a style with everything unchecked produces no toggle event and the
    // next re-render would discard the selection.
    onStyleChange?.(newStyle);
    // For each source that's currently enabled, swap from its current
    // style-layer to the new style-layer. Sources that are off stay off.
    for (const source of ['dd', 'nwp'] as const) {
      const currentId = enabledIdFor(source);
      if (!currentId) continue;
      const targetId = CLOUD_LAYER_BY_AXES[source][newStyle];
      if (currentId !== targetId) {
        onToggle(currentId);
        onToggle(targetId);
      }
    }
  });
}

/** Wire `data-layer-info` and `data-group-info` buttons inside `container`
 *  to their respective info popups. Used by both the main toolbar and the
 *  standalone {@link renderLayerToggles} so an info ⓘ is interactive
 *  wherever the toggles render. */
function wireLayerInfoButtons(container: HTMLElement): void {
  container.querySelectorAll('[data-layer-info]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const el = btn as HTMLElement;
      const layerId = el.dataset.layerInfo!;
      const metricId = el.dataset.metricId!;
      showLayerInfo(layerId, metricId);
    });
  });
  container.querySelectorAll('[data-group-info]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const groupKey = (btn as HTMLElement).dataset.groupInfo!;
      const infoFn = GROUP_INFO[groupKey];
      if (infoFn) showPopupContent(infoFn());
    });
  });
  container.querySelectorAll('[data-front-info]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showPopupContent(renderFrontsInfo());
    });
  });
}

/** Render layer toggle checkboxes into a standalone container and wire
 *  change events + ⓘ info popups. For callers that only need the
 *  layer-selection part of the main toolbar (e.g. the airport-profile
 *  panel's settings drawer). */
export function renderLayerToggles(
  container: HTMLElement,
  enabledLayers: Record<string, boolean>,
  onToggle: (layerId: string) => void,
  opts: LayerTogglesOptions = {},
): void {
  container.innerHTML = layerTogglesHtml(enabledLayers, opts);
  container.querySelectorAll<HTMLInputElement>('input[data-layer-id]').forEach((cb) => {
    cb.addEventListener('change', () => onToggle(cb.dataset.layerId!));
  });
  wireCloudCompound(container, enabledLayers, onToggle, opts.onCloudStyleChange);
  wireLayerInfoButtons(container);
}

export interface VizControlCallbacks {
  onLayerToggle: (layerId: string) => void;
  onLayoutChange: (layout: VizLayout) => void;
  onModelChange?: (model: string) => void;
  onThemeChange?: (themeId: string) => void;
  onPresetChange?: (presetId: string | null) => void;
  onCloudStyleChange?: (style: 'natural' | 'soft' | 'square') => void;
}

export interface RouteGraphControlCallbacks {
  onRouteGraphToggle: (visible: boolean) => void;
  onRouteGraphMetricChange: (axis: 'left' | 'right', metricId: string) => void;
  /** Corridor width for the observed metrics (#574). Every sampled radius is
   *  already in the payload, so this is a re-render, never a re-fetch. */
  onObservedRadiusChange?: (radiusNm: number) => void;
}

export interface MapControlCallbacks {
  onColorMetricChange: (metricId: string) => void;
  onWidthMetricChange: (metricId: string) => void;
  /** Toggle the experimental Hewson front overlay (#196). */
  onFrontsToggle?: (visible: boolean) => void;
  /** Toggle the airport forecast overlay (#424). */
  onForecastOverlayToggle?: (visible: boolean) => void;
  /** Change the metric the airport forecast overlay colours by (#424). */
  onForecastMetricChange?: (metricId: string) => void;
  /** Pick which observed layer the map draws (#574), '' for none. */
  onObservedOverlayChange?: (source: string) => void;
  /** Set the observed imagery opacity, 0–1. */
  onObservedOpacityChange?: (opacity: number) => void;
}

/** Which observed sources the current briefing actually carries. Options for
 *  anything absent are not offered: an overlay that renders an empty PNG is
 *  worse than a missing menu entry, because the pilot cannot tell "nothing
 *  there" from "not collected". */
export interface ObservedAvailability {
  reflectivity: boolean;
  rainRate: boolean;
  cloudTops: boolean;
  lightning: boolean;
}

/** Airport forecast overlay control state (#424). Present (and the cluster
 *  rendered) only when the flight is within the forecast horizon; absent when
 *  it is more than a week out. */
export interface MapForecastOverlayControls {
  /** Flight is within D-0..D-6 and has a snapshot to show. */
  available: boolean;
  /** User's show/hide preference. */
  visible: boolean;
  /** Active `FORECAST_METRICS` id. */
  metric: string;
  /** Short label for the snapshot valid-time (e.g. "Wed 12Z"); undefined while loading. */
  timeLabel?: string;
  /** Deep-link to the full forecast map seeded with day/hour/model/metric. */
  fullMapUrl?: string;
  /** The briefing's selected model has airport data for this day. When false the
   *  map is empty because of the model, not the weather — the cluster shows an
   *  explanatory note instead of the metric/time so a blank map never reads as
   *  "all clear" (designs/forecast-page.md). */
  modelSupported: boolean;
  /** Selected model id, for the "no data" note. */
  model: string;
}

export function renderVizControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: VizControlCallbacks,
  selectedModel?: string,
  availableModels?: string[],
  displayMode?: DisplayMode,
  preferredMethods?: Record<string, string>,
  unavailableLayers?: Set<string>,
  substitutedLayers?: Set<string>,
  hiddenGroups?: Set<LayerGroup>,
): void {
  let html = '<div class="viz-toolbar">';

  // Top row: Layout toggle + Model indicator + Render mode toggle
  html += '<div class="viz-toolbar-top">';

  // Layout toggle
  html += '<div class="viz-layout-toggle">';
  html += `<button class="btn-toggle${settings.layout === 'cross-section' ? ' active' : ''}" data-layout="cross-section" title="Cross-section only">${t('viz.xSection')}</button>`;
  html += `<button class="btn-toggle${settings.layout === 'compare' ? ' active' : ''}" data-layout="compare" title="Compare one layer across all models">${t('viz.compare')}</button>`;
  html += `<button class="btn-toggle${settings.layout === 'split' ? ' active' : ''}" data-layout="split" title="Side-by-side">${t('viz.split')}</button>`;
  html += `<button class="btn-toggle${settings.layout === 'map' ? ' active' : ''}" data-layout="map" title="Map only">${t('viz.map')}</button>`;
  html += '</div>';

  // Model selector
  if (selectedModel && availableModels && availableModels.length > 0) {
    html += `<div class="viz-model-selector">`;
    html += `<span class="viz-toggle-label">${t('viz.model')}</span>`;
    html += `<select id="viz-model-select" class="viz-model-select">`;
    for (const m of availableModels) {
      const selected = m === selectedModel ? ' selected' : '';
      html += `<option value="${m}"${selected}>${modelLabel(m)}</option>`;
    }
    html += `</select>`;
    html += `</div>`;
  } else if (selectedModel) {
    html += `<div class="viz-model-selector">`;
    html += `<span class="viz-toggle-label">${t('viz.model')}</span>`;
    html += `<span class="viz-model-name">${modelLabel(selectedModel)}</span>`;
    html += `</div>`;
  }

  // Theme selector
  if (settings.layout !== 'map') {
    const currentTheme = getActiveThemeId();
    html += `<div class="viz-theme-selector">`;
    html += `<span class="viz-toggle-label">${t('viz.theme')}</span>`;
    html += `<select id="viz-theme-select" class="viz-model-select">`;
    for (const [id, theme] of Object.entries(THEMES)) {
      const selected = id === currentTheme ? ' selected' : '';
      html += `<option value="${id}"${selected}>${theme.label}</option>`;
    }
    html += `</select>`;
    html += `<button id="viz-theme-preview" class="viz-layer-info-btn" title="${t('viz.previewTheme')}">\u{1f441}</button>`;
    html += `</div>`;
    // Preset selector. Reflection-label model (#219): the dropdown reflects
    // `settings.activePreset` via `selected`; "Custom" is selected (and is a
    // non-actionable label for the dirty state) when no preset is active.
    const presets = getPresets();
    const advisoryPresets = getAdvisoryPresets();
    const active = settings.activePreset ?? null;
    if (presets.length > 0 || advisoryPresets.length > 0) {
      html += `<div class="viz-theme-selector">`;
      html += `<span class="viz-toggle-label">${t('viz.preset')}</span>`;
      html += `<select id="viz-preset-select" class="viz-model-select">`;
      html += `<option value=""${active === null ? ' selected' : ''}>${t('viz.presetCustom')}</option>`;
      for (const preset of presets) {
        html += `<option value="${preset.id}"${preset.id === active ? ' selected' : ''}>${preset.label}</option>`;
      }
      if (advisoryPresets.length > 0) {
        html += `<optgroup label="${t('viz.presetGroupAdvisory')}">`;
        for (const preset of advisoryPresets) {
          html += `<option value="${preset.id}"${preset.id === active ? ' selected' : ''}>${advisoryPresetLabel(preset)}</option>`;
        }
        html += `</optgroup>`;
      }
      html += `</select></div>`;
    }
  }

  // Windy link placeholder (updated dynamically by updateWindyLink)
  html += `<span id="external-links" class="external-links" style="display: none;">`;
  html += `${t('viz.externalLinks')}<a id="windy-link" href="#" target="_blank" rel="noopener">${t('viz.windy')}</a>`;
  html += `</span>`;

  html += '</div>'; // .viz-toolbar-top

  // Layer toggles — only when cross-section visible. The HTML is built
  // by `layerTogglesHtml()` (also reused by `renderLayerToggles()` for
  // the airport-profile drawer); change listeners are wired below.
  if (settings.layout !== 'map') {
    html += layerTogglesHtml(settings.enabledLayers, {
      displayMode, preferredMethods, unavailableLayers, substitutedLayers,
      cloudStyle: settings.cloudStyle, hiddenGroups,
    });
  }
  html += '</div>'; // .viz-toolbar

  // Advisory-preset caption (#219): a one-line note explaining the active view.
  // Only emitted for advisory presets; absent for GRAMET/Custom (the panel
  // re-renders on every settings change, so it clears itself). Captions are
  // trusted static config literals today, but escape defensively so a future
  // i18n/API-sourced caption can't inject markup.
  if (settings.layout !== 'map' && isAdvisoryPreset(settings.activePreset)) {
    const ap = getAdvisoryPreset(settings.activePreset!);
    if (ap) {
      // Caption + a "Help me read this" (i) button surfacing the longer
      // interpretation blurb (#308 Phase B) — same text the Skew-T and the MCP
      // explanation use.
      html += `<div class="viz-preset-caption">${escapeHtml(advisoryPresetCaption(ap))}`
        + `<button class="viz-layer-info-btn viz-preset-help-btn" data-preset-help="${ap.id}" `
        + `title="${t('viz.helpReadGraph')}" aria-label="${t('viz.helpReadGraph')}">ⓘ</button></div>`;
    }
  }

  container.innerHTML = html;

  // Wire layout toggle
  container.querySelectorAll('[data-layout]').forEach((btn) => {
    btn.addEventListener('click', () => {
      callbacks.onLayoutChange((btn as HTMLElement).dataset.layout as VizLayout);
    });
  });

  // Wire the preset "Help me read this" (i) button → interpretation popup (#308).
  container.querySelectorAll('[data-preset-help]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = (btn as HTMLElement).dataset.presetHelp!;
      const ap = getAdvisoryPreset(id);
      if (!ap) return;
      showPopupContent(
        `<div class="skewt-help-popup"><h3>${escapeHtml(advisoryPresetLabel(ap))}</h3>`
        + `<p>${escapeHtml(advisoryPresetInterpretation(ap))}</p></div>`,
      );
    });
  });

  // Wire layer toggles
  container.querySelectorAll('[data-layer-id]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      callbacks.onLayerToggle((checkbox as HTMLInputElement).dataset.layerId!);
    });
  });

  // Wire compound clouds control (master + source + style).
  wireCloudCompound(container, settings.enabledLayers, callbacks.onLayerToggle, callbacks.onCloudStyleChange);

  // Wire model selector
  const vizModelSelect = container.querySelector('#viz-model-select') as HTMLSelectElement | null;
  if (vizModelSelect && callbacks.onModelChange) {
    const cb = callbacks.onModelChange;
    vizModelSelect.addEventListener('change', () => {
      cb(vizModelSelect.value);
    });
  }

  // Wire theme selector
  const vizThemeSelect = container.querySelector('#viz-theme-select') as HTMLSelectElement | null;
  if (vizThemeSelect && callbacks.onThemeChange) {
    const cb = callbacks.onThemeChange;
    vizThemeSelect.addEventListener('change', () => {
      cb(vizThemeSelect.value);
    });
  }
  const themePreviewBtn = container.querySelector('#viz-theme-preview') as HTMLButtonElement | null;
  if (themePreviewBtn) {
    themePreviewBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const themeId = vizThemeSelect?.value ?? getActiveThemeId();
      showThemePreview(themeId as ThemeId, { cloudStyle: settings.cloudStyle });
    });
  }
  // Wire preset selector
  const vizPresetSelect = container.querySelector('#viz-preset-select') as HTMLSelectElement | null;
  if (vizPresetSelect && callbacks.onPresetChange) {
    const presetCb = callbacks.onPresetChange;
    vizPresetSelect.addEventListener('change', () => {
      presetCb(vizPresetSelect.value || null);
    });
  }

  // Wire layer + group info ⓘ buttons (shared with renderLayerToggles).
  wireLayerInfoButtons(container);
}

/** Render route graph controls (toggle + metric dropdowns) into a separate container below the graph. */
export function renderRouteGraphControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: RouteGraphControlCallbacks,
  /** Sampled corridor widths (NM) from the observed payload. Empty (the
   *  default) hides the selector entirely — there is nothing to choose
   *  between when no observed data was collected. */
  observedRadiiNm: readonly number[] = [],
  /** The radius currently in effect, so the selector shows what is drawn
   *  even when settings hold the "widest" default. */
  activeObservedRadiusNm: number | null = null,
): void {
  const leftOptions = getMetricOptions(false);
  const rightOptions = getMetricOptions(true);
  const arrow = settings.routeGraphVisible ? '\u25BC' : '\u25B6';

  let html = '<div class="route-graph-controls">';

  // Toggle button
  html += `<button id="route-graph-toggle" class="route-graph-toggle-btn" title="Show/hide route graph">`;
  html += `<span class="route-graph-arrow">${arrow}</span> ${t('viz.routeGraph')}`;
  html += `</button>`;

  // Metric selectors (only shown when visible)
  if (settings.routeGraphVisible) {
    html += '<div class="route-graph-selectors">';

    html += '<label class="route-graph-select-label">';
    html += `<span class="viz-toggle-label">${t('viz.left')}</span>`;
    html += '<select id="route-graph-left-metric" class="route-graph-select">';
    for (const opt of leftOptions) {
      const selected = opt.id === settings.routeGraphLeftMetric ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
    }
    html += '</select>';
    html += '</label>';

    html += '<label class="route-graph-select-label">';
    html += `<span class="viz-toggle-label">${t('viz.right')}</span>`;
    html += '<select id="route-graph-right-metric" class="route-graph-select">';
    for (const opt of rightOptions) {
      const selected = opt.id === settings.routeGraphRightMetric ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
    }
    html += '</select>';
    html += '</label>';

    if (observedRadiiNm.length > 1) {
      const active = activeObservedRadiusNm ?? Math.max(...observedRadiiNm);
      html += '<label class="route-graph-select-label">';
      html += `<span class="viz-toggle-label">${t('observed.corridor')}</span>`;
      html += '<select id="route-graph-observed-radius" class="route-graph-select">';
      for (const radius of observedRadiiNm) {
        const selected = radius === active ? ' selected' : '';
        html += `<option value="${radius}"${selected}>${radius} NM</option>`;
      }
      html += '</select>';
      html += '</label>';
    }

    html += '</div>';
  }

  html += '</div>';

  container.innerHTML = html;

  // Wire toggle
  const graphToggle = container.querySelector('#route-graph-toggle') as HTMLButtonElement | null;
  if (graphToggle) {
    graphToggle.addEventListener('click', () => {
      callbacks.onRouteGraphToggle(!settings.routeGraphVisible);
    });
  }

  // Wire metric dropdowns
  const leftSelect = container.querySelector('#route-graph-left-metric') as HTMLSelectElement | null;
  if (leftSelect) {
    leftSelect.addEventListener('change', () => {
      callbacks.onRouteGraphMetricChange('left', leftSelect.value);
    });
  }
  const rightSelect = container.querySelector('#route-graph-right-metric') as HTMLSelectElement | null;
  if (rightSelect) {
    rightSelect.addEventListener('change', () => {
      callbacks.onRouteGraphMetricChange('right', rightSelect.value);
    });
  }
  const radiusSelect = container.querySelector('#route-graph-observed-radius') as HTMLSelectElement | null;
  if (radiusSelect && callbacks.onObservedRadiusChange) {
    radiusSelect.addEventListener('change', () => {
      callbacks.onObservedRadiusChange!(Number(radiusSelect.value));
    });
  }
}

/** Render map-specific controls (color + width metric dropdowns) into the map controls container. */
export function renderMapControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: MapControlCallbacks,
  frontsAvailable = false,
  forecastOverlay?: MapForecastOverlayControls,
  observed?: ObservedAvailability,
): void {
  const colorOptions = getMapMetricOptions(false);
  const widthOptions = getMapMetricOptions(true);

  let html = '<div class="map-controls">';

  html += '<label class="map-control-label">';
  html += `<span class="viz-toggle-label">${t('viz.color')}</span>`;
  html += '<select id="map-color-metric" class="map-control-select">';
  for (const opt of colorOptions) {
    const selected = opt.id === settings.mapColorMetric ? ' selected' : '';
    html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
  }
  html += '</select>';
  html += '</label>';

  html += '<label class="map-control-label">';
  html += `<span class="viz-toggle-label">${t('viz.width')}</span>`;
  html += '<select id="map-width-metric" class="map-control-select">';
  for (const opt of widthOptions) {
    const selected = opt.id === settings.mapWidthMetric ? ' selected' : '';
    html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
  }
  html += '</select>';
  html += '</label>';

  // Experimental Hewson front overlay — only surfaced when front data exists
  // for this briefing (i.e. the "Auto Front Detection" pref was on).
  if (frontsAvailable) {
    const checked = settings.mapFrontsVisible ? ' checked' : '';
    html += '<label class="map-control-label viz-toggle">';
    html += `<input type="checkbox" id="map-fronts-toggle"${checked}>`;
    html += `<span class="viz-toggle-label">${t('viz.fronts')}</span>`;
    html += '</label>';
  }

  // Airport forecast overlay (#424), right-aligned. Rendered only when the
  // flight is within the forecast horizon (otherwise nothing to show).
  if (forecastOverlay?.available) {
    const fo = forecastOverlay;
    const checked = fo.visible ? ' checked' : '';
    html += '<div class="map-forecast-controls">';
    html += '<label class="map-control-label viz-toggle">';
    html += `<input type="checkbox" id="map-forecast-toggle"${checked}>`;
    html += `<span class="viz-toggle-label">${t('viz.airports')}</span>`;
    html += '</label>';
    if (fo.modelSupported) {
      const timeLabel = fo.timeLabel ?? '…';
      html += `<span class="map-forecast-time" id="map-forecast-time" title="${escapeHtml(t('viz.airports.timeHint'))}">${escapeHtml(timeLabel)}</span>`;
      html += '<label class="map-control-label">';
      html += `<span class="viz-toggle-label">${t('viz.airports.show')}</span>`;
      html += '<select id="map-forecast-metric" class="map-control-select">';
      for (const m of FORECAST_METRICS) {
        const selected = m === fo.metric ? ' selected' : '';
        html += `<option value="${m}"${selected}>${escapeHtml(METRIC_LABEL[m])}</option>`;
      }
      html += '</select>';
      html += '</label>';
    } else {
      // Empty because the selected model has no airport data for this day, not
      // because the weather is clear — say so (forecast-page.md).
      html += `<span class="map-forecast-note">${escapeHtml(t('viz.airports.noModelData', { model: modelLabel(fo.model) }))}</span>`;
    }
    if (fo.fullMapUrl) {
      const openLabel = escapeHtml(t('viz.airports.openMap'));
      html += `<a class="map-forecast-open" id="map-forecast-open" href="${escapeHtml(fo.fullMapUrl)}" target="_blank" rel="noopener" title="${openLabel}" aria-label="${openLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg></a>`;
    }
    html += '</div>';
  }

  // Observed layer sits apart from Color/Width, and to the right: those two
  // style the FORECAST route line, this picks a MEASUREMENT of the real sky.
  // Grouped together they read as three knobs on one thing, which they are not.
  //
  // Outside the forecast-overlay block on purpose — nesting it there made the
  // selector appear only while the airport overlay happened to be on.
  //
  // One at a time: these are different measurements of the same sky, and
  // stacking them leaves a colour on the map whose source is ambiguous.
  if (observed && (observed.reflectivity || observed.rainRate || observed.cloudTops || observed.lightning)) {
    html += '<div class="map-controls-observed">';
    html += '<label class="map-control-label">';
    html += `<span class="viz-toggle-label">${t('viz.observed.label')}</span>`;
    html += '<select id="map-observed-overlay" class="map-control-select">';
    for (const opt of OBSERVED_OVERLAY_OPTIONS) {
      if (opt.needs && !observed[opt.needs]) continue;
      const selected = opt.id === settings.observedOverlay ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${escapeHtml(t(opt.labelKey))}</option>`;
    }
    html += '</select>';
    html += '</label>';
    // Opacity, like the synoptic grid layer's. These rasters cover the whole
    // corridor, so a fixed value either buries the basemap or washes the data
    // out depending on the product — and the pilot needs to read place names
    // through it.
    if (settings.observedOverlay && settings.observedOverlay !== 'eumetsat_li') {
      const pct = Math.round((settings.observedOverlayOpacity ?? 0.75) * 100);
      html += '<label class="map-control-label map-observed-opacity">';
      html += `<span class="viz-toggle-label">${escapeHtml(t('viz.observed.opacity'))}</span>`;
      html += `<input type="range" id="map-observed-opacity" min="10" max="100" step="5" value="${pct}">`;
      html += `<span class="map-observed-opacity-value" id="map-observed-opacity-value">${pct}%</span>`;
      html += '</label>';
    }
    html += '</div>';
  }

  html += '</div>';

  container.innerHTML = html;

  const forecastToggle = container.querySelector('#map-forecast-toggle') as HTMLInputElement | null;
  if (forecastToggle) {
    forecastToggle.addEventListener('change', () => {
      callbacks.onForecastOverlayToggle?.(forecastToggle.checked);
    });
  }
  const forecastMetricSelect = container.querySelector('#map-forecast-metric') as HTMLSelectElement | null;
  if (forecastMetricSelect) {
    forecastMetricSelect.addEventListener('change', () => {
      callbacks.onForecastMetricChange?.(forecastMetricSelect.value);
    });
  }

  const frontsToggle = container.querySelector('#map-fronts-toggle') as HTMLInputElement | null;
  if (frontsToggle) {
    frontsToggle.addEventListener('change', () => {
      callbacks.onFrontsToggle?.(frontsToggle.checked);
    });
  }

  // Wire metric dropdowns
  const observedOpacity = container.querySelector('#map-observed-opacity') as HTMLInputElement | null;
  if (observedOpacity && callbacks.onObservedOpacityChange) {
    const readout = container.querySelector('#map-observed-opacity-value') as HTMLElement | null;
    observedOpacity.addEventListener('input', () => {
      const pct = Number(observedOpacity.value);
      if (readout) readout.textContent = `${pct}%`;
      callbacks.onObservedOpacityChange!(pct / 100);
    });
  }

  const observedSelect = container.querySelector('#map-observed-overlay') as HTMLSelectElement | null;
  if (observedSelect && callbacks.onObservedOverlayChange) {
    observedSelect.addEventListener('change', () => {
      callbacks.onObservedOverlayChange!(observedSelect.value);
    });
  }

  const colorSelect = container.querySelector('#map-color-metric') as HTMLSelectElement | null;
  if (colorSelect) {
    colorSelect.addEventListener('change', () => {
      callbacks.onColorMetricChange(colorSelect.value);
    });
  }
  const widthSelect = container.querySelector('#map-width-metric') as HTMLSelectElement | null;
  if (widthSelect) {
    widthSelect.addEventListener('change', () => {
      callbacks.onWidthMetricChange(widthSelect.value);
    });
  }
}

// --- Compare mode controls ---

export interface CompareControlCallbacks {
  onLayoutChange: (layout: VizLayout) => void;
  onCompareLayerChange: (layerId: string) => void;
  onCompareModelToggle: (model: string, enabled: boolean) => void;
  onCompareBandModeChange: (mode: CompareBandMode) => void;
  onThemeChange?: (themeId: string) => void;
  onPresetChange?: (presetId: string | null) => void;
}

/** Render compare-mode controls: layout toggle, layer dropdown, model chips. */
export function renderCompareControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: CompareControlCallbacks,
  availableModels: string[],
): void {
  const layerGroups = getComparableLayerGroups();

  let html = '<div class="viz-toolbar">';

  // Top row: Layout toggle
  html += '<div class="viz-toolbar-top">';

  // Layout toggle (same 4 buttons, Compare active)
  html += '<div class="viz-layout-toggle">';
  html += `<button class="btn-toggle" data-layout="cross-section" title="Cross-section only">${t('viz.xSection')}</button>`;
  html += `<button class="btn-toggle active" data-layout="compare" title="Compare one layer across all models">${t('viz.compare')}</button>`;
  html += `<button class="btn-toggle" data-layout="split" title="Side-by-side">${t('viz.split')}</button>`;
  html += `<button class="btn-toggle" data-layout="map" title="Map only">${t('viz.map')}</button>`;
  html += '</div>';

  // Layer selector
  html += '<div class="viz-compare-layer-selector">';
  html += `<span class="viz-toggle-label">${t('viz.layer')}</span>`;
  html += '<select id="compare-layer-select" class="viz-model-select">';
  for (const group of layerGroups) {
    html += `<optgroup label="${group.group}">`;
    for (const layer of group.layers) {
      const selected = layer.id === settings.compareLayer ? ' selected' : '';
      html += `<option value="${layer.id}"${selected}>${t('viz.layer.' + layer.id)}</option>`;
    }
    html += '</optgroup>';
  }
  html += '</select>';
  html += '</div>';

  // Band mode selector (only visible when a band layer is selected)
  const selectedLayer = getComparableLayer(settings.compareLayer);
  const isBand = selectedLayer?.type === 'band';
  const bandModeStyle = isBand ? '' : ' style="display:none"';
  const bm = settings.compareBandMode ?? 'consensus-outline';
  html += `<div class="viz-band-mode-selector"${bandModeStyle}>`;
  html += `<span class="viz-toggle-label">${t('viz.bandMode')}</span>`;
  html += '<select id="compare-band-mode-select" class="viz-model-select">';
  const bandModes: { value: CompareBandMode; label: string }[] = [
    { value: 'overlay', label: t('viz.bandMode.overlay') },
    { value: 'overlay-soft', label: t('viz.bandMode.overlaySoft') },
    { value: 'consensus', label: t('viz.bandMode.consensus') },
    { value: 'consensus-outline', label: t('viz.bandMode.consensusOutline') },
  ];
  for (const mode of bandModes) {
    const selected = bm === mode.value ? ' selected' : '';
    html += `<option value="${mode.value}"${selected}>${mode.label}</option>`;
  }
  html += '</select>';
  html += '</div>';

  // Theme selector
  const currentTheme = getActiveThemeId();
  html += `<div class="viz-theme-selector">`;
  html += `<span class="viz-toggle-label">${t('viz.theme')}</span>`;
  html += `<select id="viz-theme-select" class="viz-model-select">`;
  for (const [id, theme] of Object.entries(THEMES)) {
    const selected = id === currentTheme ? ' selected' : '';
    html += `<option value="${id}"${selected}>${theme.label}</option>`;
  }
  html += `</select>`;
  html += `<button id="viz-theme-preview" class="viz-layer-info-btn" title="${t('viz.previewTheme')}">\u{1f441}</button>`;
  html += `</div>`;

  html += '</div>'; // .viz-toolbar-top

  // Model chips
  if (availableModels.length > 0) {
    html += '<div class="viz-compare-model-chips">';
    html += `<span class="viz-toggle-label">${t('viz.models')}</span>`;
    for (const m of availableModels) {
      const enabled = settings.compareModels[m] !== false;
      const activeClass = enabled ? ' active' : '';
      html += `<button class="btn-chip${activeClass}" data-compare-model="${m}">${modelLabel(m)}</button>`;
    }
    html += '</div>';
  }

  html += '</div>'; // .viz-toolbar

  container.innerHTML = html;

  // Wire layout toggle
  container.querySelectorAll('[data-layout]').forEach((btn) => {
    btn.addEventListener('click', () => {
      callbacks.onLayoutChange((btn as HTMLElement).dataset.layout as VizLayout);
    });
  });

  // Wire layer selector
  const layerSelect = container.querySelector('#compare-layer-select') as HTMLSelectElement | null;
  if (layerSelect) {
    layerSelect.addEventListener('change', () => {
      callbacks.onCompareLayerChange(layerSelect.value);
    });
  }

  // Wire band mode selector
  const bandModeSelect = container.querySelector('#compare-band-mode-select') as HTMLSelectElement | null;
  if (bandModeSelect) {
    bandModeSelect.addEventListener('change', () => {
      callbacks.onCompareBandModeChange(bandModeSelect.value as CompareBandMode);
    });
  }

  // Wire model chips
  container.querySelectorAll('[data-compare-model]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const el = btn as HTMLElement;
      const model = el.dataset.compareModel!;
      const currentlyActive = el.classList.contains('active');
      callbacks.onCompareModelToggle(model, !currentlyActive);
    });
  });

  // Wire theme selector
  const vizThemeSelect = container.querySelector('#viz-theme-select') as HTMLSelectElement | null;
  if (vizThemeSelect && callbacks.onThemeChange) {
    const cb = callbacks.onThemeChange;
    vizThemeSelect.addEventListener('change', () => {
      cb(vizThemeSelect.value);
    });
  }
  const themePreviewBtn = container.querySelector('#viz-theme-preview') as HTMLButtonElement | null;
  if (themePreviewBtn) {
    themePreviewBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const themeId = vizThemeSelect?.value ?? getActiveThemeId();
      showThemePreview(themeId as ThemeId, { cloudStyle: settings.cloudStyle });
    });
  }
}
