/** Map interaction: hover tooltip on segments, click-to-select, sync handle. */

import type L from 'leaflet';
import type { VizRouteData } from '../types';
import type { MapMetric } from './metrics';
import type { RouteMapRenderer } from './renderer';
import { findNearbyWaypoint } from '../interaction-utils';

export interface MapInteractionCallbacks {
  onSelectPoint: (index: number) => void;
  onHover: (index: number | undefined) => void;
}

export interface MapInteractionHandle {
  update(data: VizRouteData, color: MapMetric | null, width: MapMetric | null, altFt?: number): void;
  destroy(): void;
}

export function attachMapInteraction(
  renderer: RouteMapRenderer,
  data: VizRouteData,
  colorMetric: MapMetric | null,
  widthMetric: MapMetric | null,
  altitudeFt: number,
  callbacks: MapInteractionCallbacks,
): MapInteractionHandle {
  let currentData = data;
  let currentColor = colorMetric;
  let currentWidth = widthMetric;
  let currentAltFt = altitudeFt;

  const segmentGroup = renderer.getSegmentGroup();
  if (!segmentGroup) {
    return { update() {}, destroy() {} };
  }

  // The segment currently showing a tapped (touch) tooltip, so we can reset it
  // when another segment is tapped — touch has no mouseout to clean up after.
  let tappedLayer: any = null;

  /** Highlight a segment and open its metric tooltip. Shared by hover + tap. */
  function highlightSegment(layer: any): void {
    const idx: number | undefined = layer._segmentIndex;
    if (idx === undefined) return;

    layer.setStyle({ weight: (layer.options.weight ?? 4) + 3, opacity: 1 });

    const p1 = currentData.points[idx];
    const p2 = currentData.points[idx + 1];
    if (!p1 || !p2) return;

    const lines: string[] = [];
    const wp = findNearbyWaypoint(currentData, p1);
    if (wp) lines.push(`<strong>${wp.icao}</strong>`);
    lines.push(`${p1.distanceNm.toFixed(0)} nm`);

    if (currentColor) {
      const v = currentColor.getValue(p1, currentAltFt);
      if (v !== null) {
        lines.push(`${currentColor.label}: ${currentColor.formatValue(v)}`);
      }
    }
    if (currentWidth && currentWidth.id !== currentColor?.id) {
      const v = currentWidth.getValue(p1, currentAltFt);
      if (v !== null) {
        lines.push(`${currentWidth.label}: ${currentWidth.formatValue(v)}`);
      }
    }

    layer.bindTooltip(lines.join('<br>'), { sticky: true, direction: 'top', offset: [0, -10] }).openTooltip();
    callbacks.onHover(idx);
  }

  /** Reset a segment's style and tear down its tooltip. */
  function resetSegment(layer: any): void {
    layer.setStyle({ weight: layer.options._originalWeight ?? 4, opacity: 0.9 });
    layer.closeTooltip();
    layer.unbindTooltip();
  }

  function onSegmentMouseOver(e: L.LeafletEvent): void {
    highlightSegment(e.target as any);
  }

  function onSegmentMouseOut(e: L.LeafletEvent): void {
    resetSegment(e.target as any);
    callbacks.onHover(undefined);
  }

  function onSegmentClick(e: L.LeafletEvent): void {
    const layer = e.target as any;
    const idx: number | undefined = layer._segmentIndex;
    if (idx === undefined) return;

    // On touch there is no hover phase, so a tap must surface the segment's
    // tooltip itself. Reset any previously-tapped segment first, then show this
    // one. (On desktop mouseover already showed it — re-showing is harmless.)
    if (tappedLayer && tappedLayer !== layer) resetSegment(tappedLayer);
    highlightSegment(layer);
    tappedLayer = layer;

    callbacks.onSelectPoint(idx);
  }

  // Attach event listeners to all segment layers
  function attachListeners(): void {
    segmentGroup!.eachLayer((layer: any) => {
      // Store original weight for restore
      layer.options._originalWeight = layer.options.weight;

      layer.on('mouseover', onSegmentMouseOver);
      layer.on('mouseout', onSegmentMouseOut);
      layer.on('click', onSegmentClick);
    });
  }

  function detachListeners(): void {
    // Reset the tapped segment's style before dropping the reference — if the
    // caller updates data without recreating layers, it would otherwise stay
    // highlighted permanently.
    if (tappedLayer) {
      resetSegment(tappedLayer);
      tappedLayer = null;
    }
    segmentGroup!.eachLayer((layer: any) => {
      layer.off('mouseover', onSegmentMouseOver);
      layer.off('mouseout', onSegmentMouseOut);
      layer.off('click', onSegmentClick);
    });
  }

  attachListeners();

  return {
    update(newData, newColor, newWidth, altFt) {
      detachListeners();
      currentData = newData;
      currentColor = newColor;
      currentWidth = newWidth;
      if (altFt !== undefined) currentAltFt = altFt;
      attachListeners();
    },
    destroy() {
      detachListeners();
    },
  };
}
