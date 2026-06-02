/** Route graph interaction: hover crosshair synced with cross-section, click-to-select, tooltip. */

import type { VizRouteData, VizPoint } from '../types';
import type { RouteGraphRenderer } from './renderer';
import { getMetricLabel, type RouteGraphMetric } from './metrics';
import {
  getCanvasX, findNearestPointIndex, ensureTooltip as ensureTooltipEl,
  positionTooltip, hideTooltip as hideTooltipEl, findNearbyWaypoint,
} from '../interaction-utils';

export interface RouteGraphInteractionCallbacks {
  onSelectPoint: (index: number) => void;
  /** Called on hover to sync crosshair with cross-section. */
  onHover: (x: number | undefined) => void;
}

/** Returned by attachRouteGraphInteraction — allows updating data without re-attaching listeners. */
export interface RouteGraphInteractionHandle {
  /** Update the closed-over data and metrics without tearing down listeners. */
  update(data: VizRouteData, leftMetric: RouteGraphMetric | null, rightMetric: RouteGraphMetric | null): void;
  /** Remove all listeners and clean up tooltip. */
  destroy(): void;
}

export function attachRouteGraphInteraction(
  renderer: RouteGraphRenderer,
  data: VizRouteData,
  leftMetric: RouteGraphMetric | null,
  rightMetric: RouteGraphMetric | null,
  callbacks: RouteGraphInteractionCallbacks,
): RouteGraphInteractionHandle {
  const canvas = renderer.getCanvas();
  canvas.style.pointerEvents = 'auto';
  canvas.style.cursor = 'crosshair';
  // Suppress browser scroll/zoom gestures so pointermove fires during touch drag.
  canvas.style.touchAction = 'none';

  // Mutable state that can be swapped via update()
  let currentData = data;
  let currentLeft = leftMetric;
  let currentRight = rightMetric;
  let tooltip: HTMLElement | null = null;

  function xToDistance(x: number): number {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return 0;
    return ((x - plotArea.left) / plotArea.width) * currentData.totalDistanceNm;
  }

  /** Render crosshair + tooltip at the event position (shared by move/down). */
  function renderAt(e: PointerEvent): void {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return;

    const x = getCanvasX(e, canvas);

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      clearOverlay();
      return;
    }

    renderer.renderOverlay(x);
    callbacks.onHover(x);

    const distanceNm = xToDistance(x);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    const point = currentData.points[idx];
    showTooltip(e, point, idx);
  }

  function clearOverlay(): void {
    renderer.renderOverlay();
    hideTooltipEl(tooltip);
    callbacks.onHover(undefined);
  }

  function handlePointerDown(e: PointerEvent): void {
    if (e.button !== 0) return; // ignore right/middle on desktop; touch reports 0
    renderAt(e);
  }

  function handlePointerUp(e: PointerEvent): void {
    if (e.button !== 0) return; // left-click / tap only, matching the old click handler
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return;

    const x = getCanvasX(e, canvas);
    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = xToDistance(x);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handlePointerLeave(e: PointerEvent): void {
    // On touch, leave the crosshair/tooltip pinned where the user tapped.
    if (e.pointerType === 'mouse') clearOverlay();
  }

  // OS-cancelled touch (notification shade, home-swipe) fires pointercancel
  // instead of pointerleave — clear here too so the pin doesn't get stuck.
  function handlePointerCancel(): void {
    clearOverlay();
  }

  function formatMetricLine(metric: RouteGraphMetric, point: VizPoint): string {
    const v = metric.getValue(point);
    const fmt = v !== null
      ? (metric.formatValue ? metric.formatValue(v) : v.toFixed(1))
      : 'N/A';
    return `<span style="color:${metric.color}">${getMetricLabel(metric.id)}: ${fmt}</span>`;
  }

  function showTooltip(e: PointerEvent, point: VizPoint, idx: number): void {
    tooltip = ensureTooltipEl(canvas.parentElement!, tooltip);
    const lines: string[] = [];

    const wp = findNearbyWaypoint(currentData, point);
    lines.push(wp ? `<strong>${wp.icao}</strong>` : `<strong>Point ${idx}</strong>`);
    lines.push(`${point.distanceNm.toFixed(0)} nm`);

    if (currentLeft) lines.push(formatMetricLine(currentLeft, point));
    if (currentRight) lines.push(formatMetricLine(currentRight, point));

    tooltip.innerHTML = lines.join('<br>');
    tooltip.style.display = 'block';
    positionTooltip(tooltip, e, canvas, canvas.parentElement!.clientWidth);
  }

  canvas.addEventListener('pointermove', renderAt);
  canvas.addEventListener('pointerdown', handlePointerDown);
  canvas.addEventListener('pointerup', handlePointerUp);
  canvas.addEventListener('pointerleave', handlePointerLeave);
  canvas.addEventListener('pointercancel', handlePointerCancel);

  return {
    update(newData, newLeft, newRight) {
      currentData = newData;
      currentLeft = newLeft;
      currentRight = newRight;
    },
    destroy() {
      canvas.removeEventListener('pointermove', renderAt);
      canvas.removeEventListener('pointerdown', handlePointerDown);
      canvas.removeEventListener('pointerup', handlePointerUp);
      canvas.removeEventListener('pointerleave', handlePointerLeave);
      canvas.removeEventListener('pointercancel', handlePointerCancel);
      if (tooltip) { tooltip.remove(); tooltip = null; }
      canvas.style.pointerEvents = '';
      canvas.style.cursor = '';
      canvas.style.touchAction = '';
    },
  };
}
