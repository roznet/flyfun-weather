/** Visualization control panel: layout toggle, render mode, layer checkboxes, route graph controls. */

import type { RenderMode, VizSettings } from '../types';
import { getLayerGroups } from '../cross-section/layer-registry';
import { showLayerInfo } from '../../components/info-popup';
import { modelLabel } from '../../utils';
import { getMetricOptions } from '../route-graph/metrics';

export interface VizControlCallbacks {
  onRenderModeChange: (mode: RenderMode) => void;
  onLayerToggle: (layerId: string) => void;
  onRouteGraphToggle: (visible: boolean) => void;
  onRouteGraphMetricChange: (axis: 'left' | 'right', metricId: string) => void;
}

export function renderVizControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: VizControlCallbacks,
  selectedModel?: string,
): void {
  const groups = getLayerGroups();

  let html = '<div class="viz-toolbar">';

  // Top row: Model indicator + Render mode toggle
  html += '<div class="viz-toolbar-top">';

  // Model indicator
  if (selectedModel) {
    html += `<div class="viz-model-indicator">`;
    html += `<span class="viz-toggle-label">Model:</span>`;
    html += `<span class="viz-model-name">${modelLabel(selectedModel)}</span>`;
    html += `</div>`;
  }

  // Render mode toggle
  html += '<div class="viz-render-toggle">';
  html += '<span class="viz-toggle-label">Render:</span>';
  html += `<div class="display-mode-toggle">`;
  html += `<button class="btn-toggle${settings.renderMode === 'smooth' ? ' active' : ''}" data-render-mode="smooth">Smooth</button>`;
  html += `<button class="btn-toggle${settings.renderMode === 'columns' ? ' active' : ''}" data-render-mode="columns">Columns</button>`;
  html += '</div>';
  html += '</div>';

  html += '</div>'; // .viz-toolbar-top

  // Layer toggles — full width below
  html += '<div class="viz-layer-toggles">';
  for (const group of groups) {
    html += `<div class="viz-layer-group">`;
    html += `<span class="viz-group-label">${group.label}:</span>`;
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

  // Route graph controls
  html += renderRouteGraphControls(settings);

  html += '</div>'; // .viz-toolbar

  container.innerHTML = html;

  // Wire render mode toggle
  container.querySelectorAll('[data-render-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      callbacks.onRenderModeChange((btn as HTMLElement).dataset.renderMode as RenderMode);
    });
  });

  // Wire layer toggles
  container.querySelectorAll('[data-layer-id]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      callbacks.onLayerToggle((checkbox as HTMLInputElement).dataset.layerId!);
    });
  });

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

  // Wire route graph toggle
  const graphToggle = container.querySelector('#route-graph-toggle') as HTMLButtonElement | null;
  if (graphToggle) {
    graphToggle.addEventListener('click', () => {
      callbacks.onRouteGraphToggle(!settings.routeGraphVisible);
    });
  }

  // Wire route graph metric dropdowns
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

function renderRouteGraphControls(settings: VizSettings): string {
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
  return html;
}
