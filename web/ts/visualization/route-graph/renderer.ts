/** Route graph canvas renderer — 2D chart below the cross-section. */

import type { PlotArea, VizRouteData, VizPoint } from '../types';
import type { ResolvedAdvisoryFocus } from '../advisory-focus';
import type { RouteGraphMetric } from './metrics';
import { renderRouteGraphFocus } from '../advisory-focus';
import type { YAxisScale } from './axes';
import { computeYScale, drawLeftYAxis, drawRightYAxis, drawXGrid, drawZeroLine, drawBorder } from './axes';
import { monotoneCubicTangents } from '../cross-section/layers/base';
import { MARGIN } from './constants';
import { cssVar, isDarkTheme } from '../interaction-utils';

export class RouteGraphRenderer {
  private container: HTMLElement;
  private mainCanvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private resizeObserver: ResizeObserver;
  private data: VizRouteData | null = null;
  private leftMetric: RouteGraphMetric | null = null;
  private rightMetric: RouteGraphMetric | null = null;
  private advisoryFocus: ResolvedAdvisoryFocus | null = null;
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

    window.addEventListener('theme-changed', () => this.render());
  }

  setData(data: VizRouteData): void {
    this.data = data;
  }

  setMetrics(left: RouteGraphMetric | null, right: RouteGraphMetric | null): void {
    this.leftMetric = left;
    this.rightMetric = right;
  }

  setAdvisoryFocus(focus: ResolvedAdvisoryFocus | null): void {
    this.advisoryFocus = focus;
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

    // Plot background
    ctx.fillStyle = cssVar('--bg', '#f8f9fa');
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

    if (this.advisoryFocus?.active.highlightSurfaces.includes('route-graph')) {
      renderRouteGraphFocus(ctx, plotArea, distanceToX, this.advisoryFocus);
    }

    // Draw zero lines
    if (this.leftMetric?.showZeroLine && leftScale) drawZeroLine(ctx, leftScale, plotArea, this.leftMetric.zeroLineLabels);
    if (this.rightMetric?.showZeroLine && rightScale) drawZeroLine(ctx, rightScale, plotArea, this.rightMetric.zeroLineLabels);

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
      ctx.strokeStyle = isDarkTheme() ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)';
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
    points: readonly VizPoint[],
  ): void {
    const values = points.map((p) => metric.getValue(p));
    const allDots: Array<{ x: number; y: number }> = [];

    ctx.strokeStyle = metric.color;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';

    // Collect segments of consecutive non-null values
    const segments: Array<{ xs: number[]; ys: number[] }> = [];
    let curXs: number[] = [];
    let curYs: number[] = [];

    for (let i = 0; i < points.length; i++) {
      const v = values[i];
      if (v === null) {
        if (curXs.length > 0) {
          segments.push({ xs: curXs, ys: curYs });
          curXs = [];
          curYs = [];
        }
        continue;
      }
      const x = distanceToX(points[i].distanceNm);
      const y = scale.valueToY(v);
      curXs.push(x);
      curYs.push(y);
      allDots.push({ x, y });
    }
    if (curXs.length > 0) segments.push({ xs: curXs, ys: curYs });

    // Draw each segment as a smooth monotone cubic spline
    for (const seg of segments) {
      if (seg.xs.length < 2) {
        // Single point — just a dot (drawn below)
        continue;
      }
      const tangents = monotoneCubicTangents(seg.xs, seg.ys);
      ctx.beginPath();
      ctx.moveTo(seg.xs[0], seg.ys[0]);
      for (let i = 0; i < seg.xs.length - 1; i++) {
        const dx = seg.xs[i + 1] - seg.xs[i];
        ctx.bezierCurveTo(
          seg.xs[i] + dx / 3, seg.ys[i] + tangents[i] * dx / 3,
          seg.xs[i + 1] - dx / 3, seg.ys[i + 1] - tangents[i + 1] * dx / 3,
          seg.xs[i + 1], seg.ys[i + 1],
        );
      }
      ctx.stroke();
    }

    // Draw data point dots
    ctx.fillStyle = metric.color;
    for (const { x, y } of allDots) {
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
    points: readonly VizPoint[],
  ): void {
    const zeroY = scale.valueToY(0);
    ctx.save();
    ctx.fillStyle = metric.color;
    ctx.globalAlpha = 0.6;

    for (let i = 0; i < points.length; i++) {
      const v = metric.getValue(points[i]);
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

    ctx.restore();
  }

  private setupCanvas(canvas: HTMLCanvasElement, cssW: number, cssH: number, dpr: number): void {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
}
