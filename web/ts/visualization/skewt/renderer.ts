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
import { renderOverlayBands, getDefaultOverlayState } from './overlay-bands';
import { renderSidePanel, getVariableById, SIDE_PANEL_WIDTH, type VariableDef } from './variable-panel';
import { SoundingProfileData, DEFAULT_CONFIG, SkewTConfig, PlotArea } from './types';

// Layout constants
const MARGIN_LEFT = 40;
const MARGIN_RIGHT = 6;
const MARGIN_TOP = 24;
const MARGIN_BOTTOM = 44;
const PANEL_GAP = 8;
const FL_LABEL_WIDTH = 40; // space between plot right edge and side panel for FL labels
// Frozen plot-box aspect (width / height). The skew transform ties both the
// 45° isotherm density and the temperature scale to the plot width, so an
// unconstrained container width stretches the sounding horizontally. Clamping
// the box to a fixed ratio keeps the diagram's proportions identical across
// layouts; a wider container adds centred whitespace instead of distortion.
// 1.8 matches the classic 960px layout's max aspect, so that layout is
// unchanged while the wider sidebar layout is brought back to the same look.
const PLOT_ASPECT = 1.8;

export class SkewTRenderer {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private data: SoundingProfileData | null = null;
  private config: SkewTConfig = DEFAULT_CONFIG;
  private backgroundLines = new BackgroundLinesRenderer();
  private overlayState: Record<string, boolean>;
  private primaryVarId: string;
  private secondaryVarId: string | null;
  private lastTransform: SkewTTransform | null = null;
  private resizeObserver: ResizeObserver;
  private themeListener: (() => void) | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.overlayState = loadOverlayState();
    const panels = loadSidePanels();
    this.primaryVarId = panels.primary;
    this.secondaryVarId = panels.secondary;

    // Ensure container is positioned for absolute children
    if (getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }

    // Create main canvas
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d')!;

    // Create overlay canvas (for hover crosshair — pointer events handled by interaction module)
    this.overlayCanvas = document.createElement('canvas');
    this.overlayCanvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
    container.appendChild(this.overlayCanvas);

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

    // Compute layout: Skew-T plot area + FL labels gap + side panel.
    // The plot box is clamped to PLOT_ASPECT so the sounding keeps its
    // proportions regardless of container width; the [plot + side panel] block
    // is centred, so excess width becomes whitespace rather than stretch.
    const hasSidePanel = !!this.primaryVarId;
    const sidePanelTotalW = hasSidePanel ? FL_LABEL_WIDTH + SIDE_PANEL_WIDTH + PANEL_GAP : FL_LABEL_WIDTH;

    const plotHeight = cssH - MARGIN_TOP - MARGIN_BOTTOM;
    const availW = cssW - MARGIN_LEFT - MARGIN_RIGHT - sidePanelTotalW;
    const plotWidth = Math.min(availW, plotHeight * PLOT_ASPECT);
    const left = MARGIN_LEFT + Math.max(0, (availW - plotWidth) / 2);
    const skewtRight = left + plotWidth;

    const plotArea: PlotArea = {
      left,
      top: MARGIN_TOP,
      width: plotWidth,
      height: plotHeight,
      right: skewtRight,
      bottom: cssH - MARGIN_BOTTOM,
    };

    if (plotArea.width < 100 || plotArea.height < 100) return;

    const transform = new SkewTTransform(plotArea, this.config);
    this.lastTransform = transform;

    // Sky background
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.fillStyle = dark ? '#1a2a40' : '#e8f0ff';
    ctx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

    // 1. Background lines (cached at full DPR resolution — blit at 1:1 pixels)
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.backgroundLines.render(ctx, transform, this.config, this.canvas.width, this.canvas.height, dpr);
    ctx.restore();

    // 2. Overlay bands (clouds, icing, inversions, convective)
    renderOverlayBands(ctx, transform, this.data, this.overlayState);

    // 3. Level markers (LCL, LFC, EL, freezing, cruise)
    renderLevelMarkers(ctx, transform, this.data);

    // 4. Profile curves + CAPE/CIN shading
    const lclP = this.data.indices?.lcl_pressure_hpa as number | null ?? null;
    const elP = this.data.indices?.el_pressure_hpa as number | null ?? null;
    renderProfileCurves(ctx, transform, this.data.levels, this.data.parcel_path, lclP, elP);

    // 5. Axes and labels
    renderAxes(ctx, transform);

    // 6. Indices panel
    renderIndicesPanel(ctx, transform, this.data);

    // 7. Side panel (single fixed-width, dual-axis) — after FL label gap
    if (hasSidePanel) {
      const primaryVar = getVariableById(this.primaryVarId);
      const secondaryVar = this.secondaryVarId ? getVariableById(this.secondaryVarId) : null;
      if (primaryVar) {
        renderSidePanel(ctx, transform, this.data.levels, primaryVar, secondaryVar ?? null, {
          left: skewtRight + FL_LABEL_WIDTH + PANEL_GAP,
          width: SIDE_PANEL_WIDTH,
          top: plotArea.top,
          height: plotArea.height,
          bottom: plotArea.bottom,
        }, this.data.track_deg);
      }
    }

    // Title
    this.renderTitle(ctx, plotArea.left, this.data);
  }

  private renderPlaceholder(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.fillStyle = dark ? '#888' : '#999';
    ctx.font = '14px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Click a point on the cross-section to view its Skew-T', w / 2, h / 2);
  }

  private renderTitle(ctx: CanvasRenderingContext2D, leftX: number, data: SoundingProfileData): void {
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.font = 'bold 12px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.fillStyle = dark ? '#ddd' : '#333';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    const label = data.label || `Point ${data.point_index}`;
    const model = data.model.toUpperCase();
    ctx.fillText(`${label} — ${model}`, leftX, 4);
  }

  /** Toggle an overlay layer and re-render. */
  toggleOverlay(id: string): void {
    this.overlayState[id] = !this.overlayState[id];
    saveOverlayState(this.overlayState);
    this.render();
  }

  /** Get current overlay enabled state. */
  getOverlayState(): Record<string, boolean> {
    return { ...this.overlayState };
  }

  /**
   * Apply an advisory-preset "lens" to the Skew-T in one shot (#308): a
   * clean-slate overlay map (every id → on/off) and the primary side-panel
   * variable. Persists to the renderer's own localStorage (so a later reload or
   * a manual toggle starts from the lens) and re-renders once. Returns whether
   * anything actually changed, so callers can skip a redundant controls re-render.
   */
  applyPreset(opts: { overlays?: Record<string, boolean>; primaryVar?: string }): boolean {
    let changed = false;
    if (opts.overlays) {
      for (const [id, on] of Object.entries(opts.overlays)) {
        if (this.overlayState[id] !== on) { this.overlayState[id] = on; changed = true; }
      }
      if (changed) saveOverlayState(this.overlayState);
    }
    if (opts.primaryVar && opts.primaryVar !== this.primaryVarId) {
      this.primaryVarId = opts.primaryVar;
      saveSidePanels({ primary: this.primaryVarId, secondary: this.secondaryVarId });
      this.backgroundLines.invalidate();
      changed = true;
    }
    if (changed) this.render();
    return changed;
  }

  /** Get the primary side panel variable ID. */
  getPrimaryVar(): string { return this.primaryVarId; }

  /** Get the secondary side panel variable ID (or null). */
  getSecondaryVar(): string | null { return this.secondaryVarId; }

  /** Set the primary side panel variable and re-render. */
  setPrimaryVar(id: string): void {
    this.primaryVarId = id;
    saveSidePanels({ primary: id, secondary: this.secondaryVarId });
    this.backgroundLines.invalidate();
    this.render();
  }

  /** Set the secondary side panel variable (or null for none) and re-render. */
  setSecondaryVar(id: string | null): void {
    this.secondaryVarId = id;
    saveSidePanels({ primary: this.primaryVarId, secondary: id });
    this.backgroundLines.invalidate();
    this.render();
  }

  /** Get the overlay canvas for interaction attachment. */
  getOverlayCanvas(): HTMLCanvasElement { return this.overlayCanvas; }

  /** Get the current transform (null if not rendered yet). */
  getTransform(): SkewTTransform | null { return this.lastTransform; }

  /** Get the current sounding data. */
  getData(): SoundingProfileData | null { return this.data; }

  /** Clean up resources. */
  destroy(): void {
    this.resizeObserver.disconnect();
    if (this.themeListener) {
      window.removeEventListener('theme-changed', this.themeListener);
    }
    this.canvas.remove();
    this.overlayCanvas.remove();
  }
}

const OVERLAY_STORAGE_KEY = 'wb_skewtOverlays';

function loadOverlayState(): Record<string, boolean> {
  const defaults = getDefaultOverlayState();
  try {
    const saved = localStorage.getItem(OVERLAY_STORAGE_KEY);
    if (saved) return { ...defaults, ...JSON.parse(saved) };
  } catch { /* ignore */ }
  return defaults;
}

function saveOverlayState(state: Record<string, boolean>): void {
  try { localStorage.setItem(OVERLAY_STORAGE_KEY, JSON.stringify(state)); } catch { /* ignore */ }
}

const SIDE_PANELS_KEY = 'wb_skewtSidePanels';

interface SidePanelSelection {
  primary: string;
  secondary: string | null;
}

function loadSidePanels(): SidePanelSelection {
  try {
    const saved = localStorage.getItem(SIDE_PANELS_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed.primary) return parsed;
    }
  } catch { /* ignore */ }
  return { primary: 'headwind', secondary: null };
}

function saveSidePanels(sel: SidePanelSelection): void {
  try { localStorage.setItem(SIDE_PANELS_KEY, JSON.stringify(sel)); } catch { /* ignore */ }
}
