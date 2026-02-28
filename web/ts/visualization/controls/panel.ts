/** Visualization control panel: layout toggle, model selector, layer checkboxes, map controls. */

import type { VizLayout, VizSettings } from '../types';
import { getLayerGroups } from '../cross-section/layer-registry';
import { showLayerInfo, showPopupContent } from '../../components/info-popup';
import { modelLabel } from '../../utils';
import { getMetricOptions } from '../route-graph/metrics';
import { getMapMetricOptions, MAP_METRIC_NONE } from '../route-map/metrics';

/** Explanatory text for layer group info buttons. */
const GROUP_INFO: Record<string, string> = {
  clouds: `<h3>Cloud Detection Methods</h3>
<p>Clouds can be detected two ways on the cross-section:</p>
<ul>
  <li><strong>NWP Layers</strong> — derived from the numerical weather prediction model's cloud cover parameterisation (low / mid / high bands). Shows where the model itself predicts clouds.</li>
  <li><strong>DD Layers</strong> — derived from dewpoint depression in the sounding profile. When the gap between temperature and dewpoint is small (DD &lt; 2°C), air is near saturation and cloud is likely. This is a sounding-based signal independent of the model's cloud scheme.</li>
</ul>
<p>Both methods can be enabled simultaneously for comparison. They often agree in moist layers but may differ where NWP sub-grid clouds or local moisture are present.</p>`,

  icing: `<h3>Icing Methods</h3>
<p>The cross-section offers three icing overlays, each combining an <strong>icing formula</strong> with a <strong>cloud detection method</strong>:</p>
<h4>Formulas</h4>
<ul>
  <li><strong>Ogimet</strong> — continuous parabolic index peaking at −7°C. Maps temperature to an icing severity index across the 0 to −14°C range (layered) or 0 to −20°C (convective). Simple, well-proven in European GA.</li>
  <li><strong>SFIP</strong> — fuzzy-logic algorithm combining temperature, relative humidity, cloud liquid water, and vertical velocity via membership functions. More complex, uses direct NWP moisture data when available.</li>
</ul>
<h4>Cloud Signals</h4>
<ul>
  <li><strong>DD</strong> (dewpoint depression) — sounding-based. Uses a continuous cosine taper: full icing index at DD = 0, zero at DD ≥ 2°C. No NWP cloud data needed.</li>
  <li><strong>NWP</strong> (model cloud cover) — uses the NWP model's cloud cover percentage at each altitude. 50% cloud → 50% of icing index. This is the approach used by Autorouter GRAMET.</li>
</ul>
<h4>The Three Methods</h4>
<ul>
  <li><strong>Ogimet-DD</strong> — Ogimet formula + DD cloud signal</li>
  <li><strong>Ogimet-NWP</strong> — Ogimet formula + NWP cloud signal</li>
  <li><strong>SFIP-NWP</strong> — SFIP fuzzy-logic (inherently uses NWP moisture inputs)</li>
</ul>
<p>Enable multiple methods to compare them side-by-side. The advisory system uses whichever method you select in your profile settings.</p>`,
};

export interface VizControlCallbacks {
  onLayerToggle: (layerId: string) => void;
  onLayoutChange: (layout: VizLayout) => void;
  onModelChange?: (model: string) => void;
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
): void {
  const groups = getLayerGroups();

  let html = '<div class="viz-toolbar">';

  // Top row: Layout toggle + Model indicator + Render mode toggle
  html += '<div class="viz-toolbar-top">';

  // Layout toggle
  html += '<div class="viz-layout-toggle">';
  html += `<button class="btn-toggle${settings.layout === 'cross-section' ? ' active' : ''}" data-layout="cross-section" title="Cross-section only">X-Section</button>`;
  html += `<button class="btn-toggle${settings.layout === 'split' ? ' active' : ''}" data-layout="split" title="Side-by-side">Split</button>`;
  html += `<button class="btn-toggle${settings.layout === 'map' ? ' active' : ''}" data-layout="map" title="Map only">Map</button>`;
  html += '</div>';

  // Model selector
  if (selectedModel && availableModels && availableModels.length > 0) {
    html += `<div class="viz-model-selector">`;
    html += `<span class="viz-toggle-label">Model:</span>`;
    html += `<select id="viz-model-select" class="viz-model-select">`;
    for (const m of availableModels) {
      const selected = m === selectedModel ? ' selected' : '';
      html += `<option value="${m}"${selected}>${modelLabel(m)}</option>`;
    }
    html += `</select>`;
    html += `</div>`;
  } else if (selectedModel) {
    html += `<div class="viz-model-selector">`;
    html += `<span class="viz-toggle-label">Model:</span>`;
    html += `<span class="viz-model-name">${modelLabel(selectedModel)}</span>`;
    html += `</div>`;
  }

  // Windy link placeholder (updated dynamically by updateWindyLink)
  html += `<span id="external-links" class="external-links" style="display: none;">`;
  html += `Open selected location in <a id="windy-link" href="#" target="_blank" rel="noopener">Windy</a>`;
  html += `</span>`;

  html += '</div>'; // .viz-toolbar-top

  // Layer toggles — only when cross-section visible
  if (settings.layout !== 'map') {
    html += '<div class="viz-layer-toggles">';
    for (const group of groups) {
      html += `<div class="viz-layer-group">`;
      html += `<span class="viz-group-label">${group.label}:</span>`;
      if (GROUP_INFO[group.group]) {
        html += `<button class="viz-layer-info-btn viz-group-info-btn" data-group-info="${group.group}" title="About ${group.label}" aria-label="About ${group.label}">\u24d8</button>`;
      }
      for (const layer of group.layers) {
        const checked = settings.enabledLayers[layer.id] !== false ? 'checked' : '';
        html += `<label class="viz-layer-checkbox">`;
        html += `<input type="checkbox" data-layer-id="${layer.id}" ${checked}>`;
        html += `<span>${layer.name}</span>`;
        html += `</label>`;
        if (layer.metricId) {
          html += `<button class="viz-layer-info-btn" data-layer-info="${layer.id}" data-metric-id="${layer.metricId}" title="More info" aria-label="More info">\u24d8</button>`;
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
      const html = GROUP_INFO[groupKey];
      if (html) showPopupContent(html);
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
  html += `<span class="route-graph-arrow">${arrow}</span> Route Graph`;
  html += `</button>`;

  // Metric selectors (only shown when visible)
  if (settings.routeGraphVisible) {
    html += '<div class="route-graph-selectors">';

    html += '<label class="route-graph-select-label">';
    html += '<span class="viz-toggle-label">Left:</span>';
    html += '<select id="route-graph-left-metric" class="route-graph-select">';
    for (const opt of leftOptions) {
      const selected = opt.id === settings.routeGraphLeftMetric ? ' selected' : '';
      html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
    }
    html += '</select>';
    html += '</label>';

    html += '<label class="route-graph-select-label">';
    html += '<span class="viz-toggle-label">Right:</span>';
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
  html += '<span class="viz-toggle-label">Color:</span>';
  html += '<select id="map-color-metric" class="map-control-select">';
  for (const opt of colorOptions) {
    const selected = opt.id === settings.mapColorMetric ? ' selected' : '';
    html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
  }
  html += '</select>';
  html += '</label>';

  html += '<label class="map-control-label">';
  html += '<span class="viz-toggle-label">Width:</span>';
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
