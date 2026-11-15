/** Route-map legend (#998): one legend area in the map's bottom panel.
 *
 *  Line 1 is the route metric's scale from the served catalog, each label
 *  centred under its swatch. Line 2, only while the airport overlay is on, is a
 *  compact strip of the airport metric's legend; it replaces the floating
 *  top-right box the overlay used to draw, so the map has one legend. No width
 *  row: line width follows the colour metric. */

import type { MapMetric } from './metrics';
import type { MapLegend } from '../weather-map-format';
import { escapeHtml } from '../../utils';

/** Pure HTML for the route metric's scale. */
export function routeLegendHtml(metric: MapMetric | null): string {
  if (!metric) return '';
  const stops = metric.legendStops;
  if (!stops.length) return '';
  const cols = `grid-template-columns:repeat(${stops.length},1fr)`;
  let html = '<div class="rm-legend-route">';
  html += `<div class="map-legend-swatches" style="${cols}">`;
  for (const stop of stops) {
    html += `<div class="map-legend-stop" style="background:${stop.color}" title="${escapeHtml(stop.label)}"></div>`;
  }
  html += '</div>';
  html += `<div class="map-legend-labels" style="${cols}">`;
  for (const stop of stops) html += `<span>${escapeHtml(stop.label)}</span>`;
  html += '</div>';
  html += metric.unit ? `<span class="rm-legend-unit">${escapeHtml(metric.unit)}</span>` : '';
  html += '</div>';
  return html;
}

/** Pure HTML for the airport overlay's compact legend line. */
export function airportLegendHtml(legend: MapLegend | null): string {
  if (!legend || !legend.items.length) return '';
  let html = `<div class="rm-legend-airports"><span class="rm-legend-airports-title">${escapeHtml(legend.title)}</span>`;
  for (const item of legend.items) {
    html += `<span class="rm-legend-airport-item"><span class="rm-legend-dot" style="background:${item.color}"></span>${escapeHtml(item.label)}</span>`;
  }
  html += '</div>';
  return html;
}
