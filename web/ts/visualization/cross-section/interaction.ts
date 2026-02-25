/** Cross-section interaction: hover crosshair, click-to-select, tooltip. */

import type { VizRouteData, VizPoint, VizCloudDiag } from '../types';
import type { CrossSectionRenderer } from './renderer';
import {
  getCanvasX, getCanvasY, findNearestPointIndex, ensureTooltip as ensureTooltipEl,
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
    const y = getCanvasY(e, canvas);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) {
      renderer.renderOverlay();
      hideTooltipEl(tooltip);
      callbacks.onHover?.(undefined);
      return;
    }

    renderer.renderOverlay(x, y);
    callbacks.onHover?.(x);

    const distanceNm = transform.xToDistance(x);
    const hoverAltFt = transform.yToAltitude(y);
    const idx = findNearestPointIndex(currentData.points, distanceNm);
    const point = currentData.points[idx];
    showTooltip(e, point, idx, distanceNm, hoverAltFt);
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

  function showTooltip(
    e: MouseEvent, point: VizPoint, idx: number,
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

    // --- Cloud bands ---
    if (en('cloud-bands') && point.cloudLayers.length > 0) {
      const lines: string[] = [];
      for (const cl of point.cloudLayers) {
        if (altInBand(hoverAltFt, cl.baseFt, cl.topFt)) {
          let line = `${fmtFL(cl.baseFt)}–${fmtFL(cl.topFt)} ${cl.coverage}`;
          if (cl.meanDewpointDepressionC !== undefined) {
            line += ` (DD ${cl.meanDewpointDepressionC.toFixed(1)}°C)`;
          }
          lines.push(line);
        }
      }
      if (lines.length > 0) sections.push(lines.join('<br>'));
    }

    // --- NWP Cloud bands ---
    if (en('nwp-cloud-bands') && point.nwpCloudDiag) {
      const diag = point.nwpCloudDiag;
      const nwpLines: string[] = [];
      for (const [label, layer] of nwpDiagLayers(diag)) {
        if (layer.coverPct !== null && layer.coverPct > 0 &&
            layer.baseFt !== null && layer.topFt !== null &&
            altInBand(hoverAltFt, layer.baseFt, layer.topFt)) {
          nwpLines.push(`NWP ${label}: ${Math.round(layer.coverPct)}% (${fmtFL(layer.baseFt)}–${fmtFL(layer.topFt)})`);
        }
      }
      if (nwpLines.length > 0) sections.push(nwpLines.join('<br>'));
    }

    // --- Icing bands ---
    if (en('icing-bands')) {
      const lines: string[] = [];
      for (const z of point.icingZones) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          lines.push(`${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} ${z.risk} ${z.type}`);
        }
      }
      if (lines.length > 0) sections.push(`Icing<br>${lines.join('<br>')}`);
    }

    // --- SFIP bands ---
    if (en('sfip-bands')) {
      const lines: string[] = [];
      for (const z of point.sfipZones) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          const sfipVal = z.meanSfip100 !== null ? ` ${Math.round(z.meanSfip100)}/100` : '';
          const tag = z.variant === 'proxy' ? ' (PROXY)' : z.variant === 'interp' ? ' (INTERP)' : '';
          lines.push(`${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} SFIP${sfipVal}${tag}`);
        }
      }
      if (lines.length > 0) sections.push(lines.join('<br>'));
    }

    // --- CAT bands ---
    if (en('cat-bands')) {
      const lines: string[] = [];
      for (const z of point.catLayers) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          lines.push(`${fmtFL(z.baseFt)}–${fmtFL(z.topFt)} CAT ${z.risk}`);
        }
      }
      if (lines.length > 0) sections.push(lines.join('<br>'));
    }

    // --- Inversions ---
    if (en('inversion-bands')) {
      const lines: string[] = [];
      for (const inv of point.inversions) {
        if (altInBand(hoverAltFt, inv.baseFt, inv.topFt)) {
          lines.push(`${fmt(inv.baseFt)}–${fmt(inv.topFt)} ft +${inv.strengthC.toFixed(1)}°C`);
        }
      }
      if (lines.length > 0) sections.push(`Inversion<br>${lines.join('<br>')}`);
    }

    // --- Convective ---
    if (en('convective-bg') && point.convectiveRisk !== 'none') {
      const alt = point.altitudeLines;
      const baseFt = alt.lfcAltitudeFt ?? alt.lclAltitudeFt;
      const topFt = alt.elAltitudeFt;
      if (baseFt !== null && topFt !== null && altInBand(hoverAltFt, baseFt, topFt)) {
        let line = `Convective: ${point.convectiveRisk}`;
        if (point.capeSurfaceJkg > 0) line += ` (CAPE ${Math.round(point.capeSurfaceJkg)})`;
        line += `<br>Tower: ${fmtFL(baseFt)}–${fmtFL(topFt)}`;
        sections.push(line);
      }
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

    // Build HTML with section separators
    tooltip.innerHTML = sections
      .map((s) => `<div class="tt-section">${s}</div>`)
      .join('');
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number): string {
  return Math.round(n).toLocaleString();
}

/** Format altitude as FL (≥ 5000 ft) or feet. */
function fmtFL(ft: number): string {
  if (ft >= 5000) return `FL${Math.round(ft / 100).toString().padStart(3, '0')}`;
  return `${fmt(ft)} ft`;
}

/** True if hover altitude falls within [baseFt, topFt]. */
function altInBand(hoverAlt: number, baseFt: number, topFt: number): boolean {
  return hoverAlt >= baseFt && hoverAlt <= topFt;
}

/** True if hover altitude is within tolerance (ft) of a line altitude. */
function altNearLine(hoverAlt: number, lineAlt: number, tolerance = 1500): boolean {
  return Math.abs(hoverAlt - lineAlt) <= tolerance;
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

/** Iterate NWP cloud diagnostic layers as [label, layer] pairs. */
function nwpDiagLayers(diag: VizCloudDiag): [string, { coverPct: number | null; baseFt: number | null; topFt: number | null }][] {
  return [['Low', diag.low], ['Mid', diag.mid], ['High', diag.high]];
}
