/** Compare cross-section renderer: one layer across N models. */

import type { CoordTransform, PlotArea, VizRouteData, CrossSectionLayer } from '../types';
import type { ComparableLayer } from './compare-layers';
import { drawAxes } from './axes';
import { terrainFillLayer } from './layers/terrain-fill';
import { cruiseAltitudeLayer } from './layers/reference-lines';
import { getAllLayers } from './layer-registry';
import { drawSmoothBand, drawSmoothLine, type BandPointData, type PointData } from './layers/base';
import { getActiveTheme } from './theme';

const MARGIN = { left: 60, right: 50, top: 20, bottom: 50 };

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
    const data = this.datasets[0].data;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return null;

    const plotArea: PlotArea = {
      left: MARGIN.left,
      top: MARGIN.top,
      width: cssW - MARGIN.left - MARGIN.right,
      height: cssH - MARGIN.top - MARGIN.bottom,
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

  render(): void {
    if (this.datasets.length === 0) return;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return;

    const dpr = window.devicePixelRatio || 1;
    this.setupCanvas(this.mainCanvas, cssW, cssH, dpr);
    this.setupCanvas(this.overlayCanvas, cssW, cssH, dpr);
    this.setupCanvas(this.offscreenCanvas, cssW, cssH, dpr);

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
        this.renderBandLayer(ctx, transform, cssW, cssH, dpr);
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
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const ctx = this.overlayCanvas.getContext('2d')!;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const transform = this.createTransform();
    if (!transform || this.datasets.length === 0) { ctx.restore(); return; }

    const { plotArea } = transform;
    const data = this.datasets[0].data;

    const theme = getActiveTheme();

    // Selected point indicator
    if (this.selectedPointIndex >= 0 && this.selectedPointIndex < data.points.length) {
      const pt = data.points[this.selectedPointIndex];
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

  destroy(): void {
    this.resizeObserver.disconnect();
    this.mainCanvas.remove();
    this.overlayCanvas.remove();
  }

  private renderBandLayer(
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
    const alpha = Math.min(0.85, Math.max(0.15, 1.5 / numModels));

    const offCtx = this.offscreenCanvas.getContext('2d')!;

    const { plotArea } = transform;

    for (const dataset of this.datasets) {
      // Clear offscreen to transparent — each layer's semi-transparent fills
      // will composite against the opaque sky on the main canvas via
      // source-over blending at reduced alpha.
      offCtx.save();
      offCtx.scale(dpr, dpr);
      offCtx.clearRect(0, 0, cssW, cssH);

      // Clip offscreen to plot area
      offCtx.beginPath();
      offCtx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
      offCtx.clip();

      // Render the layer at full opacity on offscreen
      crossSectionLayer.render(offCtx, transform, dataset.data);
      offCtx.restore();

      // Composite offscreen onto main canvas with source-over at reduced
      // alpha so all models are visible and blend naturally on any background.
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.drawImage(this.offscreenCanvas, 0, 0, cssW * dpr, cssH * dpr, 0, 0, cssW, cssH);
      ctx.restore();
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

  private setupCanvas(canvas: HTMLCanvasElement, cssW: number, cssH: number, dpr: number): void {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
}
