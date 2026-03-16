/** Compare cross-section interaction: hover crosshair, click-to-select, per-model tooltip. */

import type { VizPoint } from '../types';
import type { CompareSectionRenderer, CompareModelData } from './compare-renderer';
import type { ComparableLayer } from './compare-layers';
import {
  getCanvasX, getCanvasY, findNearestPointIndex, ensureTooltip as ensureTooltipEl,
  positionTooltip, hideTooltip as hideTooltipEl, findNearbyWaypoint,
} from '../interaction-utils';
import { modelLabel } from '../../utils';

export interface CompareInteractionCallbacks {
  onSelectPoint: (index: number) => void;
  onHover?: (x: number | undefined) => void;
}

export interface CompareInteractionHandle {
  update(datasets: CompareModelData[], layer: ComparableLayer | null): void;
  destroy(): void;
}

export function attachCompareInteraction(
  renderer: CompareSectionRenderer,
  datasets: CompareModelData[],
  layer: ComparableLayer | null,
  callbacks: CompareInteractionCallbacks,
): CompareInteractionHandle {
  const canvas = renderer.getCanvas();
  canvas.style.pointerEvents = 'auto';
  canvas.style.cursor = 'crosshair';

  let currentDatasets = datasets;
  let currentLayer = layer;
  let tooltip: HTMLElement | null = null;

  function handleMouseMove(e: MouseEvent): void {
    const transform = renderer.createTransform();
    if (!transform || currentDatasets.length === 0) return;

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
    const refData = currentDatasets[0].data;
    const idx = findNearestPointIndex(refData.points, distanceNm);
    showTooltip(e, idx, distanceNm, hoverAltFt);
  }

  function handleClick(e: MouseEvent): void {
    const transform = renderer.createTransform();
    if (!transform || currentDatasets.length === 0) return;

    const x = getCanvasX(e, canvas);
    const { plotArea } = transform;

    if (x < plotArea.left || x > plotArea.left + plotArea.width) return;

    const distanceNm = transform.xToDistance(x);
    const refData = currentDatasets[0].data;
    const idx = findNearestPointIndex(refData.points, distanceNm);
    callbacks.onSelectPoint(idx);
  }

  function handleMouseLeave(): void {
    renderer.renderOverlay();
    hideTooltipEl(tooltip);
    callbacks.onHover?.(undefined);
  }

  function showTooltip(
    e: MouseEvent, idx: number, distanceNm: number, hoverAltFt: number,
  ): void {
    tooltip = ensureTooltipEl(canvas.parentElement!, tooltip);
    const sections: string[] = [];
    const refData = currentDatasets[0].data;
    const refPoint = refData.points[idx];

    // Header
    const headerLines: string[] = [];
    const wp = findNearbyWaypoint(refData, refPoint);
    headerLines.push(wp ? `<strong>${wp.icao}</strong>` : `<strong>Point ${idx}</strong>`);
    headerLines.push(`${refPoint.distanceNm.toFixed(0)} nm`);
    try {
      const d = new Date(refPoint.time);
      headerLines.push(d.toISOString().slice(11, 16) + 'Z');
    } catch { /* skip */ }
    headerLines.push(`${fmtFL(hoverAltFt)}`);
    sections.push(headerLines.join('<br>'));

    if (!currentLayer) {
      tooltip.innerHTML = sections.map((s) => `<div class="tt-section">${s}</div>`).join('');
      tooltip.style.display = 'block';
      positionTooltip(tooltip, e, canvas, canvas.parentElement!.clientWidth);
      return;
    }

    // Layer name
    sections.push(`<strong>${currentLayer.name}</strong>`);

    // Per-model details
    const modelLines: string[] = [];
    let hitCount = 0;

    for (const dataset of currentDatasets) {
      const point = dataset.data.points[idx];
      if (!point) continue;

      const label = modelLabel(dataset.model);
      const hit = currentLayer.type === 'band'
        ? bandHitTest(point, currentLayer.id, hoverAltFt)
        : lineHitTest(point, currentLayer, hoverAltFt);

      if (hit) {
        hitCount++;
        modelLines.push(`${label} \u2713 ${hit}`);
      } else {
        modelLines.push(`<span style="opacity:0.5">${label} \u2717</span>`);
      }
    }

    sections.push(modelLines.join('<br>'));

    if (currentDatasets.length > 1) {
      sections.push(`Agreement: ${hitCount}/${currentDatasets.length} models`);
    }

    tooltip.innerHTML = sections.map((s) => `<div class="tt-section">${s}</div>`).join('');
    tooltip.style.display = 'block';
    positionTooltip(tooltip, e, canvas, canvas.parentElement!.clientWidth);
  }

  canvas.addEventListener('mousemove', handleMouseMove);
  canvas.addEventListener('click', handleClick);
  canvas.addEventListener('mouseleave', handleMouseLeave);

  return {
    update(newDatasets, newLayer) {
      currentDatasets = newDatasets;
      currentLayer = newLayer;
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

function fmtFL(ft: number): string {
  if (ft >= 5000) return `FL${Math.round(ft / 100).toString().padStart(3, '0')}`;
  return `${Math.round(ft).toLocaleString()} ft`;
}

function altInBand(hoverAlt: number, baseFt: number, topFt: number): boolean {
  return hoverAlt >= baseFt && hoverAlt <= topFt;
}

/** Hit-test for band layers: check if hover altitude falls within any zone. */
function bandHitTest(point: VizPoint, layerId: string, hoverAltFt: number): string | null {
  switch (layerId) {
    case 'icing-bands':
      for (const z of point.icingZones) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          return `${z.risk} ${fmtFL(z.baseFt)}\u2013${fmtFL(z.topFt)}`;
        }
      }
      return null;

    case 'icing-ogimet-nwp-bands':
      for (const z of point.icingOgimetNwpZones) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          return `${z.risk} ${fmtFL(z.baseFt)}\u2013${fmtFL(z.topFt)}`;
        }
      }
      return null;

    case 'sfip-bands':
      for (const z of point.sfipZones) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          return `${z.risk} ${fmtFL(z.baseFt)}\u2013${fmtFL(z.topFt)}`;
        }
      }
      return null;

    case 'cloud-bands':
      for (const cl of point.cloudLayers) {
        if (altInBand(hoverAltFt, cl.baseFt, cl.topFt)) {
          return `${cl.coverage} ${fmtFL(cl.baseFt)}\u2013${fmtFL(cl.topFt)}`;
        }
      }
      return null;

    case 'nwp-cloud-bands':
      for (const cl of point.nwpCloudLayers) {
        if (altInBand(hoverAltFt, cl.baseFt, cl.topFt)) {
          return `${cl.coverage} ${fmtFL(cl.baseFt)}\u2013${fmtFL(cl.topFt)}`;
        }
      }
      return null;

    case 'cat-bands':
      for (const z of point.catLayers) {
        if (z.risk !== 'none' && altInBand(hoverAltFt, z.baseFt, z.topFt)) {
          return `${z.risk} ${fmtFL(z.baseFt)}\u2013${fmtFL(z.topFt)}`;
        }
      }
      return null;

    case 'inversion-bands':
      for (const inv of point.inversions) {
        if (altInBand(hoverAltFt, inv.baseFt, inv.topFt)) {
          return `+${inv.strengthC.toFixed(1)}\u00B0C`;
        }
      }
      return null;

    case 'thermo-convective-bg':
      if (point.convectiveRisk !== 'none' &&
          point.convectiveBaseFt !== null && point.convectiveTopFt !== null &&
          altInBand(hoverAltFt, point.convectiveBaseFt, point.convectiveTopFt)) {
        return point.convectiveRisk;
      }
      return null;

    case 'nwp-convective-bg':
      if (point.nwpConvectiveRisk !== 'none' &&
          point.nwpConvectiveBaseFt !== null && point.nwpConvectiveTopFt !== null &&
          altInBand(hoverAltFt, point.nwpConvectiveBaseFt, point.nwpConvectiveTopFt)) {
        return point.nwpConvectiveRisk;
      }
      return null;

    default:
      return null;
  }
}

/** Hit-test for line layers: check proximity to the altitude value. */
function lineHitTest(point: VizPoint, layer: ComparableLayer, hoverAltFt: number): string | null {
  if (!layer.lineAccessor) return null;
  const val = layer.lineAccessor(point);
  if (val === null) return null;
  if (Math.abs(hoverAltFt - val) <= 1500) {
    return `${Math.round(val).toLocaleString()} ft`;
  }
  return null;
}
