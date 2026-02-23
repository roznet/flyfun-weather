/** Route graph interaction: hover crosshair synced with cross-section, click-to-select, tooltip. */

import type { VizRouteData, VizPoint } from '../types';
import type { RouteGraphRenderer } from './renderer';
import type { RouteGraphMetric } from './metrics';

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

  function ensureTooltip(): HTMLElement {
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'viz-tooltip';
      canvas.parentElement!.appendChild(tooltip);
    }
    return tooltip;
  }

  function getCanvasX(e: MouseEvent): number {
    const rect = canvas.getBoundingClientRect();
    return e.clientX - rect.left;
  }

  function findNearestPointIndex(distanceNm: number): number {
    let bestIdx = 0;
    let bestDelta = Math.abs(data.points[0].distanceNm - distanceNm);
    for (let i = 1; i < data.points.length; i++) {
      const delta = Math.abs(data.points[i].distanceNm - distanceNm);
      if (delta < bestDelta) {
        bestDelta = delta;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  function xToDistance(x: number): number {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return 0;
    return ((x - plotArea.left) / plotArea.width) * data.totalDistanceNm;
  }

  function handleMouseMove(e: MouseEvent): void {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return;

    const x = getCanvasX(e);

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      renderer.renderOverlay();
      hideTooltip();
      callbacks.onHover(undefined);
      return;
    }

    renderer.renderOverlay(x);
    callbacks.onHover(x);

    // Show tooltip
    const distanceNm = xToDistance(x);
    const idx = findNearestPointIndex(distanceNm);
    const point = data.points[idx];
    showTooltip(e, point, idx);
  }

  function handleClick(e: MouseEvent): void {
    const plotArea = renderer.getPlotArea();
    if (!plotArea) return;

    const x = getCanvasX(e);
    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = xToDistance(x);
    const idx = findNearestPointIndex(distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handleMouseLeave(): void {
    renderer.renderOverlay();
    hideTooltip();
    callbacks.onHover(undefined);
  }

  function showTooltip(e: MouseEvent, point: VizPoint, idx: number): void {
    const tip = ensureTooltip();
    const lines: string[] = [];

    // Waypoint or point index
    const wp = data.waypointMarkers.find((w) => Math.abs(w.distanceNm - point.distanceNm) < 1);
    lines.push(wp ? `<strong>${wp.icao}</strong>` : `<strong>Point ${idx}</strong>`);
    lines.push(`${point.distanceNm.toFixed(0)} nm`);

    // Left metric value
    if (leftMetric) {
      const v = leftMetric.getValue(point);
      if (v !== null) {
        const fmt = leftMetric.formatValue ? leftMetric.formatValue(v) : v.toFixed(1);
        lines.push(`<span style="color:${leftMetric.color}">${leftMetric.label}: ${fmt}</span>`);
      } else {
        lines.push(`<span style="color:${leftMetric.color}">${leftMetric.label}: N/A</span>`);
      }
    }

    // Right metric value
    if (rightMetric) {
      const v = rightMetric.getValue(point);
      if (v !== null) {
        const fmt = rightMetric.formatValue ? rightMetric.formatValue(v) : v.toFixed(1);
        lines.push(`<span style="color:${rightMetric.color}">${rightMetric.label}: ${fmt}</span>`);
      } else {
        lines.push(`<span style="color:${rightMetric.color}">${rightMetric.label}: N/A</span>`);
      }
    }

    tip.innerHTML = lines.join('<br>');
    tip.style.display = 'block';

    const rect = canvas.getBoundingClientRect();
    const tipX = e.clientX - rect.left + 12;
    const tipY = e.clientY - rect.top - 10;

    const containerW = canvas.parentElement!.clientWidth;
    const tipWidth = tip.offsetWidth || 160;
    if (tipX + tipWidth > containerW) {
      tip.style.left = '';
      tip.style.right = `${containerW - tipX + 24}px`;
    } else {
      tip.style.left = `${tipX}px`;
      tip.style.right = '';
    }
    tip.style.top = `${Math.max(0, tipY)}px`;
  }

  function hideTooltip(): void {
    if (tooltip) tooltip.style.display = 'none';
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
