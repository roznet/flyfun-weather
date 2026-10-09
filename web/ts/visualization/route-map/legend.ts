/** Map legend — one grid row per styling channel (Color, Width): the metric
 *  picker, then a scale strip with each stop label centred under its swatch.
 *
 *  The picker IS the row's title (#731): naming the metric in text as well
 *  as in a dropdown below it said everything twice. And the Width row draws
 *  thickness, not colour — width only changes the route line's weight, so a
 *  colour ramp there matched nothing on the map. */

import type { MapMetric } from './metrics';
import { getMapMetricOptions } from './metrics';
import { t } from '../../i18n/i18n';

export interface MapLegendCallbacks {
  onColorMetricChange: (metricId: string) => void;
  onWidthMetricChange: (metricId: string) => void;
}

export interface MapLegendSelection {
  /** Selected ids, as stored in `vizSettings` (width may be `MAP_METRIC_NONE`). */
  colorMetricId: string;
  widthMetricId: string;
  /** Resolved metrics; null when the id is none/unknown. */
  colorMetric: MapMetric | null;
  widthMetric: MapMetric | null;
}

/** Route-line weights run 3–25 px; scale so the thickest bar fits the row. */
const WIDTH_BAR_SCALE = 0.6;
const WIDTH_BAR_MIN_PX = 2;

/** Bar height (px) for a route-line weight, as drawn in the width strip. */
export function widthBarPx(weight: number): number {
  return Math.max(WIDTH_BAR_MIN_PX, Math.round(weight * WIDTH_BAR_SCALE * 10) / 10);
}

/** Pure HTML for the legend grid — no DOM, so it is unit-testable. */
export function mapLegendHtml(sel: MapLegendSelection): string {
  let html = '<div class="map-legend-grid">';
  html += legendRow('color', t('viz.color'), getMapMetricOptions(false), sel.colorMetricId,
    sel.colorMetric ? colorScaleHtml(sel.colorMetric) : '');
  html += legendRow('width', t('viz.width'), getMapMetricOptions(true), sel.widthMetricId,
    sel.widthMetric ? widthScaleHtml(sel.widthMetric) : '');
  html += '</div>';
  return html;
}

export function renderMapLegend(
  container: HTMLElement,
  sel: MapLegendSelection,
  callbacks: MapLegendCallbacks,
): void {
  container.innerHTML = mapLegendHtml(sel);

  const colorSelect = container.querySelector('#map-color-metric') as HTMLSelectElement | null;
  colorSelect?.addEventListener('change', () => callbacks.onColorMetricChange(colorSelect.value));
  const widthSelect = container.querySelector('#map-width-metric') as HTMLSelectElement | null;
  widthSelect?.addEventListener('change', () => callbacks.onWidthMetricChange(widthSelect.value));
}

function legendRow(
  channel: 'color' | 'width',
  label: string,
  options: Array<{ id: string; label: string }>,
  selectedId: string,
  scaleHtml: string,
): string {
  let html = `<label class="viz-toggle-label map-legend-channel" for="map-${channel}-metric">${label}</label>`;
  html += `<select id="map-${channel}-metric" class="map-control-select">`;
  for (const opt of options) {
    const selected = opt.id === selectedId ? ' selected' : '';
    html += `<option value="${opt.id}"${selected}>${opt.label}</option>`;
  }
  html += '</select>';
  // Always emit the cell so the grid keeps its shape when a row has no scale.
  html += `<div class="map-legend-scale map-legend-scale-${channel}">${scaleHtml}</div>`;
  return html;
}

/** Swatches and labels share one column template, so stop i's label sits
 *  under stop i's swatch whatever the label lengths. */
function scaleHtml(metric: MapMetric, swatch: (stop: MapMetric['legendStops'][number]) => string): string {
  const stops = metric.legendStops;
  if (stops.length === 0) return '';
  const cols = `grid-template-columns:repeat(${stops.length},1fr)`;
  let html = `<div class="map-legend-swatches" style="${cols}">`;
  for (const stop of stops) html += swatch(stop);
  html += '</div>';
  html += `<div class="map-legend-labels" style="${cols}">`;
  for (const stop of stops) html += `<span>${stop.label}</span>`;
  html += '</div>';
  return html;
}

function colorScaleHtml(metric: MapMetric): string {
  return scaleHtml(metric, (stop) =>
    `<div class="map-legend-stop" style="background:${stop.color}" title="${stop.label}"></div>`);
}

function widthScaleHtml(metric: MapMetric): string {
  return scaleHtml(metric, (stop) => {
    const h = widthBarPx(metric.getWidth(stop.value));
    return `<div class="map-legend-width-cell" title="${stop.label}"><div class="map-legend-width-bar" style="height:${h}px"></div></div>`;
  });
}
