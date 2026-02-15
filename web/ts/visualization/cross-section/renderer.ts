/** Main cross-section canvas renderer with coordinate transform. */

import type { CoordTransform, PlotArea, RenderMode, VizRouteData, CrossSectionLayer } from '../types';
import { drawAxes } from './axes';

const MARGIN = { left: 60, right: 50, top: 20, bottom: 50 };

export class CrossSectionRenderer {
  private container: HTMLElement;
  private mainCanvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private resizeObserver: ResizeObserver;
  private data: VizRouteData | null = null;
  private layers: CrossSectionLayer[] = [];
  private enabledLayers: Record<string, boolean> = {};
  private renderMode: RenderMode = 'smooth';
  private selectedPointIndex = -1;

  constructor(container: HTMLElement) {
    this.container = container;

    this.mainCanvas = document.createElement('canvas');
    this.mainCanvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
    container.appendChild(this.mainCanvas);

    this.overlayCanvas = document.createElement('canvas');
    this.overlayCanvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
    container.appendChild(this.overlayCanvas);

    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(container);
  }

  setData(data: VizRouteData): void {
    this.data = data;
  }

  setLayers(layers: CrossSectionLayer[], enabled: Record<string, boolean>): void {
    this.layers = layers;
    this.enabledLayers = enabled;
  }

  setRenderMode(mode: RenderMode): void {
    this.renderMode = mode;
  }

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.renderOverlay();
  }

  getCanvas(): HTMLCanvasElement {
    return this.overlayCanvas;
  }

  /** Create a coordinate transform for the current canvas size and data. */
  createTransform(): CoordTransform | null {
    if (!this.data) return null;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return null;

    const plotArea: PlotArea = {
      left: MARGIN.left,
      top: MARGIN.top,
      width: cssW - MARGIN.left - MARGIN.right,
      height: cssH - MARGIN.top - MARGIN.bottom,
    };

    const maxDist = this.data.totalDistanceNm;
    const maxAlt = this.data.flightCeilingFt;

    return {
      distanceToX: (d: number) => plotArea.left + (d / maxDist) * plotArea.width,
      altitudeToY: (a: number) => plotArea.top + (1 - a / maxAlt) * plotArea.height,
      xToDistance: (x: number) => ((x - plotArea.left) / plotArea.width) * maxDist,
      yToAltitude: (y: number) => (1 - (y - plotArea.top) / plotArea.height) * maxAlt,
      plotArea,
    };
  }

  render(): void {
    if (!this.data) return;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return;

    const dpr = window.devicePixelRatio || 1;
    this.setupCanvas(this.mainCanvas, cssW, cssH, dpr);
    this.setupCanvas(this.overlayCanvas, cssW, cssH, dpr);

    const ctx = this.mainCanvas.getContext('2d')!;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const transform = this.createTransform();
    if (!transform) { ctx.restore(); return; }

    // Sky-blue plot background
    const { plotArea } = transform;
    ctx.fillStyle = '#b5d4e8';
    ctx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

    // Draw axes first (background grid)
    drawAxes(ctx, transform, this.data);

    // Draw layers in order
    for (const layer of this.layers) {
      if (this.enabledLayers[layer.id] !== false) {
        ctx.save();
        // Clip to plot area
        ctx.beginPath();
        ctx.rect(
          transform.plotArea.left,
          transform.plotArea.top,
          transform.plotArea.width,
          transform.plotArea.height,
        );
        ctx.clip();
        layer.render(ctx, transform, this.data, this.renderMode);
        ctx.restore();
      }
    }

    ctx.restore();
    this.renderOverlay();
  }

  renderOverlay(hoverX?: number): void {
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const ctx = this.overlayCanvas.getContext('2d')!;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const transform = this.createTransform();
    if (!transform || !this.data) { ctx.restore(); return; }

    const { plotArea } = transform;

    // Draw selected point indicator
    if (this.selectedPointIndex >= 0 && this.selectedPointIndex < this.data.points.length) {
      const pt = this.data.points[this.selectedPointIndex];
      const x = transform.distanceToX(pt.distanceNm);
      ctx.strokeStyle = 'rgba(37, 99, 235, 0.6)';
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(x, plotArea.top);
      ctx.lineTo(x, plotArea.top + plotArea.height);
      ctx.stroke();
    }

    // Draw hover crosshair
    if (hoverX !== undefined && hoverX >= plotArea.left && hoverX <= plotArea.left + plotArea.width) {
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.3)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hoverX, plotArea.top);
      ctx.lineTo(hoverX, plotArea.top + plotArea.height);
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

  private setupCanvas(canvas: HTMLCanvasElement, cssW: number, cssH: number, dpr: number): void {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
}
