/** Compare cross-section renderer: one layer across N models. */

import type { CoordTransform, VizRouteData, CompareBandMode } from '../types';
import type { ComparableLayer } from './compare-layers';
import { drawAxes } from './axes';
import { terrainFillLayer } from './layers/terrain-fill';
import { cruiseAltitudeLayer } from './layers/reference-lines';
import { getAllLayers } from './layer-registry';
import { drawSmoothBand, drawSmoothLine, type BandPointData, type PointData } from './layers/base';
import { getActiveTheme } from './theme';
import { getZonesForLayer } from './compare-zone-access';
import { createCoordTransform, renderCrosshairOverlay, setupCanvasDpr } from './renderer-utils';

export interface CompareModelData {
  model: string;
  data: VizRouteData;
}

export class CompareSectionRenderer {
  private container: HTMLElement;
  private mainCanvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private offscreenCanvas: HTMLCanvasElement;
  private resizeObserver: ResizeObserver;
  private datasets: CompareModelData[] = [];
  private compareLayer: ComparableLayer | null = null;
  private bandMode: CompareBandMode = 'consensus-outline';
  private selectedPointIndex = -1;

  constructor(container: HTMLElement) {
    this.container = container;

    this.mainCanvas = document.createElement('canvas');
    this.mainCanvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
    container.appendChild(this.mainCanvas);

    this.overlayCanvas = document.createElement('canvas');
    this.overlayCanvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
    container.appendChild(this.overlayCanvas);

    // Offscreen canvas for band layer compositing
    this.offscreenCanvas = document.createElement('canvas');

    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(container);

    window.addEventListener('theme-changed', () => this.render());
  }

  setModelData(datasets: CompareModelData[]): void {
    this.datasets = datasets;
  }

  setCompareLayer(layer: ComparableLayer | null): void {
    this.compareLayer = layer;
  }

  setBandMode(mode: CompareBandMode): void {
    this.bandMode = mode;
  }

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.renderOverlay();
  }

  getCanvas(): HTMLCanvasElement {
    return this.overlayCanvas;
  }

  /** Create a coordinate transform using the first dataset's dimensions. */
  createTransform(): CoordTransform | null {
    if (this.datasets.length === 0) return null;
    return createCoordTransform(this.container, this.datasets[0].data);
  }

  render(): void {
    if (this.datasets.length === 0) return;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return;

    const dpr = window.devicePixelRatio || 1;
    setupCanvasDpr(this.mainCanvas, cssW, cssH, dpr);
    setupCanvasDpr(this.overlayCanvas, cssW, cssH, dpr);
    setupCanvasDpr(this.offscreenCanvas, cssW, cssH, dpr);

    const ctx = this.mainCanvas.getContext('2d')!;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const transform = this.createTransform();
    if (!transform) { ctx.restore(); return; }

    // Sky-blue background
    const { plotArea } = transform;
    ctx.fillStyle = getActiveTheme().sky.background;
    ctx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

    // Axes (using first dataset for waypoints/distance)
    drawAxes(ctx, transform, this.datasets[0].data);

    // Clip to plot area for layer rendering
    ctx.save();
    ctx.beginPath();
    ctx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
    ctx.clip();

    // Render the selected compare layer
    if (this.compareLayer) {
      if (this.compareLayer.type === 'band') {
        if (this.bandMode === 'overlay') {
          this.renderBandLayerOverlay(ctx, transform, cssW, cssH, dpr);
        } else if (this.bandMode === 'overlay-soft') {
          this.renderOverlaySoft(ctx, transform);
        } else {
          this.renderConsensusBand(ctx, transform, this.bandMode === 'consensus-outline');
        }
      } else {
        this.renderLineEnvelope(ctx, transform);
      }
    }

    // Terrain and reference lines (from first dataset)
    terrainFillLayer.render(ctx, transform, this.datasets[0].data);
    ctx.restore(); // unclip

    // Cruise altitude on top (unclipped so labels can extend)
    ctx.save();
    ctx.beginPath();
    ctx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
    ctx.clip();
    cruiseAltitudeLayer.render(ctx, transform, this.datasets[0].data);
    ctx.restore();

    ctx.restore();
    this.renderOverlay();
  }

  renderOverlay(hoverX?: number, hoverY?: number): void {
    const points = this.datasets.length > 0 ? this.datasets[0].data.points : null;
    renderCrosshairOverlay(
      this.overlayCanvas, this.container,
      this.createTransform(), points,
      this.selectedPointIndex, hoverX, hoverY,
    );
  }

  destroy(): void {
    this.resizeObserver.disconnect();
    this.mainCanvas.remove();
    this.overlayCanvas.remove();
  }

  private renderBandLayerOverlay(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
    cssW: number,
    cssH: number,
    dpr: number,
  ): void {
    if (!this.compareLayer) return;

    // Find the CrossSectionLayer with matching id
    const crossSectionLayer = getAllLayers().find((l) => l.id === this.compareLayer!.id);
    if (!crossSectionLayer) return;

    const numModels = this.datasets.length;
    // Higher per-model alpha so overlapping areas compound more visibly
    const alpha = Math.min(0.9, Math.max(0.25, 2.0 / numModels));

    const offCtx = this.offscreenCanvas.getContext('2d')!;

    const { plotArea } = transform;

    // Use 'multiply' blending so overlapping model areas compound darker,
    // making agreement areas visually distinct from single-model areas
    for (const dataset of this.datasets) {
      offCtx.save();
      offCtx.scale(dpr, dpr);
      offCtx.clearRect(0, 0, cssW, cssH);

      offCtx.beginPath();
      offCtx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
      offCtx.clip();

      crossSectionLayer.render(offCtx, transform, dataset.data);
      offCtx.restore();

      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.drawImage(this.offscreenCanvas, 0, 0, cssW * dpr, cssH * dpr, 0, 0, cssW, cssH);
      ctx.restore();
    }
  }

  /** Overlay Soft: each model's zones rendered as feathered gradient fills.
   *  Where models overlap, the soft fills compound — denser = more agreement. */
  private renderOverlaySoft(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
  ): void {
    if (!this.compareLayer) return;
    const layerId = this.compareLayer.id;
    const numModels = this.datasets.length;
    if (numModels === 0) return;

    const theme = getActiveTheme();
    const baseRgb = this.getLayerBaseRgb(layerId, theme);
    // Per-model alpha: enough to be visible solo, compounds on overlap
    const modelAlpha = Math.min(0.7, Math.max(0.25, 1.4 / numModels));

    const FEATHER_FRACTION = 0.15;

    for (const dataset of this.datasets) {
      const points = dataset.data.points;

      for (let i = 0; i < points.length - 1; i++) {
        const curr = points[i];
        const next = points[i + 1];
        const currZones = getZonesForLayer(layerId, curr);
        const nextZones = getZonesForLayer(layerId, next);
        const usedNext = new Set<number>();

        const drawSoftZoneBand = (bandPoints: BandPointData[]) => {
          // Find bounding Y for the gradient
          let minY = Infinity, maxY = -Infinity;
          for (const bp of bandPoints) {
            if (bp.top == null || bp.base == null) continue;
            const yTop = transform.altitudeToY(bp.top);
            const yBase = transform.altitudeToY(bp.base);
            minY = Math.min(minY, yTop, yBase);
            maxY = Math.max(maxY, yTop, yBase);
          }
          const bandHeight = maxY - minY;
          if (bandHeight <= 0) return;

          const featherPx = Math.max(2, bandHeight * FEATHER_FRACTION);
          const topStop = Math.min(featherPx / bandHeight, 0.3);
          const botStop = Math.max(1.0 - featherPx / bandHeight, 0.7);

          const color = `rgba(${baseRgb[0]}, ${baseRgb[1]}, ${baseRgb[2]}, ${modelAlpha})`;
          const transparent = `rgba(${baseRgb[0]}, ${baseRgb[1]}, ${baseRgb[2]}, 0)`;

          const grad = ctx.createLinearGradient(0, minY, 0, maxY);
          grad.addColorStop(0, transparent);
          grad.addColorStop(topStop, color);
          grad.addColorStop(botStop, color);
          grad.addColorStop(1, transparent);

          drawSmoothBand(ctx, bandPoints, transform, grad as unknown as string);
        };

        // Match zones between adjacent points (same logic as zone-matching.ts)
        for (const cz of currZones) {
          let bestIdx = -1;
          let bestOverlap = 0;
          for (let j = 0; j < nextZones.length; j++) {
            if (usedNext.has(j)) continue;
            const overlap = Math.min(cz.topFt, nextZones[j].topFt) -
                            Math.max(cz.baseFt, nextZones[j].baseFt);
            if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
          }

          if (bestIdx >= 0) {
            usedNext.add(bestIdx);
            const nz = nextZones[bestIdx];
            drawSoftZoneBand([
              { distance: curr.distanceNm, base: cz.baseFt, top: cz.topFt },
              { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
            ]);
          } else {
            const midDist = (curr.distanceNm + next.distanceNm) / 2;
            const midAlt = (cz.baseFt + cz.topFt) / 2;
            drawSoftZoneBand([
              { distance: curr.distanceNm, base: cz.baseFt, top: cz.topFt },
              { distance: midDist, base: midAlt, top: midAlt },
            ]);
          }
        }

        // Unmatched next zones
        for (let j = 0; j < nextZones.length; j++) {
          if (usedNext.has(j)) continue;
          const nz = nextZones[j];
          const midDist = (curr.distanceNm + next.distanceNm) / 2;
          const midAlt = (nz.baseFt + nz.topFt) / 2;
          drawSoftZoneBand([
            { distance: midDist, base: midAlt, top: midAlt },
            { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
          ]);
        }
      }
    }
  }

  /** Consensus band rendering: fill opacity proportional to model agreement.
   *  When showOutlines is true, also draws per-model zone boundaries. */
  private renderConsensusBand(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
    showOutlines: boolean,
  ): void {
    if (!this.compareLayer) return;
    const layerId = this.compareLayer.id;
    const numModels = this.datasets.length;
    if (numModels === 0) return;

    const refPoints = this.datasets[0].data.points;
    const theme = getActiveTheme();
    const baseRgb = this.getLayerBaseRgb(layerId, theme);

    // For each pair of adjacent route points, compute consensus intervals
    // and render them as bands.
    for (let i = 0; i < refPoints.length - 1; i++) {
      const dist0 = refPoints[i].distanceNm;
      const dist1 = this.datasets[0].data.points[i + 1]?.distanceNm;
      if (dist1 === undefined) continue;

      // Collect zones at this point and next from all models
      const zonesAtCurr = this.datasets.map((ds) =>
        i < ds.data.points.length ? getZonesForLayer(layerId, ds.data.points[i]) : []);
      const zonesAtNext = this.datasets.map((ds) =>
        i + 1 < ds.data.points.length ? getZonesForLayer(layerId, ds.data.points[i + 1]) : []);

      // Build consensus intervals at current point
      const consensusCurr = buildConsensusIntervals(zonesAtCurr, numModels);
      const consensusNext = buildConsensusIntervals(zonesAtNext, numModels);

      // Render consensus fill: for each interval at curr, find the best matching
      // interval at next and draw a smooth band between them.
      const usedNext = new Set<number>();
      for (const ci of consensusCurr) {
        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < consensusNext.length; j++) {
          if (usedNext.has(j)) continue;
          const overlap = Math.min(ci.topFt, consensusNext[j].topFt) -
                          Math.max(ci.baseFt, consensusNext[j].baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        // Average the consensus ratio between the two endpoints
        const ratio = bestIdx >= 0
          ? (ci.count / numModels + consensusNext[bestIdx].count / numModels) / 2
          : ci.count / numModels;
        // Non-linear: single-model faint, full-agreement vivid
        const alpha = 0.08 + 0.82 * ratio * ratio;
        const fill = `rgba(${baseRgb[0]}, ${baseRgb[1]}, ${baseRgb[2]}, ${alpha.toFixed(2)})`;

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const ni = consensusNext[bestIdx];
          drawSmoothBand(ctx, [
            { distance: dist0, base: ci.baseFt, top: ci.topFt },
            { distance: dist1, base: ni.baseFt, top: ni.topFt },
          ], transform, fill);
        } else {
          // Taper to midpoint
          const midDist = (dist0 + dist1) / 2;
          const midAlt = (ci.baseFt + ci.topFt) / 2;
          drawSmoothBand(ctx, [
            { distance: dist0, base: ci.baseFt, top: ci.topFt },
            { distance: midDist, base: midAlt, top: midAlt },
          ], transform, fill);
        }
      }

      // Unmatched next intervals (appear mid-segment)
      for (let j = 0; j < consensusNext.length; j++) {
        if (usedNext.has(j)) continue;
        const ni = consensusNext[j];
        const ratio = ni.count / numModels;
        // Non-linear: single-model faint, full-agreement vivid
        const alpha = 0.08 + 0.82 * ratio * ratio;
        const fill = `rgba(${baseRgb[0]}, ${baseRgb[1]}, ${baseRgb[2]}, ${alpha.toFixed(2)})`;
        const midDist = (dist0 + dist1) / 2;
        const midAlt = (ni.baseFt + ni.topFt) / 2;
        drawSmoothBand(ctx, [
          { distance: midDist, base: midAlt, top: midAlt },
          { distance: dist1, base: ni.baseFt, top: ni.topFt },
        ], transform, fill);
      }
    }

    // Draw per-model outlines on top
    if (showOutlines) {
      const modelColors = theme.compareModelColors;
      for (let m = 0; m < this.datasets.length; m++) {
        const ds = this.datasets[m];
        const color = modelColors[m % modelColors.length];
        const zones = ds.data.points.map((pt) => getZonesForLayer(layerId, pt));

        // For each zone track, draw top and bottom boundary lines
        this.renderModelOutlines(ctx, transform, ds.data, zones, color);
      }
    }
  }

  /** Draw zone boundary outlines for a single model. */
  private renderModelOutlines(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
    data: VizRouteData,
    zonesPerPoint: { baseFt: number; topFt: number; severity: string }[][],
    color: string,
  ): void {
    // For each pair of adjacent points, match zones and draw top/base outlines
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = zonesPerPoint[i];
      const next = zonesPerPoint[i + 1] ?? [];
      const dist0 = data.points[i].distanceNm;
      const dist1 = data.points[i + 1].distanceNm;
      const usedNext = new Set<number>();

      for (const z of curr) {
        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < next.length; j++) {
          if (usedNext.has(j)) continue;
          const overlap = Math.min(z.topFt, next[j].topFt) - Math.max(z.baseFt, next[j].baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const nz = next[bestIdx];
          // Top boundary
          drawSmoothLine(ctx, [
            { distance: dist0, value: z.topFt },
            { distance: dist1, value: nz.topFt },
          ], transform, { color, width: 2 });
          // Base boundary
          drawSmoothLine(ctx, [
            { distance: dist0, value: z.baseFt },
            { distance: dist1, value: nz.baseFt },
          ], transform, { color, width: 2 });
        } else {
          // Taper to midpoint
          const midDist = (dist0 + dist1) / 2;
          const midAlt = (z.baseFt + z.topFt) / 2;
          drawSmoothLine(ctx, [
            { distance: dist0, value: z.topFt },
            { distance: midDist, value: midAlt },
          ], transform, { color, width: 2 });
          drawSmoothLine(ctx, [
            { distance: dist0, value: z.baseFt },
            { distance: midDist, value: midAlt },
          ], transform, { color, width: 2 });
        }
      }

      // Unmatched next zones
      for (let j = 0; j < next.length; j++) {
        if (usedNext.has(j)) continue;
        const nz = next[j];
        const midDist = (dist0 + dist1) / 2;
        const midAlt = (nz.baseFt + nz.topFt) / 2;
        drawSmoothLine(ctx, [
          { distance: midDist, value: midAlt },
          { distance: dist1, value: nz.topFt },
        ], transform, { color, width: 2 });
        drawSmoothLine(ctx, [
          { distance: midDist, value: midAlt },
          { distance: dist1, value: nz.baseFt },
        ], transform, { color, width: 2 });
      }
    }
  }

  /** Get the base RGB color for a layer from the theme. */
  private getLayerBaseRgb(
    layerId: string,
    theme: ReturnType<typeof getActiveTheme>,
  ): [number, number, number] {
    // Extract RGB from the theme's rgba strings for each layer type
    switch (layerId) {
      case 'icing-bands':
      case 'icing-ogimet-nwp-bands':
        return parseRgbaToRgb(theme.icing.moderate) ?? [255, 165, 0];
      case 'sfip-bands':
        return parseRgbaToRgb(theme.sfipIcing.moderate) ?? [255, 165, 0];
      case 'cat-bands':
        return parseRgbaToRgb(theme.cat.moderate) ?? [255, 152, 0];
      case 'square-cloud-bands':
      case 'square-nwp-cloud-bands':
        return theme.nwpClouds.brightRgb;
      case 'inversion-bands':
        return theme.inversion.baseRgb;
      case 'thermo-convective-bg':
      case 'nwp-convective-bg':
        return parseRgbaToRgb(theme.convective.riskColors.moderate) ?? [255, 152, 0];
      default:
        return [100, 149, 237]; // fallback cornflower blue
    }
  }

  private renderLineEnvelope(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
  ): void {
    if (!this.compareLayer || !this.compareLayer.lineAccessor) return;

    const accessor = this.compareLayer.lineAccessor;
    const { style, fillColor } = this.resolveLineStyle(this.compareLayer);

    // Use first dataset's points as the distance reference
    const refPoints = this.datasets[0].data.points;

    // For each route point, compute min/max/mean across all models
    const bandPoints: BandPointData[] = [];
    const meanPoints: PointData[] = [];

    for (let i = 0; i < refPoints.length; i++) {
      const distance = refPoints[i].distanceNm;
      let min = Infinity;
      let max = -Infinity;
      let sum = 0;
      let count = 0;

      for (const dataset of this.datasets) {
        if (i >= dataset.data.points.length) continue;
        const val = accessor(dataset.data.points[i]);
        if (val !== null) {
          min = Math.min(min, val);
          max = Math.max(max, val);
          sum += val;
          count++;
        }
      }

      if (count > 0) {
        bandPoints.push({ distance, base: min, top: max });
        meanPoints.push({ distance, value: sum / count });
      } else {
        bandPoints.push({ distance, base: null, top: null });
        meanPoints.push({ distance, value: null });
      }
    }

    // Draw filled envelope (min→max)
    drawSmoothBand(ctx, bandPoints, transform, fillColor);

    // Draw mean line
    drawSmoothLine(ctx, meanPoints, transform, style);
  }

  /** Resolve line style from theme when possible, falling back to static definition. */
  private resolveLineStyle(layer: ComparableLayer): {
    style: { color: string; width: number; dash?: number[] };
    fillColor: string;
  } {
    const theme = getActiveTheme();
    const themeMap: Record<string, { style: { color: string; width: number; dash?: number[] }; fillColor: string }> = {
      'freezing-level': { style: theme.temperature.freezingLevel, fillColor: `${theme.temperature.freezingLevel.color}26` },
      'minus-10c-level': { style: theme.temperature.minus10c, fillColor: `${theme.temperature.minus10c.color}20` },
      'minus-20c-level': { style: theme.temperature.minus20c, fillColor: `${theme.temperature.minus20c.color}20` },
      'lcl-line': { style: theme.stability.lcl, fillColor: `${theme.stability.lcl.color}20` },
      'lfc-line': { style: theme.stability.lfc, fillColor: `${theme.stability.lfc.color}20` },
      'el-line': { style: theme.stability.el, fillColor: `${theme.stability.el.color}20` },
    };
    return themeMap[layer.id] ?? {
      style: layer.lineStyle ?? { color: '#2563eb', width: 2 },
      fillColor: layer.envelopeFill ?? 'rgba(37, 99, 235, 0.15)',
    };
  }
}

// ---------------------------------------------------------------------------
// Consensus interval helpers
// ---------------------------------------------------------------------------

interface ConsensusInterval {
  baseFt: number;
  topFt: number;
  /** Number of models with a zone overlapping this interval. */
  count: number;
}

/** Build consensus intervals from zones across N models at a single route point.
 *
 *  Uses a sweep-line approach: collect all zone start/end altitudes as events,
 *  sort them, and walk through to compute per-interval model counts. */
function buildConsensusIntervals(
  zonesPerModel: { baseFt: number; topFt: number }[][],
  _numModels: number,
): ConsensusInterval[] {
  // Collect altitude events: +1 at baseFt, -1 at topFt for each zone
  const events: { alt: number; delta: number }[] = [];
  for (const zones of zonesPerModel) {
    for (const z of zones) {
      if (z.topFt <= z.baseFt) continue;
      events.push({ alt: z.baseFt, delta: +1 });
      events.push({ alt: z.topFt, delta: -1 });
    }
  }
  if (events.length === 0) return [];

  // Sort by altitude, with +1 before -1 at same altitude (so intervals aren't lost)
  events.sort((a, b) => a.alt - b.alt || b.delta - a.delta);

  const result: ConsensusInterval[] = [];
  let count = 0;
  let prevAlt = events[0].alt;

  for (const ev of events) {
    if (ev.alt > prevAlt && count > 0) {
      result.push({ baseFt: prevAlt, topFt: ev.alt, count });
    }
    count += ev.delta;
    prevAlt = ev.alt;
  }

  return result;
}

/** Parse an rgba() CSS string to extract [r, g, b]. */
function parseRgbaToRgb(rgba: string): [number, number, number] | null {
  const m = rgba.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
}
