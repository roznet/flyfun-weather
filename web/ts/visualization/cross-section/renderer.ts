/** Main cross-section canvas renderer with coordinate transform. */

import type { CoordTransform, VizRouteData, CrossSectionLayer } from '../types';
import type { ResolvedAdvisoryFocus } from '../advisory-focus';
import {
  crossSectionPaintPlan,
  focusRegionsForPrimitiveKind,
  renderCrossSectionFocus,
} from '../advisory-focus';
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
  private advisoryFocus: ResolvedAdvisoryFocus | null = null;
  private emphasizedLayerIds: ReadonlySet<string> | null = null;
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

  setAdvisoryFocus(focus: ResolvedAdvisoryFocus | null): void {
    this.advisoryFocus = focus;
  }

  setLayerEmphasis(
    layerIds: ReadonlySet<string> | readonly string[] | null,
  ): void {
    this.emphasizedLayerIds = layerIds === null ? null : new Set(layerIds);
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

    const advisoryFocus = this.advisoryFocus?.active.highlightSurfaces.includes('cross-section')
      ? this.advisoryFocus
      : null;
    const bandRegions = advisoryFocus
      ? focusRegionsForPrimitiveKind(advisoryFocus.regions, 'band')
      : [];
    const railRegions = advisoryFocus
      ? focusRegionsForPrimitiveKind(advisoryFocus.regions, 'route-rail')
      : [];
    for (const step of crossSectionPaintPlan(this.layers)) {
      if (step.kind === 'layer') {
        this.renderLayer(ctx, transform, step.layer);
        continue;
      }
      if (!advisoryFocus) continue;
      const regions = step.primitiveKind === 'band' ? bandRegions : railRegions;
      if (regions.length === 0) continue;
      renderCrossSectionFocus(ctx, transform, { ...advisoryFocus, regions });
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

  private renderLayer(
    ctx: CanvasRenderingContext2D,
    transform: CoordTransform,
    layer: CrossSectionLayer,
  ): void {
    if (!this.data || this.enabledLayers[layer.id] === false) return;
    ctx.save();
    try {
      if (this.emphasizedLayerIds && !this.emphasizedLayerIds.has(layer.id)) {
        ctx.globalAlpha = 0.22;
      }
      ctx.beginPath();
      ctx.rect(
        transform.plotArea.left,
        transform.plotArea.top,
        transform.plotArea.width,
        transform.plotArea.height,
      );
      ctx.clip();
      layer.render(ctx, transform, this.data);
    } finally {
      ctx.restore();
    }
  }
}
