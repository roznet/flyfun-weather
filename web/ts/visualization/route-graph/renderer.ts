/** Route graph canvas renderer — 2D chart below the cross-section. */

import type { PlotArea, VizRouteData } from '../types';
import type { RouteGraphMetric } from './metrics';
import type { YAxisScale } from './axes';
import { computeYScale, drawLeftYAxis, drawRightYAxis, drawXGrid, drawZeroLine, drawBorder } from './axes';

/** Margins match cross-section left/right for X-axis alignment. */
const MARGIN = { left: 60, right: 50, top: 8, bottom: 24 };

export class RouteGraphRenderer {
  private container: HTMLElement;
  private mainCanvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private resizeObserver: ResizeObserver;
  private data: VizRouteData | null = null;
  private leftMetric: RouteGraphMetric | null = null;
  private rightMetric: RouteGraphMetric | null = null;
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

  setMetrics(left: RouteGraphMetric | null, right: RouteGraphMetric | null): void {
    this.leftMetric = left;
    this.rightMetric = right;
  }

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.renderOverlay();
  }

  getCanvas(): HTMLCanvasElement {
    return this.overlayCanvas;
  }

  /** Create the distance-to-X transform matching the cross-section. */
  createDistanceToX(): ((d: number) => number) | null {
    if (!this.data) return null;
    const cssW = this.container.clientWidth;
    if (cssW === 0) return null;
    const plotLeft = MARGIN.left;
    const plotWidth = cssW - MARGIN.left - MARGIN.right;
    const maxDist = this.data.totalDistanceNm;
    return (d: number) => plotLeft + (d / maxDist) * plotWidth;
  }

  /** Get the plot area for external use (hover sync). */
  getPlotArea(): PlotArea | null {
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return null;
    return {
      left: MARGIN.left,
      top: MARGIN.top,
      width: cssW - MARGIN.left - MARGIN.right,
      height: cssH - MARGIN.top - MARGIN.bottom,
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

    const plotArea: PlotArea = {
      left: MARGIN.left,
      top: MARGIN.top,
      width: cssW - MARGIN.left - MARGIN.right,
      height: cssH - MARGIN.top - MARGIN.bottom,
    };

    const maxDist = this.data.totalDistanceNm;
    const distanceToX = (d: number) => plotArea.left + (d / maxDist) * plotArea.width;

    // Light background
    ctx.fillStyle = '#f8f9fa';
    ctx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

    // X grid lines (aligned with cross-section)
    drawXGrid(ctx, this.data, plotArea, distanceToX);

    // Compute scales and render metrics
    const leftScale = this.leftMetric
      ? computeYScale(this.data.points.map((p) => this.leftMetric!.getValue(p)), this.leftMetric, plotArea)
      : null;
    const rightScale = this.rightMetric
      ? computeYScale(this.data.points.map((p) => this.rightMetric!.getValue(p)), this.rightMetric, plotArea)
      : null;

    // Clip to plot area for data rendering
    ctx.save();
    ctx.beginPath();
    ctx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
    ctx.clip();

    // Draw zero lines
    if (this.leftMetric?.showZeroLine && leftScale) drawZeroLine(ctx, leftScale, plotArea);
    if (this.rightMetric?.showZeroLine && rightScale) drawZeroLine(ctx, rightScale, plotArea);

    // Render left metric
    if (this.leftMetric && leftScale) {
      this.renderMetric(ctx, this.leftMetric, leftScale, distanceToX, plotArea);
    }

    // Render right metric
    if (this.rightMetric && rightScale) {
      this.renderMetric(ctx, this.rightMetric, rightScale, distanceToX, plotArea);
    }

    ctx.restore(); // unclip

    // Axes and border
    if (this.leftMetric && leftScale) drawLeftYAxis(ctx, leftScale, this.leftMetric, plotArea);
    if (this.rightMetric && rightScale) drawRightYAxis(ctx, rightScale, this.rightMetric, plotArea);
    drawBorder(ctx, plotArea);

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

    const plotArea = this.getPlotArea();
    if (!plotArea || !this.data) { ctx.restore(); return; }

    const maxDist = this.data.totalDistanceNm;
    const distanceToX = (d: number) => plotArea.left + (d / maxDist) * plotArea.width;

    // Selected point indicator
    if (this.selectedPointIndex >= 0 && this.selectedPointIndex < this.data.points.length) {
      const pt = this.data.points[this.selectedPointIndex];
      const x = distanceToX(pt.distanceNm);
      ctx.strokeStyle = 'rgba(37, 99, 235, 0.6)';
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(x, plotArea.top);
      ctx.lineTo(x, plotArea.top + plotArea.height);
      ctx.stroke();
    }

    // Hover crosshair
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

  // --- Private rendering ---

  private renderMetric(
    ctx: CanvasRenderingContext2D,
    metric: RouteGraphMetric,
    scale: YAxisScale,
    distanceToX: (d: number) => number,
    plotArea: PlotArea,
  ): void {
    if (!this.data) return;
    const points = this.data.points;

    if (metric.renderType === 'bar') {
      this.renderBars(ctx, metric, scale, distanceToX, plotArea, points);
    } else {
      this.renderLine(ctx, metric, scale, distanceToX, points);
    }
  }

  private renderLine(
    ctx: CanvasRenderingContext2D,
    metric: RouteGraphMetric,
    scale: YAxisScale,
    distanceToX: (d: number) => number,
    points: readonly { distanceNm: number }[],
  ): void {
    const values = (points as any[]).map((p) => metric.getValue(p));
    const validPairs: Array<{ x: number; y: number }> = [];

    ctx.strokeStyle = metric.color;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';

    // Draw segments, breaking at nulls
    let inPath = false;
    for (let i = 0; i < points.length; i++) {
      const v = values[i];
      if (v === null) {
        if (inPath) { ctx.stroke(); inPath = false; }
        continue;
      }
      const x = distanceToX(points[i].distanceNm);
      const y = scale.valueToY(v);
      if (!inPath) {
        ctx.beginPath();
        ctx.moveTo(x, y);
        inPath = true;
      } else {
        ctx.lineTo(x, y);
      }
      validPairs.push({ x, y });
    }
    if (inPath) ctx.stroke();

    // Draw data point dots
    ctx.fillStyle = metric.color;
    for (const { x, y } of validPairs) {
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  private renderBars(
    ctx: CanvasRenderingContext2D,
    metric: RouteGraphMetric,
    scale: YAxisScale,
    distanceToX: (d: number) => number,
    plotArea: PlotArea,
    points: readonly { distanceNm: number }[],
  ): void {
    const zeroY = scale.valueToY(0);
    ctx.fillStyle = metric.color;
    ctx.globalAlpha = 0.6;

    for (let i = 0; i < points.length; i++) {
      const v = metric.getValue(points[i] as any);
      if (v === null || v === 0) continue;

      const x = distanceToX(points[i].distanceNm);
      const y = scale.valueToY(v);

      // Bar width: half distance to neighbors
      const prevDist = i > 0 ? points[i - 1].distanceNm : points[i].distanceNm;
      const nextDist = i < points.length - 1 ? points[i + 1].distanceNm : points[i].distanceNm;
      const halfLeft = distanceToX((points[i].distanceNm + prevDist) / 2);
      const halfRight = distanceToX((points[i].distanceNm + nextDist) / 2);
      const barLeft = i === 0 ? x - (halfRight - x) : halfLeft;
      const barRight = i === points.length - 1 ? x + (x - halfLeft) : halfRight;
      const barWidth = barRight - barLeft;

      // Bar from zero line to value
      const barTop = Math.min(y, zeroY);
      const barHeight = Math.abs(y - zeroY);
      ctx.fillRect(barLeft, barTop, barWidth, barHeight);
    }

    ctx.globalAlpha = 1;
  }

  private setupCanvas(canvas: HTMLCanvasElement, cssW: number, cssH: number, dpr: number): void {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
}
