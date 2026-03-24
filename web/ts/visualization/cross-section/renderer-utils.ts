/** Shared utilities for CrossSectionRenderer and CompareSectionRenderer. */

import type { CoordTransform, PlotArea } from '../types';
import { getActiveTheme } from './theme';

export const CROSS_SECTION_MARGIN = { left: 60, right: 50, top: 20, bottom: 50 };

/** Create a coordinate transform for the given container and data dimensions. */
export function createCoordTransform(
  container: HTMLElement,
  data: { totalDistanceNm: number; flightCeilingFt: number },
): CoordTransform | null {
  const cssW = container.clientWidth;
  const cssH = container.clientHeight;
  if (cssW === 0 || cssH === 0) return null;

  const plotArea: PlotArea = {
    left: CROSS_SECTION_MARGIN.left,
    top: CROSS_SECTION_MARGIN.top,
    width: cssW - CROSS_SECTION_MARGIN.left - CROSS_SECTION_MARGIN.right,
    height: cssH - CROSS_SECTION_MARGIN.top - CROSS_SECTION_MARGIN.bottom,
  };

  const maxDist = data.totalDistanceNm;
  const maxAlt = data.flightCeilingFt;

  return {
    distanceToX: (d: number) => plotArea.left + (d / maxDist) * plotArea.width,
    altitudeToY: (a: number) => plotArea.top + (1 - a / maxAlt) * plotArea.height,
    xToDistance: (x: number) => ((x - plotArea.left) / plotArea.width) * maxDist,
    yToAltitude: (y: number) => (1 - (y - plotArea.top) / plotArea.height) * maxAlt,
    plotArea,
  };
}

/** Render crosshair overlay with selected-point indicator and hover lines. */
export function renderCrosshairOverlay(
  overlayCanvas: HTMLCanvasElement,
  container: HTMLElement,
  transform: CoordTransform | null,
  points: { distanceNm: number }[] | null,
  selectedPointIndex: number,
  hoverX?: number,
  hoverY?: number,
): void {
  const cssW = container.clientWidth;
  const cssH = container.clientHeight;
  if (cssW === 0 || cssH === 0) return;

  const dpr = window.devicePixelRatio || 1;
  const ctx = overlayCanvas.getContext('2d')!;
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cssW, cssH);

  if (!transform || !points) { ctx.restore(); return; }

  const { plotArea } = transform;
  const theme = getActiveTheme();

  // Selected point indicator
  if (selectedPointIndex >= 0 && selectedPointIndex < points.length) {
    const pt = points[selectedPointIndex];
    const x = transform.distanceToX(pt.distanceNm);
    ctx.strokeStyle = 'rgba(37, 99, 235, 0.6)';
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();
  }

  // Hover crosshair (vertical)
  const crosshairColor = theme.axes.waypointLineColor;
  if (hoverX !== undefined && hoverX >= plotArea.left && hoverX <= plotArea.left + plotArea.width) {
    ctx.strokeStyle = crosshairColor;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(hoverX, plotArea.top);
    ctx.lineTo(hoverX, plotArea.top + plotArea.height);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Hover crosshair (horizontal)
  if (hoverY !== undefined && hoverY >= plotArea.top && hoverY <= plotArea.top + plotArea.height) {
    ctx.strokeStyle = crosshairColor;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(plotArea.left, hoverY);
    ctx.lineTo(plotArea.left + plotArea.width, hoverY);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.restore();
}

/** Set canvas pixel dimensions for the given CSS size and device pixel ratio. */
export function setupCanvasDpr(canvas: HTMLCanvasElement, cssW: number, cssH: number, dpr: number): void {
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
}
