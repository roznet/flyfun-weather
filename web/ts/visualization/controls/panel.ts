/** Visualization control panel: layout toggle, model selector, layer checkboxes, map controls. */

import type { VizLayout, VizSettings, CompareBandMode, LayerGroup } from '../types';
import type { DisplayMode } from '../../types/metrics';
import { getLayerGroups, getPreferredLayerForGroup, getPresets, getPreset } from '../cross-section/layer-registry';
import {
  CLOUD_LAYER_BY_AXES,
  ALL_CLOUD_LAYER_IDS,
  parseCloudLayerId,
} from '../cross-section/layers/cloud-bands-factory';
import { getDdSubstituteId } from '../cross-section/nwp-fallback';
import { getComparableLayerGroups, getComparableLayer } from '../cross-section/compare-layers';
import { showLayerInfo, showPopupContent } from '../../components/info-popup';
import { modelLabel } from '../../utils';
import { getMetricOptions } from '../route-graph/metrics';
import { getMapMetricOptions, MAP_METRIC_NONE } from '../route-map/metrics';
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
  html += `<option value="soft"${style === 'soft' ? ' selected' : ''}>${t('viz.cloudStyle.soft')}</option>`;
  html += `<option value="natural"${style === 'natural' ? ' selected' : ''}>${t('viz.cloudStyle.natural')}</option>`;
  html += `<option value="square"${style === 'square' ? ' selected' : ''}>${t('viz.cloudStyle.square')}</option>`;
  html += `</select>`;
  return html;
}

/** Build the `<div class="viz-layer-toggles">...</div>` block for a set of
 *  enabled-layer states. Used inline by the main toolbar and by
 *  {@link renderLayerToggles} for callers that only want this section. */
function layerTogglesHtml(
  enabledLayers: Record<string, boolean>,
  opts: LayerTogglesOptions = {},
): string {
  const { displayMode, preferredMethods, unavailableLayers, cloudStyle, substitutedLayers, hiddenGroups } = opts;
  const groups = getLayerGroups();
  let html = '<div class="viz-layer-toggles">';
  for (const group of groups) {
    if (hiddenGroups?.has(group.group)) continue;
    const isCompactCollapse = displayMode === 'compact' && COMPACT_GROUPS.has(group.group);
    const layersToRender = isCompactCollapse
      ? [getPreferredLayerForGroup(group.group, group.layers, preferredMethods?.[group.group])]
      : group.layers;

    html += `<div class="viz-layer-group">`;
    html += `<span class="viz-group-label">${group.label}:</span>`;
    if (GROUP_INFO[group.group]) {
      html += `<button class="viz-layer-info-btn viz-group-info-btn" data-group-info="${group.group}" title="About ${group.label}" aria-label="About ${group.label}">ⓘ</button>`;
    }
    // Clouds group in non-compact mode: compound source-toggles + style dropdown.
    if (group.group === 'clouds' && !isCompactCollapse) {
      html += cloudCompoundHtml(enabledLayers, unavailableLayers, cloudStyle, substitutedLayers);
      html += '</div>';
      continue;
    }
    for (const layer of layersToRender) {
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
}

export interface MapControlCallbacks {
  onColorMetricChange: (metricId: string) => void;
  onWidthMetricChange: (metricId: string) => void;
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
    // Preset selector
    const presets = getPresets();
    if (presets.length > 0) {
      html += `<div class="viz-theme-selector">`;
      html += `<span class="viz-toggle-label">${t('viz.preset')}</span>`;
      html += `<select id="viz-preset-select" class="viz-model-select">`;
      html += `<option value="">${t('viz.presetCustom')}</option>`;
      for (const preset of presets) {
        html += `<option value="${preset.id}">${preset.label}</option>`;
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
      cloudStyle: settings.cloudStyle,
    });
  }
  html += '</div>'; // .viz-toolbar

  container.innerHTML = html;

  // Wire layout toggle
  container.querySelectorAll('[data-layout]').forEach((btn) => {
    btn.addEventListener('click', () => {
      callbacks.onLayoutChange((btn as HTMLElement).dataset.layout as VizLayout);
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
      showThemePreview(themeId as ThemeId);
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
}

/** Render map-specific controls (color + width metric dropdowns) into the map controls container. */
export function renderMapControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: MapControlCallbacks,
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

  html += '</div>';

  container.innerHTML = html;

  // Wire metric dropdowns
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
      showThemePreview(themeId as ThemeId);
    });
  }
}
