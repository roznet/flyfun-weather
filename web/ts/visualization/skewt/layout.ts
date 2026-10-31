/**
 * Plot-box layout shared by SkewTRenderer and SkewTCompareRenderer (#962).
 *
 * The plot box and temperature window come from `fitSkewT` (45° isotherms,
 * fixed skew shift): wide containers keep the classic −60…+40 layout, narrow
 * ones show a window centred on the sounding and shrink the container height
 * when the sounding needs it. The container's original CSS height is the
 * ceiling, recorded once on the element (both renderers share the container).
 */

import { fitSkewT, skewedExtent } from './skewt-transform';
import type { PlotArea, SkewTConfig, SoundingProfileData } from './types';

export interface SkewTMargins {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface SkewTLayout {
  plotArea: PlotArea;
  /** Base config with the fitted temperature window. */
  config: SkewTConfig;
  /** Container height (CSS px) the layout wants. */
  cssH: number;
}

const MAX_HEIGHT_ATTR = 'skewtMaxHeight';

/** The container's height ceiling: its original height, recorded on first use. */
export function containerMaxHeight(container: HTMLElement): number {
  const saved = Number(container.dataset[MAX_HEIGHT_ATTR]);
  if (saved > 0) return saved;
  const h = parseFloat(container.style.height) || container.getBoundingClientRect().height;
  if (h > 0) container.dataset[MAX_HEIGHT_ATTR] = String(h);
  return h;
}

/** Skewed extent of T, Td and the parcel path over every sounding given. */
export function soundingsExtent(soundings: SoundingProfileData[], config: SkewTConfig): [number, number] | null {
  const points: { t: number | null; p: number }[] = [];
  for (const s of soundings) {
    for (const l of s.levels) {
      points.push({ t: l.temperature_c, p: l.pressure_hpa });
      points.push({ t: l.dewpoint_c, p: l.pressure_hpa });
    }
    for (const pp of s.parcel_path ?? []) {
      points.push({ t: pp.temperature_c, p: pp.pressure_hpa });
    }
  }
  return skewedExtent(points, config);
}

/**
 * Lay out the plot in a `cssW`-wide container whose height may go up to
 * `maxCssH`. `rightReserve` is the width kept right of the plot (FL labels +
 * side panel). The [plot + reserve] block is centred when the plot narrows.
 */
export function layoutSkewT(
  cssW: number,
  maxCssH: number,
  margins: SkewTMargins,
  rightReserve: number,
  extent: [number, number] | null,
  baseConfig: SkewTConfig,
): SkewTLayout {
  const availW = cssW - margins.left - margins.right - rightReserve;
  const availH = maxCssH - margins.top - margins.bottom;
  const fit = fitSkewT(availW, availH, extent, baseConfig);
  const left = margins.left + Math.max(0, (availW - fit.width) / 2);
  return {
    plotArea: {
      left,
      top: margins.top,
      width: fit.width,
      height: fit.height,
      right: left + fit.width,
      bottom: margins.top + fit.height,
    },
    config: { ...baseConfig, tMin: fit.tMin, tMax: fit.tMax },
    cssH: fit.height + margins.top + margins.bottom,
  };
}

const SIDE_PANEL_VISIBLE_KEY = 'wb_skewtSidePanelVisible';

/** Whether the side panel is shown (one setting for single + compare). */
export function loadSidePanelVisible(): boolean {
  try { return localStorage.getItem(SIDE_PANEL_VISIBLE_KEY) !== '0'; } catch { return true; }
}

export function saveSidePanelVisible(visible: boolean): void {
  try { localStorage.setItem(SIDE_PANEL_VISIBLE_KEY, visible ? '1' : '0'); } catch { /* ignore */ }
}
