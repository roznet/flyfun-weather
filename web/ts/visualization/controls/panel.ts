/** Visualization control panel: layout toggle, render mode, layer checkboxes. */

import type { RenderMode, VizSettings } from '../types';
import { getLayerGroups } from '../cross-section/layer-registry';

export interface VizControlCallbacks {
  onRenderModeChange: (mode: RenderMode) => void;
  onLayerToggle: (layerId: string) => void;
}

export function renderVizControls(
  container: HTMLElement,
  settings: VizSettings,
  callbacks: VizControlCallbacks,
  selectedModel?: string,
): void {
  const groups = getLayerGroups();

  let html = '<div class="viz-toolbar">';

  // Model indicator
  if (selectedModel) {
    html += `<div class="viz-model-indicator">`;
    html += `<span class="viz-toggle-label">Model:</span>`;
    html += `<span class="viz-model-name">${selectedModel.toUpperCase()}</span>`;
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

  // Layer toggles
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
    }
    html += '</div>';
  }
  html += '</div>';

  html += '</div>';

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
}
