/** Cross-section interaction: hover crosshair, click-to-select, tooltip. */

import type { VizRouteData, VizPoint } from '../types';
import type { CrossSectionRenderer } from './renderer';

export interface InteractionCallbacks {
  onSelectPoint: (index: number) => void;
}

export function attachInteraction(
  renderer: CrossSectionRenderer,
  data: VizRouteData,
  callbacks: InteractionCallbacks,
): () => void {
  const canvas = renderer.getCanvas();
  // Make the overlay canvas receive pointer events
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

  function handleMouseMove(e: MouseEvent): void {
    const transform = renderer.createTransform();
    if (!transform) return;

    const x = getCanvasX(e);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      renderer.renderOverlay();
      hideTooltip();
      return;
    }

    renderer.renderOverlay(x);

    // Show tooltip
    const distanceNm = transform.xToDistance(x);
    const idx = findNearestPointIndex(distanceNm);
    const point = data.points[idx];

    showTooltip(e, point, idx, data);
  }

  function handleClick(e: MouseEvent): void {
    const transform = renderer.createTransform();
    if (!transform) return;

    const x = getCanvasX(e);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = transform.xToDistance(x);
    const idx = findNearestPointIndex(distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handleMouseLeave(): void {
    renderer.renderOverlay();
    hideTooltip();
  }

  function showTooltip(e: MouseEvent, point: VizPoint, idx: number, routeData: VizRouteData): void {
    const tip = ensureTooltip();
    const lines: string[] = [];

    // Waypoint or point index
    const wp = routeData.waypointMarkers.find((w) => Math.abs(w.distanceNm - point.distanceNm) < 1);
    lines.push(wp ? `<strong>${wp.icao}</strong>` : `<strong>Point ${idx}</strong>`);

    // Distance and time
    lines.push(`${point.distanceNm.toFixed(0)} nm`);
    try {
      const d = new Date(point.time);
      lines.push(d.toISOString().slice(11, 16) + 'Z');
    } catch { /* skip */ }

    // Temperature lines
    const alt = point.altitudeLines;
    if (alt.freezingLevelFt !== null) lines.push(`0°C: ${fmt(alt.freezingLevelFt)} ft`);
    if (alt.minus10cLevelFt !== null) lines.push(`-10°C: ${fmt(alt.minus10cLevelFt)} ft`);
    if (alt.lclAltitudeFt !== null) lines.push(`LCL: ${fmt(alt.lclAltitudeFt)} ft`);

    // Cloud layers
    if (point.cloudLayers.length > 0) {
      lines.push(`Clouds: ${point.cloudLayers.length} layer${point.cloudLayers.length > 1 ? 's' : ''}`);
    }

    // Icing
    const activeIcing = point.icingZones.filter((z) => z.risk !== 'none');
    if (activeIcing.length > 0) {
      const worstRisk = activeIcing.reduce((a, b) => {
        const order = ['light', 'moderate', 'severe'];
        return order.indexOf(b.risk) > order.indexOf(a.risk) ? b.risk : a.risk;
      }, 'none');
      lines.push(`Icing: ${worstRisk}`);
    }

    // Convective
    if (point.convectiveRisk !== 'none') {
      lines.push(`Convective: ${point.convectiveRisk}`);
    }

    tip.innerHTML = lines.join('<br>');
    tip.style.display = 'block';

    // Position tooltip
    const rect = canvas.getBoundingClientRect();
    const tipX = e.clientX - rect.left + 12;
    const tipY = e.clientY - rect.top - 10;

    // Flip if too close to right edge
    const containerW = canvas.parentElement!.clientWidth;
    if (tipX + 160 > containerW) {
      tip.style.left = '';
      tip.style.right = `${containerW - tipX + 24}px`;
    } else {
      tip.style.left = `${tipX}px`;
      tip.style.right = '';
    }
    tip.style.top = `${Math.max(0, tipY)}px`;
  }

  function hideTooltip(): void {
    if (tooltip) {
      tooltip.style.display = 'none';
    }
  }

  canvas.addEventListener('mousemove', handleMouseMove);
  canvas.addEventListener('click', handleClick);
  canvas.addEventListener('mouseleave', handleMouseLeave);

  // Return cleanup function
  return () => {
    canvas.removeEventListener('mousemove', handleMouseMove);
    canvas.removeEventListener('click', handleClick);
    canvas.removeEventListener('mouseleave', handleMouseLeave);
    if (tooltip) {
      tooltip.remove();
      tooltip = null;
    }
    canvas.style.pointerEvents = '';
    canvas.style.cursor = '';
  };
}

function fmt(n: number): string {
  return Math.round(n).toLocaleString();
}
