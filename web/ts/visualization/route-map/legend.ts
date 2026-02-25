/** Map legend — DOM-based color gradient bar from metric legend stops. */

import type { MapMetric } from './metrics';

export function renderMapLegend(
  container: HTMLElement,
  colorMetric: MapMetric | null,
  widthMetric: MapMetric | null,
): void {
  if (!colorMetric && !widthMetric) {
    container.innerHTML = '';
    return;
  }

  let html = '';

  if (colorMetric && colorMetric.legendStops.length > 0) {
    html += renderLegendBar('Color', colorMetric);
  }

  if (widthMetric && widthMetric.id !== colorMetric?.id && widthMetric.legendStops.length > 0) {
    html += renderLegendBar('Width', widthMetric);
  }

  container.innerHTML = html;
}

function renderLegendBar(prefix: string, metric: MapMetric): string {
  const stops = metric.legendStops;

  let html = `<div class="map-legend-bar">`;
  html += `<span class="viz-toggle-label">${prefix}: ${metric.label}</span>`;

  // Gradient bar
  html += `<div class="map-legend-gradient">`;
  for (const stop of stops) {
    html += `<div class="map-legend-stop" style="background:${stop.color}" title="${stop.label}"></div>`;
  }
  html += `</div>`;

  html += `</div>`;

  // Labels below gradient
  html += `<div class="map-legend-labels">`;
  for (const stop of stops) {
    html += `<span>${stop.label}</span>`;
  }
  html += `</div>`;

  return html;
}
