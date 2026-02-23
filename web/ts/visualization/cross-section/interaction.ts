/** Cross-section interaction: hover crosshair, click-to-select, tooltip. */

import type { VizRouteData, VizPoint } from '../types';
import type { CrossSectionRenderer } from './renderer';
import {
  getCanvasX, findNearestPointIndex, ensureTooltip as ensureTooltipEl,
  positionTooltip, hideTooltip as hideTooltipEl, findNearbyWaypoint,
} from '../interaction-utils';

export interface InteractionCallbacks {
  onSelectPoint: (index: number) => void;
  /** Called on hover to sync crosshair with other visualizations. */
  onHover?: (x: number | undefined) => void;
}

/** Returned by attachInteraction — allows updating data without re-attaching listeners. */
export interface InteractionHandle {
  /** Update the closed-over data without tearing down listeners. */
  update(data: VizRouteData): void;
  /** Remove all listeners and clean up tooltip. */
  destroy(): void;
}

export function attachInteraction(
  renderer: CrossSectionRenderer,
  data: VizRouteData,
  callbacks: InteractionCallbacks,
): InteractionHandle {
  const canvas = renderer.getCanvas();
  canvas.style.pointerEvents = 'auto';
  canvas.style.cursor = 'crosshair';

  // Mutable state that can be swapped via update()
  let currentData = data;
  let tooltip: HTMLElement | null = null;

  function handleMouseMove(e: MouseEvent): void {
    const transform = renderer.createTransform();
    if (!transform) return;

    const x = getCanvasX(e, canvas);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      renderer.renderOverlay();
      hideTooltipEl(tooltip);
      callbacks.onHover?.(undefined);
      return;
    }

    renderer.renderOverlay(x);
    callbacks.onHover?.(x);

    const distanceNm = transform.xToDistance(x);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    const point = currentData.points[idx];
    showTooltip(e, point, idx);
  }

  function handleClick(e: MouseEvent): void {
    const transform = renderer.createTransform();
    if (!transform) return;

    const x = getCanvasX(e, canvas);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = transform.xToDistance(x);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handleMouseLeave(): void {
    renderer.renderOverlay();
    hideTooltipEl(tooltip);
    callbacks.onHover?.(undefined);
  }

  function showTooltip(e: MouseEvent, point: VizPoint, idx: number): void {
    tooltip = ensureTooltipEl(canvas.parentElement!, tooltip);
    const lines: string[] = [];

    const wp = findNearbyWaypoint(currentData, point);
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
      const order = ['none', 'light', 'moderate', 'severe'];
      const worstRisk = activeIcing.reduce((worst, z) =>
        order.indexOf(z.risk) > order.indexOf(worst) ? z.risk : worst
      , activeIcing[0].risk);
      lines.push(`Icing: ${worstRisk}`);
    }

    // Convective with tower bounds
    if (point.convectiveRisk !== 'none') {
      let convLine = `Convective: ${point.convectiveRisk}`;
      if (point.capeSurfaceJkg > 0) convLine += ` (CAPE ${Math.round(point.capeSurfaceJkg)})`;
      lines.push(convLine);
      if (alt.lclAltitudeFt !== null && alt.elAltitudeFt !== null) {
        const baseFt = alt.lfcAltitudeFt ?? alt.lclAltitudeFt;
        lines.push(`Tower: ${fmt(baseFt)}–${fmt(alt.elAltitudeFt)} ft`);
      }
    }

    tooltip.innerHTML = lines.join('<br>');
    tooltip.style.display = 'block';
    positionTooltip(tooltip, e, canvas, canvas.parentElement!.clientWidth);
  }

  canvas.addEventListener('mousemove', handleMouseMove);
  canvas.addEventListener('click', handleClick);
  canvas.addEventListener('mouseleave', handleMouseLeave);

  return {
    update(newData) {
      currentData = newData;
    },
    destroy() {
      canvas.removeEventListener('mousemove', handleMouseMove);
      canvas.removeEventListener('click', handleClick);
      canvas.removeEventListener('mouseleave', handleMouseLeave);
      if (tooltip) { tooltip.remove(); tooltip = null; }
      canvas.style.pointerEvents = '';
      canvas.style.cursor = '';
    },
  };
}

function fmt(n: number): string {
  return Math.round(n).toLocaleString();
}
