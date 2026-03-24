/** Main cross-section canvas renderer with coordinate transform. */

import type { CoordTransform, VizRouteData, CrossSectionLayer } from '../types';
import { drawAxes } from './axes';
import { getActiveTheme } from './theme';
import { createCoordTransform, renderCrosshairOverlay, setupCanvasDpr } from './renderer-utils';

export class CrossSectionRenderer {
  private container: HTMLElement;
  private mainCanvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private resizeObserver: ResizeObserver;
  private data: VizRouteData | null = null;
  private layers: CrossSectionLayer[] = [];
  private enabledLayers: Record<string, boolean> = {};
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

  setLayers(layers: CrossSectionLayer[], enabled: Record<string, boolean>): void {
    this.layers = layers;
    this.enabledLayers = enabled;
  }

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.renderOverlay();
  }

  getCanvas(): HTMLCanvasElement {
    return this.overlayCanvas;
  }

  isLayerEnabled(id: string): boolean {
    return this.enabledLayers[id] !== false;
  }

  /** Create a coordinate transform for the current canvas size and data. */
  createTransform(): CoordTransform | null {
    if (!this.data) return null;
    return createCoordTransform(this.container, this.data);
  }

  render(): void {
    if (!this.data) return;
    const cssW = this.container.clientWidth;
    const cssH = this.container.clientHeight;
    if (cssW === 0 || cssH === 0) return;

    const dpr = window.devicePixelRatio || 1;
    setupCanvasDpr(this.mainCanvas, cssW, cssH, dpr);
    setupCanvasDpr(this.overlayCanvas, cssW, cssH, dpr);

    const ctx = this.mainCanvas.getContext('2d')!;
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    const transform = this.createTransform();
    if (!transform) { ctx.restore(); return; }

    // Sky-blue plot background
    const { plotArea } = transform;
    ctx.fillStyle = getActiveTheme().sky.background;
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
        layer.render(ctx, transform, this.data);
        ctx.restore();
      }
    }

    ctx.restore();
    this.renderOverlay();
  }

  renderOverlay(hoverX?: number, hoverY?: number): void {
    renderCrosshairOverlay(
      this.overlayCanvas, this.container,
      this.createTransform(), this.data?.points ?? null,
      this.selectedPointIndex, hoverX, hoverY,
    );
  }

  destroy(): void {
    this.resizeObserver.disconnect();
    this.mainCanvas.remove();
    this.overlayCanvas.remove();
  }
}
