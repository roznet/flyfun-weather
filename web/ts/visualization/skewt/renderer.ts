/**
 * Main Skew-T renderer — orchestrates all rendering layers.
 *
 * Creates and manages two canvases:
 * - Main canvas: background lines, profile curves, CAPE/CIN, overlays
 * - Overlay canvas: hover crosshair (future)
 *
 * Follows the same pattern as CrossSectionRenderer.
 */

import { SkewTTransform } from './skewt-transform';
import { BackgroundLinesRenderer } from './background-lines';
import { renderProfileCurves } from './profile-curves';
import { renderAxes, renderLevelMarkers, renderIndicesPanel } from './axes';
import { renderWindPanel, getWindPanelWidth, WindPanelLayout } from './wind-panel';
import { SoundingProfileData, DEFAULT_CONFIG, SkewTConfig, PlotArea } from './types';

// Layout constants
const MARGIN_LEFT = 40;
const MARGIN_RIGHT = 40;
const MARGIN_TOP = 20;
const MARGIN_BOTTOM = 40;
const WIND_PANEL_GAP = 8;

export class SkewTRenderer {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private data: SoundingProfileData | null = null;
  private config: SkewTConfig = DEFAULT_CONFIG;
  private backgroundLines = new BackgroundLinesRenderer();
  private resizeObserver: ResizeObserver;
  private themeListener: (() => void) | null = null;

  constructor(container: HTMLElement) {
    this.container = container;

    // Create canvas
    this.canvas = document.createElement('canvas');
    this.canvas.style.width = '100%';
    this.canvas.style.height = '100%';
    this.canvas.style.display = 'block';
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d')!;

    // Respond to container resize
    this.resizeObserver = new ResizeObserver(() => {
      this.backgroundLines.invalidate();
      this.render();
    });
    this.resizeObserver.observe(container);

    // Re-render on theme change
    this.themeListener = () => {
      this.backgroundLines.invalidate();
      this.render();
    };
    window.addEventListener('theme-changed', this.themeListener);
  }

  /** Set sounding data and render. */
  setData(data: SoundingProfileData): void {
    this.data = data;
    this.render();
  }

  /** Clear data and show placeholder. */
  clear(): void {
    this.data = null;
    this.render();
  }

  /** Full render pass. */
  render(): void {
    const rect = this.container.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const cssW = rect.width;
    const cssH = rect.height;
    this.canvas.width = cssW * dpr;
    this.canvas.height = cssH * dpr;

    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    if (!this.data) {
      this.renderPlaceholder(ctx, cssW, cssH);
      return;
    }

    // Compute layout: Skew-T plot area + wind panel
    const windPanelWidth = this.data.track_deg !== null ? getWindPanelWidth() : 0;
    const skewtRight = cssW - MARGIN_RIGHT - (windPanelWidth > 0 ? windPanelWidth + WIND_PANEL_GAP : 0);

    const plotArea: PlotArea = {
      left: MARGIN_LEFT,
      top: MARGIN_TOP,
      width: skewtRight - MARGIN_LEFT,
      height: cssH - MARGIN_TOP - MARGIN_BOTTOM,
      right: skewtRight,
      bottom: cssH - MARGIN_BOTTOM,
    };

    if (plotArea.width < 100 || plotArea.height < 100) return;

    const transform = new SkewTTransform(plotArea, this.config);

    // Sky background
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.fillStyle = dark ? '#1a2a40' : '#e8f0ff';
    ctx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

    // 1. Background lines (cached at full DPR resolution — blit at 1:1 pixels)
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.backgroundLines.render(ctx, transform, this.config, this.canvas.width, this.canvas.height, dpr);
    ctx.restore();

    // 2. Level markers (LCL, LFC, EL, freezing, cruise)
    renderLevelMarkers(ctx, transform, this.data);

    // 3. Profile curves + CAPE/CIN shading
    const lclP = this.data.indices?.lcl_pressure_hpa as number | null ?? null;
    const elP = this.data.indices?.el_pressure_hpa as number | null ?? null;
    renderProfileCurves(ctx, transform, this.data.levels, this.data.parcel_path, lclP, elP);

    // 4. Axes and labels
    renderAxes(ctx, transform);

    // 5. Indices panel
    renderIndicesPanel(ctx, transform, this.data);

    // 6. Wind panel
    if (windPanelWidth > 0) {
      const windLayout: WindPanelLayout = {
        left: skewtRight + WIND_PANEL_GAP,
        width: windPanelWidth,
        top: plotArea.top,
        height: plotArea.height,
        bottom: plotArea.bottom,
      };
      renderWindPanel(ctx, transform, this.data.levels, this.data.track_deg, windLayout);
    }

    // Title
    this.renderTitle(ctx, cssW, this.data);
  }

  private renderPlaceholder(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.fillStyle = dark ? '#888' : '#999';
    ctx.font = '14px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Click a point on the cross-section to view its Skew-T', w / 2, h / 2);
  }

  private renderTitle(ctx: CanvasRenderingContext2D, width: number, data: SoundingProfileData): void {
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.font = 'bold 12px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.fillStyle = dark ? '#ddd' : '#333';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    const label = data.label || `Point ${data.point_index}`;
    const model = data.model.toUpperCase();
    ctx.fillText(`${label} — ${model}`, MARGIN_LEFT, 4);
  }

  /** Clean up resources. */
  destroy(): void {
    this.resizeObserver.disconnect();
    if (this.themeListener) {
      window.removeEventListener('theme-changed', this.themeListener);
    }
    this.canvas.remove();
  }
}
