/** Route graph interaction: hover crosshair synced with cross-section, click-to-select, tooltip. */

import type { VizRouteData, VizPoint } from '../types';
import type { RouteGraphRenderer } from './renderer';
import type { RouteGraphMetric } from './metrics';
import {
  getCanvasX, findNearestPointIndex, ensureTooltip as ensureTooltipEl,
  positionTooltip, hideTooltip as hideTooltipEl, findNearbyWaypoint,
} from '../interaction-utils';

export interface RouteGraphInteractionCallbacks {
  onSelectPoint: (index: number) => void;
  /** Called on hover to sync crosshair with cross-section. */
  onHover: (x: number | undefined) => void;
}

export function attachRouteGraphInteraction(
  renderer: RouteGraphRenderer,
  data: VizRouteData,
  leftMetric: RouteGraphMetric | null,
  rightMetric: RouteGraphMetric | null,
  callbacks: RouteGraphInteractionCallbacks,
): () => void {
  const canvas = renderer.getCanvas();
  canvas.style.pointerEvents = 'auto';
  canvas.style.cursor = 'crosshair';

  let tooltip: HTMLElement | null = null;

  function xToDistance(x: number): number {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return 0;
    return ((x - plotArea.left) / plotArea.width) * data.totalDistanceNm;
  }

  function handleMouseMove(e: MouseEvent): void {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return;

    const x = getCanvasX(e, canvas);

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      renderer.renderOverlay();
      hideTooltipEl(tooltip);
      callbacks.onHover(undefined);
      return;
    }

    renderer.renderOverlay(x);
    callbacks.onHover(x);

    // Show tooltip
    const distanceNm = xToDistance(x);
    const idx = findNearestPointIndex(data.points, distanceNm);
    const point = data.points[idx];
    showTooltip(e, point, idx);
  }

  function handleClick(e: MouseEvent): void {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return;

    const x = getCanvasX(e, canvas);
    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = xToDistance(x);
    const idx = findNearestPointIndex(data.points, distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handleMouseLeave(): void {
    renderer.renderOverlay();
    hideTooltipEl(tooltip);
    callbacks.onHover(undefined);
  }

  function formatMetricLine(metric: RouteGraphMetric, point: VizPoint): string {
    const v = metric.getValue(point);
    const fmt = v !== null
      ? (metric.formatValue ? metric.formatValue(v) : v.toFixed(1))
      : 'N/A';
    return `<span style="color:${metric.color}">${metric.label}: ${fmt}</span>`;
  }

  function showTooltip(e: MouseEvent, point: VizPoint, idx: number): void {
    tooltip = ensureTooltipEl(canvas.parentElement!, tooltip);
    const lines: string[] = [];

    const wp = findNearbyWaypoint(data, point);
    lines.push(wp ? `<strong>${wp.icao}</strong>` : `<strong>Point ${idx}</strong>`);
    lines.push(`${point.distanceNm.toFixed(0)} nm`);

    if (leftMetric) lines.push(formatMetricLine(leftMetric, point));
    if (rightMetric) lines.push(formatMetricLine(rightMetric, point));

    tooltip.innerHTML = lines.join('<br>');
    tooltip.style.display = 'block';
    positionTooltip(tooltip, e, canvas, canvas.parentElement!.clientWidth);
  }

  canvas.addEventListener('mousemove', handleMouseMove);
  canvas.addEventListener('click', handleClick);
  canvas.addEventListener('mouseleave', handleMouseLeave);

  return () => {
    canvas.removeEventListener('mousemove', handleMouseMove);
    canvas.removeEventListener('click', handleClick);
    canvas.removeEventListener('mouseleave', handleMouseLeave);
    if (tooltip) { tooltip.remove(); tooltip = null; }
    canvas.style.pointerEvents = '';
    canvas.style.cursor = '';
  };
}
