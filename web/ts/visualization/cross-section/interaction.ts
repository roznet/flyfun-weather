/** Cross-section interaction: hover crosshair, click-to-select, tooltip. */

import type { VizRouteData, VizPoint } from '../types';
import type { CrossSectionRenderer } from './renderer';
import {
  getCanvasX, getCanvasY, findNearestPointIndex, ensureTooltip as ensureTooltipEl,
  positionTooltip, hideTooltip as hideTooltipEl, findNearbyWaypoint,
  fmtFL, altInBand, altNearLine,
} from '../interaction-utils';
import { LAYER_TOOLTIPS } from './tooltip-formatters';
import { columnSpanNm, sigmetSpanNm, COLUMN_HEIGHT_FT } from './layers/current-conditions';
import { formatVisibility } from '../../units';
import { escapeHtml } from '../../utils';

export interface InteractionCallbacks {
  onSelectPoint: (index: number) => void;
  /** Called on hover to sync crosshair with other visualizations. */
  onHover?: (x: number | undefined) => void;
  /** Called on hover with altitude in ft for linked cursor (e.g. Skew-T). */
  onHoverAltitude?: (altFt: number | undefined) => void;
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
  // Suppress browser scroll/zoom gestures so pointermove fires during a touch
  // drag — without this, touch panning eats the events and no crosshair shows.
  canvas.style.touchAction = 'none';

  // Mutable state that can be swapped via update()
  let currentData = data;
  let tooltip: HTMLElement | null = null;

  /** Render crosshair + tooltip at the event position (shared by move/down). */
  function renderAt(e: PointerEvent): void {
    const transform = renderer.createTransform();
    if (!transform) return;

    const x = getCanvasX(e, canvas);
    const y = getCanvasY(e, canvas);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      clearOverlay();
      return;
    }

    renderer.renderOverlay(x, y);
    callbacks.onHover?.(x);

    const distanceNm = transform.xToDistance(x);
    const hoverAltFt = transform.yToAltitude(y);
    callbacks.onHoverAltitude?.(hoverAltFt);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    const point = currentData.points[idx];
    showTooltip(e, point, idx, distanceNm, hoverAltFt);
  }

  function clearOverlay(): void {
    renderer.renderOverlay();
    hideTooltipEl(tooltip);
    callbacks.onHover?.(undefined);
    callbacks.onHoverAltitude?.(undefined);
  }

  // pointerdown gives instant feedback on a touch tap (no hover phase on touch).
  function handlePointerDown(e: PointerEvent): void {
    if (e.button !== 0) return; // ignore right/middle on desktop; touch reports 0
    renderAt(e);
  }

  // pointerup selects the nearest point — equivalent to the old mouse click,
  // and the tap-to-select gesture on touch.
  function handlePointerUp(e: PointerEvent): void {
    if (e.button !== 0) return; // left-click / tap only, matching the old click handler
    const transform = renderer.createTransform();
    if (!transform) return;

    const x = getCanvasX(e, canvas);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = transform.xToDistance(x);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handlePointerLeave(e: PointerEvent): void {
    // Mouse hover-out clears the crosshair. On touch, lifting the finger should
    // leave the crosshair/tooltip pinned where the user tapped.
    if (e.pointerType === 'mouse') clearOverlay();
  }

  // The OS can cancel a touch mid-gesture (notification shade, home-swipe, too
  // many contacts) — pointercancel fires instead of pointerleave, so clear here
  // too or the touch-pinned crosshair would stay stuck.
  function handlePointerCancel(): void {
    clearOverlay();
  }

  function showTooltip(
    e: PointerEvent, point: VizPoint, idx: number,
    distanceNm: number, hoverAltFt: number,
  ): void {
    tooltip = ensureTooltipEl(canvas.parentElement!, tooltip);
    const sections: string[] = [];
    const en = (id: string) => renderer.isLayerEnabled(id);

    // --- Header (always) ---
    const headerLines: string[] = [];
    const wp = findNearbyWaypoint(currentData, point);
    headerLines.push(wp ? `<strong>${wp.icao}</strong>` : `<strong>Point ${idx}</strong>`);
    headerLines.push(`${point.distanceNm.toFixed(0)} nm`);
    try {
      const d = new Date(point.time);
      headerLines.push(d.toISOString().slice(11, 16) + 'Z');
    } catch { /* skip */ }
    headerLines.push(`${fmtFL(hoverAltFt)}`);
    sections.push(headerLines.join('<br>'));

    // --- Terrain ---
    if (en('terrain')) {
      const terrainFt = terrainElevationAt(currentData, distanceNm);
      if (terrainFt > 0 && hoverAltFt <= terrainFt) {
        sections.push(`Terrain: ${fmt(terrainFt)} ft`);
      }
    }

    // --- Band/zone-style layers (driven by tooltip-formatters registry) ---
    for (const def of LAYER_TOOLTIPS) {
      const enabled = en(def.id) || (def.enabledBy?.some(en) ?? false);
      if (!enabled) continue;
      const lines: string[] = [];
      for (const z of def.getZones(point)) {
        if (!altInBand(hoverAltFt, z.baseFt, z.topFt)) continue;
        const line = def.formatLine(z, hoverAltFt);
        if (line !== null) lines.push(line);
      }
      if (lines.length === 0) continue;
      const body = lines.join('<br>');
      sections.push(def.header ? `${def.header}<br>${body}` : body);
    }

    // --- Temperature lines (proximity-based) ---
    const alt = point.altitudeLines;
    if (en('freezing-level') && alt.freezingLevelFt !== null && altNearLine(hoverAltFt, alt.freezingLevelFt)) {
      sections.push(`0°C: ${fmt(alt.freezingLevelFt)} ft`);
    }
    if (en('minus-10c') && alt.minus10cLevelFt !== null && altNearLine(hoverAltFt, alt.minus10cLevelFt)) {
      sections.push(`-10°C: ${fmt(alt.minus10cLevelFt)} ft`);
    }
    if (en('minus-20c') && alt.minus20cLevelFt !== null && altNearLine(hoverAltFt, alt.minus20cLevelFt)) {
      sections.push(`-20°C: ${fmt(alt.minus20cLevelFt)} ft`);
    }

    // --- Stability lines (proximity-based) ---
    if (en('lcl') && alt.lclAltitudeFt !== null && altNearLine(hoverAltFt, alt.lclAltitudeFt)) {
      sections.push(`LCL: ${fmt(alt.lclAltitudeFt)} ft`);
    }
    if (en('lfc') && alt.lfcAltitudeFt !== null && altNearLine(hoverAltFt, alt.lfcAltitudeFt)) {
      sections.push(`LFC: ${fmt(alt.lfcAltitudeFt)} ft`);
    }
    if (en('el') && alt.elAltitudeFt !== null && altNearLine(hoverAltFt, alt.elAltitudeFt)) {
      sections.push(`EL: ${fmt(alt.elAltitudeFt)} ft`);
    }

    // --- Current conditions (route-global; matched by X span + Y band, not per-point) ---
    if (en('current-conditions') && !currentData.timeAxisMode && currentData.currentConditions) {
      const cc = currentData.currentConditions;
      const ccLines: string[] = [];
      for (const a of cc.airports) {
        const [d0, d1] = columnSpanNm(a.enrouteDistanceNm);
        if (distanceNm < d0 || distanceNm > d1) continue;
        if (hoverAltFt < a.baseFt || hoverAltFt > a.baseFt + COLUMN_HEIGHT_FT) continue;
        const parts = [`<strong>${a.icao}</strong> ${a.flightCategory}`];
        if (a.ceilingFt !== null) parts.push(`ceil ${fmtFL(a.ceilingFt)}`);
        if (a.visibilityM !== null) parts.push(`vis ${formatVisibility(a.visibilityM)}`);
        ccLines.push(parts.join(' · '));
        if (a.metarRaw) ccLines.push(`<span class="tt-raw">${escapeHtml(a.metarRaw)}</span>`);
      }
      for (const s of cc.sigmets) {
        const [from, to] = sigmetSpanNm(s.enrouteFromNm, s.enrouteToNm);
        if (distanceNm < from || distanceNm > to) continue;
        if (hoverAltFt < (s.baseFt ?? -Infinity) || hoverAltFt > (s.topFt ?? Infinity)) continue;
        const band = `${s.baseFt !== null ? fmtFL(s.baseFt) : 'SFC'}–${s.topFt !== null ? fmtFL(s.topFt) : 'TOP'}`;
        const q = s.qualifier ? `${s.qualifier} ` : '';
        ccLines.push(`SIGMET ${q}${s.hazard} ${band}`);
        if (s.rawText) ccLines.push(`<span class="tt-raw">${escapeHtml(s.rawText)}</span>`);
      }
      if (ccLines.length) sections.push(`Current conditions<br>${ccLines.join('<br>')}`);
    }

    // Build HTML with section separators
    tooltip.innerHTML = sections
      .map((s) => `<div class="tt-section">${s}</div>`)
      .join('');
    tooltip.style.display = 'block';
    positionTooltip(tooltip, e, canvas, canvas.parentElement!.clientWidth);
  }

  canvas.addEventListener('pointermove', renderAt);
  canvas.addEventListener('pointerdown', handlePointerDown);
  canvas.addEventListener('pointerup', handlePointerUp);
  canvas.addEventListener('pointerleave', handlePointerLeave);
  canvas.addEventListener('pointercancel', handlePointerCancel);

  return {
    update(newData) {
      currentData = newData;
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number): string {
  return Math.round(n).toLocaleString();
}

/** Interpolate terrain elevation at a given distance. */
function terrainElevationAt(data: VizRouteData, distanceNm: number): number {
  if (!data.terrainProfile || data.terrainProfile.length === 0) return 0;
  const profile = data.terrainProfile;
  if (distanceNm <= profile[0].distanceNm) return profile[0].elevationFt;
  if (distanceNm >= profile[profile.length - 1].distanceNm) return profile[profile.length - 1].elevationFt;
  for (let i = 0; i < profile.length - 1; i++) {
    if (distanceNm >= profile[i].distanceNm && distanceNm <= profile[i + 1].distanceNm) {
      const t = (distanceNm - profile[i].distanceNm) / (profile[i + 1].distanceNm - profile[i].distanceNm);
      return profile[i].elevationFt + t * (profile[i + 1].elevationFt - profile[i].elevationFt);
    }
  }
  return 0;
}
