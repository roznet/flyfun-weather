/**
 * Skew-T compare renderer — multi-model T/Td curves on one diagram.
 *
 * Follows the same dual-canvas pattern as SkewTRenderer but:
 * - No overlay bands (clouds, icing, inversions)
 * - Draws T/Td for each model with distinct colors
 * - Optional CAPE/CIN and level markers (toggleable, default off)
 * - Side panel shows per-model lines with unified range
 */

import { SkewTTransform } from './skewt-transform';
import { BackgroundLinesRenderer } from './background-lines';
import { renderCompareProfileCurves, type CompareProfileDataset } from './profile-curves';
import { renderAxes, renderLevelMarkers, renderIndicesPanel } from './axes';
import { renderCompareSidePanel, getVariableById, SIDE_PANEL_WIDTH, type CompareSidePanelDataset } from './variable-panel';
import { SoundingProfileData, DEFAULT_CONFIG, SkewTConfig, PlotArea } from './types';

// Layout constants — same as SkewTRenderer
const MARGIN_LEFT = 40;
const MARGIN_RIGHT = 6;
const MARGIN_TOP = 24;
const MARGIN_BOTTOM = 44;
const PANEL_GAP = 8;
const FL_LABEL_WIDTH = 40;

export interface CompareModelDataset {
  model: string;
  data: SoundingProfileData;
  color: string;
  isPrimary: boolean;
}

export class SkewTCompareRenderer {
  private container: HTMLElement;
  private canvas: HTMLCanvasElement;
  private overlayCanvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private datasets: CompareModelDataset[] = [];
  private config: SkewTConfig = DEFAULT_CONFIG;
  private backgroundLines = new BackgroundLinesRenderer();
  private primaryVarId: string;
  private secondaryVarId: string | null;
  private showCapeCinState: boolean;
  private showLevelMarkersState: boolean;
  private lastTransform: SkewTTransform | null = null;
  private resizeObserver: ResizeObserver;
  private themeListener: (() => void) | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    const panels = loadCompareSidePanels();
    this.primaryVarId = panels.primary;
    this.secondaryVarId = panels.secondary;
    this.showCapeCinState = loadToggle(CAPE_CIN_KEY, false);
    this.showLevelMarkersState = loadToggle(LEVEL_MARKERS_KEY, false);

    if (getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }

    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
    container.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d')!;

    this.overlayCanvas = document.createElement('canvas');
    this.overlayCanvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
    container.appendChild(this.overlayCanvas);

    this.resizeObserver = new ResizeObserver(() => {
      this.backgroundLines.invalidate();
      this.render();
    });
    this.resizeObserver.observe(container);

    this.themeListener = () => {
      this.backgroundLines.invalidate();
      this.render();
    };
    window.addEventListener('theme-changed', this.themeListener);
  }

  /** Set multi-model data and render. */
  setModelData(datasets: CompareModelDataset[]): void {
    this.datasets = datasets;
    this.render();
  }

  /** Clear data and show placeholder. */
  clear(): void {
    this.datasets = [];
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

    if (this.datasets.length === 0) {
      this.renderPlaceholder(ctx, cssW, cssH);
      return;
    }

    // Layout
    const hasSidePanel = !!this.primaryVarId;
    const sidePanelTotalW = hasSidePanel ? FL_LABEL_WIDTH + SIDE_PANEL_WIDTH + PANEL_GAP : FL_LABEL_WIDTH;
    const skewtRight = cssW - MARGIN_RIGHT - sidePanelTotalW;

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
    this.lastTransform = transform;

    // Sky background
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.fillStyle = dark ? '#1a2a40' : '#e8f0ff';
    ctx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

    // 1. Background lines (cached)
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.backgroundLines.render(ctx, transform, this.config, this.canvas.width, this.canvas.height, dpr);
    ctx.restore();

    // 2. No overlay bands in compare mode

    // 3. Optional level markers from all models
    if (this.showLevelMarkersState) {
      for (const ds of this.datasets) {
        renderLevelMarkers(ctx, transform, ds.data);
      }
    }

    // 4. Multi-model profile curves
    const profileDatasets: CompareProfileDataset[] = this.datasets.map(ds => ({
      model: ds.model,
      levels: ds.data.levels,
      color: ds.color,
      isPrimary: ds.isPrimary,
      parcelPath: ds.data.parcel_path,
      lclP: ds.data.indices?.lcl_pressure_hpa as number | null ?? null,
      elP: ds.data.indices?.el_pressure_hpa as number | null ?? null,
    }));
    renderCompareProfileCurves(ctx, transform, profileDatasets, this.showCapeCinState);

    // 5. Axes
    renderAxes(ctx, transform);

    // 6. Indices panel from primary
    if (this.showCapeCinState || this.showLevelMarkersState) {
      const primary = this.datasets.find(d => d.isPrimary);
      if (primary) {
        renderIndicesPanel(ctx, transform, primary.data);
      }
    }

    // 7. Side panel with per-model lines
    if (hasSidePanel) {
      const primaryVar = getVariableById(this.primaryVarId);
      const secondaryVar = this.secondaryVarId ? getVariableById(this.secondaryVarId) : null;
      if (primaryVar) {
        const panelDatasets: CompareSidePanelDataset[] = this.datasets.map(ds => ({
          model: ds.model,
          levels: ds.data.levels,
          color: ds.color,
          isPrimary: ds.isPrimary,
        }));
        const primaryData = this.datasets.find(d => d.isPrimary) ?? this.datasets[0];
        renderCompareSidePanel(ctx, transform, panelDatasets, primaryVar, secondaryVar ?? null, {
          left: skewtRight + FL_LABEL_WIDTH + PANEL_GAP,
          width: SIDE_PANEL_WIDTH,
          top: plotArea.top,
          height: plotArea.height,
          bottom: plotArea.bottom,
        }, primaryData.data.track_deg);
      }
    }

    // 8. Title with model legend
    this.renderTitle(ctx, cssW, plotArea);
  }

  private renderPlaceholder(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.fillStyle = dark ? '#888' : '#999';
    ctx.font = '14px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Click a point on the cross-section to compare models', w / 2, h / 2);
  }

  private renderTitle(ctx: CanvasRenderingContext2D, width: number, plotArea: PlotArea): void {
    const dark = document.documentElement.dataset.theme === 'dark';
    ctx.font = 'bold 12px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';

    const primary = this.datasets.find(d => d.isPrimary);
    const label = primary?.data.label || `Point ${primary?.data.point_index ?? '?'}`;

    // Title
    ctx.fillStyle = dark ? '#ddd' : '#333';
    ctx.fillText(`${label} — Compare`, MARGIN_LEFT, 4);

    // Model legend (colored dots + names)
    ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
    let x = MARGIN_LEFT + ctx.measureText(`${label} — Compare`).width + 16;
    for (const ds of this.datasets) {
      // Dot
      ctx.fillStyle = ds.color;
      ctx.beginPath();
      ctx.arc(x + 4, 10, 4, 0, Math.PI * 2);
      ctx.fill();
      // Label
      x += 12;
      ctx.fillStyle = dark ? '#ccc' : '#555';
      const modelText = ds.model.toUpperCase() + (ds.isPrimary ? ' \u2605' : '');
      ctx.fillText(modelText, x, 4);
      x += ctx.measureText(modelText).width + 10;
    }

    // Legend: solid = T, dashed = Td
    ctx.fillStyle = dark ? '#888' : '#999';
    ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('solid = T, dashed = Td', width - MARGIN_RIGHT - 4, 6);
  }

  toggleCapeCin(): void {
    this.showCapeCinState = !this.showCapeCinState;
    saveToggle(CAPE_CIN_KEY, this.showCapeCinState);
    this.render();
  }

  toggleLevelMarkers(): void {
    this.showLevelMarkersState = !this.showLevelMarkersState;
    saveToggle(LEVEL_MARKERS_KEY, this.showLevelMarkersState);
    this.render();
  }

  getShowCapeCin(): boolean { return this.showCapeCinState; }
  getShowLevelMarkers(): boolean { return this.showLevelMarkersState; }

  getPrimaryVar(): string { return this.primaryVarId; }
  getSecondaryVar(): string | null { return this.secondaryVarId; }

  setPrimaryVar(id: string): void {
    this.primaryVarId = id;
    saveCompareSidePanels({ primary: id, secondary: this.secondaryVarId });
    this.backgroundLines.invalidate();
    this.render();
  }

  setSecondaryVar(id: string | null): void {
    this.secondaryVarId = id;
    saveCompareSidePanels({ primary: this.primaryVarId, secondary: id });
    this.backgroundLines.invalidate();
    this.render();
  }

  getOverlayCanvas(): HTMLCanvasElement { return this.overlayCanvas; }
  getTransform(): SkewTTransform | null { return this.lastTransform; }
  getDatasets(): CompareModelDataset[] { return this.datasets; }

  destroy(): void {
    this.resizeObserver.disconnect();
    if (this.themeListener) {
      window.removeEventListener('theme-changed', this.themeListener);
    }
    this.canvas.remove();
    this.overlayCanvas.remove();
  }
}

// --- Persistence ---

const CAPE_CIN_KEY = 'wb_skewtCompareCapeCin';
const LEVEL_MARKERS_KEY = 'wb_skewtCompareLevelMarkers';
const SIDE_PANELS_KEY = 'wb_skewtCompareSidePanels';

function loadToggle(key: string, defaultVal: boolean): boolean {
  try {
    const saved = localStorage.getItem(key);
    if (saved !== null) return JSON.parse(saved);
  } catch { /* ignore */ }
  return defaultVal;
}

function saveToggle(key: string, value: boolean): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

interface SidePanelSelection {
  primary: string;
  secondary: string | null;
}

function loadCompareSidePanels(): SidePanelSelection {
  try {
    const saved = localStorage.getItem(SIDE_PANELS_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed.primary) return parsed;
    }
  } catch { /* ignore */ }
  return { primary: 'headwind', secondary: null };
}

function saveCompareSidePanels(sel: SidePanelSelection): void {
  try { localStorage.setItem(SIDE_PANELS_KEY, JSON.stringify(sel)); } catch { /* ignore */ }
}
