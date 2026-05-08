/** Visualization control panel: layout toggle, model selector, layer checkboxes, map controls. */

import type { VizLayout, VizSettings, CompareBandMode } from '../types';
import type { DisplayMode } from '../../types/metrics';
import { getLayerGroups, getPreferredLayerForGroup, getPresets, getPreset } from '../cross-section/layer-registry';
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

export interface VizControlCallbacks {
  onLayerToggle: (layerId: string) => void;
  onLayoutChange: (layout: VizLayout) => void;
  onModelChange?: (model: string) => void;
  onThemeChange?: (themeId: string) => void;
  onPresetChange?: (presetId: string | null) => void;
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
): void {
  const groups = getLayerGroups();

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

  // Layer toggles — only when cross-section visible
  if (settings.layout !== 'map') {
    html += '<div class="viz-layer-toggles">';
    for (const group of groups) {
      const isCompactCollapse = displayMode === 'compact' && COMPACT_GROUPS.has(group.group);
      const layersToRender = isCompactCollapse
        ? [getPreferredLayerForGroup(group.group, group.layers, preferredMethods?.[group.group])]
        : group.layers;

      html += `<div class="viz-layer-group">`;
      html += `<span class="viz-group-label">${group.label}:</span>`;
      if (GROUP_INFO[group.group]) {
        html += `<button class="viz-layer-info-btn viz-group-info-btn" data-group-info="${group.group}" title="About ${group.label}" aria-label="About ${group.label}">\u24d8</button>`;
      }
      for (const layer of layersToRender) {
        const isUnavailable = unavailableLayers?.has(layer.id) ?? false;
        const checked = !isUnavailable && settings.enabledLayers[layer.id] !== false ? 'checked' : '';
        const disabled = isUnavailable ? 'disabled' : '';
        const dimClass = isUnavailable ? ' viz-layer-unavailable' : '';
        const tooltip = isUnavailable ? ` title="${t('viz.notAvailableModel')}"` : '';
        html += `<label class="viz-layer-checkbox${dimClass}"${tooltip}>`;
        html += `<input type="checkbox" data-layer-id="${layer.id}" ${checked} ${disabled}>`;
        html += `<span>${isCompactCollapse ? group.label : t('viz.layer.' + layer.id)}</span>`;
        html += `</label>`;
        if (layer.metricId) {
          html += `<button class="viz-layer-info-btn" data-layer-info="${layer.id}" data-metric-id="${layer.metricId}" title="${t('viz.moreInfo')}" aria-label="${t('viz.moreInfo')}">\u24d8</button>`;
        }
      }
      html += '</div>';
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

  // Wire layer toggles
  container.querySelectorAll('[data-layer-id]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      callbacks.onLayerToggle((checkbox as HTMLInputElement).dataset.layerId!);
    });
  });

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

  // Wire info buttons
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

  // Wire group info buttons
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
