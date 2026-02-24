/** Shared interaction helpers for cross-section and route graph canvases. */

import type { PlotArea, VizRouteData, VizPoint } from './types';

/** Get the CSS-space X coordinate of a mouse event relative to an element. */
export function getCanvasX(e: MouseEvent, el: HTMLElement): number {
  const rect = el.getBoundingClientRect();
  return e.clientX - rect.left;
}

/** Get the CSS-space Y coordinate of a mouse event relative to an element. */
export function getCanvasY(e: MouseEvent, el: HTMLElement): number {
  const rect = el.getBoundingClientRect();
  return e.clientY - rect.top;
}

/** Find the index of the route point nearest to the given distance. */
export function findNearestPointIndex(points: readonly VizPoint[], distanceNm: number): number {
  let bestIdx = 0;
  let bestDelta = Math.abs(points[0].distanceNm - distanceNm);
  for (let i = 1; i < points.length; i++) {
    const delta = Math.abs(points[i].distanceNm - distanceNm);
    if (delta < bestDelta) {
      bestDelta = delta;
      bestIdx = i;
    }
  }
  return bestIdx;
}

/** Choose a nice distance tick interval for the X-axis. */
export function chooseDistanceTickInterval(maxDistance: number): number {
  if (maxDistance <= 50) return 10;
  if (maxDistance <= 150) return 25;
  if (maxDistance <= 300) return 50;
  if (maxDistance <= 600) return 100;
  return 200;
}

/** Lazily create and return a tooltip element parented to the given container. */
export function ensureTooltip(
  container: HTMLElement,
  existing: HTMLElement | null,
): HTMLElement {
  if (existing) return existing;
  const tip = document.createElement('div');
  tip.className = 'viz-tooltip';
  container.appendChild(tip);
  return tip;
}

/** Position a tooltip relative to a mouse event within a container, flipping if near the right edge. */
export function positionTooltip(
  tip: HTMLElement,
  e: MouseEvent,
  canvas: HTMLElement,
  containerWidth: number,
): void {
  const rect = canvas.getBoundingClientRect();
  const tipX = e.clientX - rect.left + 12;
  const tipY = e.clientY - rect.top - 10;

  const tipWidth = tip.offsetWidth || 160;
  if (tipX + tipWidth > containerWidth) {
    tip.style.left = '';
    tip.style.right = `${containerWidth - tipX + 24}px`;
  } else {
    tip.style.left = `${tipX}px`;
    tip.style.right = '';
  }
  tip.style.top = `${Math.max(0, tipY)}px`;
}

/** Hide a tooltip element. */
export function hideTooltip(tip: HTMLElement | null): void {
  if (tip) tip.style.display = 'none';
}

/** Find the waypoint marker nearest to a point (within 1 nm), if any. */
export function findNearbyWaypoint(
  data: VizRouteData,
  point: VizPoint,
): { distanceNm: number; icao: string } | undefined {
  return data.waypointMarkers.find((w) => Math.abs(w.distanceNm - point.distanceNm) < 1);
}
