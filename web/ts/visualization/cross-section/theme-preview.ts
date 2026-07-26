/** Theme preview: a sample cross-section scene plus a decoded key.
 *
 * Two jobs, deliberately split so neither crowds the other:
 *
 *  - The **scene** (canvas) answers "what will my chart look like in this
 *    theme" — every colour family drawn together, in plausible vertical
 *    order, at the real opacities, over the theme's sky and terrain. It
 *    carries no text: labels crammed into a 500px canvas were what made the
 *    old preview unreadable.
 *  - The **key** (DOM) answers "which colour means what" — one row per value,
 *    grouped by family, with lines in their own block so a dashed stroke is
 *    never mistaken for a filled band.
 *
 * Both sides read the *previewed* theme (not necessarily the active one) and
 * pull every colour through the same `scales.ts` / `layer-legends.ts`
 * functions the renderers use, so the preview cannot drift from the chart.
 */

import { THEMES, type ThemeId, type CrossSectionTheme, type LineStyle } from './theme';
import { showPopupContent } from '../../components/info-popup';
import {
  drawNaturalCloudBand,
  DEFAULT_NATURAL_CONFIG,
  type CloudStyle,
} from './layers/cloud-bands-factory';
import { referenceLineStyles } from './layers/reference-lines';
import { getLayerLegend, type LegendEntry } from '../layer-legends';
import { legendChipsHtml } from '../legend-render';
import { cloudFillFromDD, nwpCloudFill, icingRiskColor, catRiskColor, inversionOpacity } from '../scales';
import { escapeHtml } from '../../utils';
import { t } from '../../i18n/i18n';

const SCENE_W = 560;
const SCENE_H = 250;

export interface ThemePreviewOptions {
  /** Cloud rendering the user currently has enabled. The scene and the cloud
   *  swatches both follow it, so previewing with Square clouds selected shows
   *  square cells rather than the natural puffs the old preview always drew. */
  cloudStyle?: CloudStyle;
}

export function showThemePreview(themeId: ThemeId, opts: ThemePreviewOptions = {}): void {
  const theme = THEMES[themeId];
  if (!theme) return;
  const cloudStyle: CloudStyle = opts.cloudStyle ?? 'natural';

  showPopupContent(previewHtml(theme, cloudStyle));

  // showPopupContent takes HTML, and HTML serialization drops canvas pixels —
  // so the markup carries an empty canvas that we paint once it is in the DOM.
  const canvas = document.querySelector<HTMLCanvasElement>('#theme-preview-scene');
  if (canvas) paintScene(canvas, theme, cloudStyle);
}

function previewHtml(theme: CrossSectionTheme, cloudStyle: CloudStyle): string {
  return `<div class="theme-preview">
    <h3>${escapeHtml(theme.label)} — ${escapeHtml(t('viz.themePreviewTitle'))}</h3>
    <canvas id="theme-preview-scene" class="theme-preview-scene"
            width="${SCENE_W * 2}" height="${SCENE_H * 2}"
            style="width:${SCENE_W}px;height:${SCENE_H}px"></canvas>
    <p class="theme-preview-caption">${escapeHtml(t('viz.themePreviewCaption'))}</p>
    ${legendHtml(theme, cloudStyle)}
  </div>`;
}

// --- Key (DOM) -------------------------------------------------------------

/** One key section per colour family, sourced from the shared layer legends
 *  so the preview and the per-layer info popups always agree. */
function legendHtml(theme: CrossSectionTheme, cloudStyle: CloudStyle): string {
  const sky = theme.sky.background;
  const nwpCloudId = { natural: 'nwp-cloud-bands', soft: 'soft-nwp-cloud-bands', square: 'square-nwp-cloud-bands' }[cloudStyle];
  const ddCloudId = { natural: 'cloud-bands', soft: 'soft-cloud-bands', square: 'square-cloud-bands' }[cloudStyle];
  const styleName = t(`viz.cloudStyle.${cloudStyle}`);

  const section = (title: string, entries: LegendEntry[] | null): string => {
    if (!entries || entries.length === 0) return '';
    return `<section class="theme-preview-group">
      <h4>${escapeHtml(title)}</h4>
      <div class="theme-preview-chips">${legendChipsHtml(entries, sky)}</div>
    </section>`;
  };

  const bands = [
    section(`${t('viz.group.clouds')} — NWP (${styleName})`, getLayerLegend(nwpCloudId, theme)),
    section(`${t('viz.group.clouds')} — DD (${styleName})`, getLayerLegend(ddCloudId, theme)),
    section(t('viz.group.icing'), getLayerLegend('icing-bands', theme)),
    section(t('viz.group.turbulence'), getLayerLegend('cat-bands', theme)),
    section(t('viz.group.convection'), getLayerLegend('nwp-convective-bg', theme)),
    section(t('viz.group.stability'), getLayerLegend('inversion-bands', theme)),
    section(t('viz.group.obscuration'), getLayerLegend('surface-obscuration-bands', theme)),
    section(t('viz.group.sun'), getLayerLegend('night-shading', theme)),
  ].join('');

  // Lines get their own full-width block: each sample carries its own colour,
  // width and dash next to its own label, so −10°C and −20°C stay separable
  // even in themes that give them the same hue.
  const lineIds = ['freezing-level', 'minus-10c', 'minus-20c', 'lcl', 'lfc', 'el', 'cruise-altitude'];
  const lineEntries = lineIds.flatMap((id) => getLayerLegend(id, theme) ?? []);

  return `<div class="theme-preview-legend">
    ${bands}
    <section class="theme-preview-group theme-preview-group--lines">
      <h4>${escapeHtml(t('viz.group.lines'))}</h4>
      <div class="theme-preview-chips">${legendChipsHtml(lineEntries, sky)}</div>
    </section>
  </div>`;
}

// --- Scene (canvas) --------------------------------------------------------

/** Vertical layout of the sample scene, as fractions of plot height (0 = top).
 *  Families are stacked in the order the real chart draws them and given
 *  disjoint x-ranges where they would otherwise pile up, so nothing in the
 *  preview overlaps by accident. */
const SCENE = {
  ceilingLine: 0.07,
  nwpCloud: { top: 0.11, bot: 0.25, x0: 0.00, x1: 1.00 },
  minus20Line: 0.29,
  cat: { top: 0.32, bot: 0.42, x0: 0.00, x1: 0.60 },
  elLine: { y: 0.35, x0: 0.00, x1: 0.50 },
  minus10Line: 0.45,
  cruiseLine: 0.50,
  freezingLine: 0.55,
  icing: { top: 0.555, bot: 0.65, x0: 0.00, x1: 0.60 },
  ddCloud: { top: 0.68, bot: 0.78, x0: 0.00, x1: 0.60 },
  lfcLine: { y: 0.71, x0: 0.00, x1: 0.50 },
  inversion: { top: 0.79, bot: 0.845, x0: 0.00, x1: 0.42 },
  lclLine: { y: 0.865, x0: 0.00, x1: 0.35 },
  tower: { top: 0.28, x0: 0.72, x1: 0.90 },
  terrainMaxFrac: 0.14,
} as const;

function paintScene(canvas: HTMLCanvasElement, theme: CrossSectionTheme, cloudStyle: CloudStyle): void {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(2, 0, 0, 2, 0, 0);
  ctx.clearRect(0, 0, SCENE_W, SCENE_H);

  const L = 0, T = 0, W = SCENE_W, H = SCENE_H;
  const x = (f: number) => L + W * f;
  const y = (f: number) => T + H * f;

  // Everything is clipped to the frame. The old preview skipped this, and the
  // natural cloud blobs (radius up to ~45px) smeared outside the border.
  ctx.save();
  ctx.beginPath();
  ctx.rect(L, T, W, H);
  ctx.clip();

  ctx.fillStyle = theme.sky.background;
  ctx.fillRect(L, T, W, H);

  ctx.strokeStyle = theme.axes.gridColor;
  ctx.lineWidth = 0.5;
  for (let i = 1; i < 5; i++) {
    const gy = y(i / 5);
    ctx.beginPath();
    ctx.moveTo(L, gy);
    ctx.lineTo(L + W, gy);
    ctx.stroke();
  }

  ctx.strokeStyle = theme.axes.waypointLineColor;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x(0.5), T);
  ctx.lineTo(x(0.5), T + H);
  ctx.stroke();
  ctx.setLineDash([]);

  // Clouds — NWP cover ramp on top, DD deck below, both in the user's style.
  const nwp = SCENE.nwpCloud;
  drawCloudRow(ctx, cloudStyle, x(nwp.x0), x(nwp.x1), y(nwp.top), y(nwp.bot),
    [25, 50, 80].map((pct) => ({
      color: nwpCloudFill(pct, theme),
      fillFraction: Math.max(DEFAULT_NATURAL_CONFIG.minFillFraction, Math.min(1, pct / 100)),
    })), theme, 7);

  const dd = SCENE.ddCloud;
  drawCloudRow(ctx, cloudStyle, x(dd.x0), x(dd.x1), y(dd.top), y(dd.bot),
    ([['sct', 2.5], ['bkn', 1.5], ['ovc', 0.5]] as const).map(([cov, ddC]) => ({
      color: cloudFillFromDD(ddC, cov, theme),
      fillFraction: Math.max(
        DEFAULT_NATURAL_CONFIG.minFillFraction,
        DEFAULT_NATURAL_CONFIG.fillFraction[cov.toUpperCase() as 'SCT' | 'BKN' | 'OVC'],
      ),
    })), theme, 31);

  // Risk bands — light → severe left to right.
  const risks = ['light', 'moderate', 'severe'] as const;
  drawSegmentedBand(ctx, x(SCENE.icing.x0), x(SCENE.icing.x1), y(SCENE.icing.top), y(SCENE.icing.bot),
    risks.map((r) => icingRiskColor(r, theme)));
  drawSegmentedBand(ctx, x(SCENE.cat.x0), x(SCENE.cat.x1), y(SCENE.cat.top), y(SCENE.cat.bot),
    risks.map((r) => catRiskColor(r, theme)));

  // Inversion at a representative 2°C strength.
  const [ir, ig, ib] = theme.inversion.baseRgb;
  ctx.fillStyle = `rgba(${ir}, ${ig}, ${ib}, ${inversionOpacity(2, theme)})`;
  ctx.fillRect(x(SCENE.inversion.x0), y(SCENE.inversion.top),
    x(SCENE.inversion.x1) - x(SCENE.inversion.x0),
    y(SCENE.inversion.bot) - y(SCENE.inversion.top));

  const terrainAt = makeTerrain(L, W, T + H, H * SCENE.terrainMaxFrac);

  drawTower(ctx, theme, x(SCENE.tower.x0), x(SCENE.tower.x1), y(SCENE.tower.top), terrainAt);

  // Lines last (drawn over the bands, as on the chart).
  const ref = referenceLineStyles(theme);
  drawLine(ctx, ref.ceiling, L, L + W, y(SCENE.ceilingLine));
  drawLine(ctx, theme.temperature.minus20c, L, L + W, y(SCENE.minus20Line));
  drawLine(ctx, theme.stability.el, x(SCENE.elLine.x0), x(SCENE.elLine.x1), y(SCENE.elLine.y));
  drawLine(ctx, theme.temperature.minus10c, L, L + W, y(SCENE.minus10Line));
  drawLine(ctx, ref.cruise, L, L + W, y(SCENE.cruiseLine));
  drawLine(ctx, theme.temperature.freezingLevel, L, L + W, y(SCENE.freezingLine));
  drawLine(ctx, theme.stability.lfc, x(SCENE.lfcLine.x0), x(SCENE.lfcLine.x1), y(SCENE.lfcLine.y));
  drawLine(ctx, theme.stability.lcl, x(SCENE.lclLine.x0), x(SCENE.lclLine.x1), y(SCENE.lclLine.y));

  drawTerrain(ctx, theme, L, W, T + H, terrainAt);

  ctx.restore();

  ctx.strokeStyle = theme.axes.gridColor;
  ctx.lineWidth = 1;
  ctx.strokeRect(L + 0.5, T + 0.5, W - 1, H - 1);
}

/** Blob overhang allowed past a natural cloud row's edges, in px. Sized from
 *  the factory's nominal blob radius so a blob near the row edge fades out
 *  under its own gradient instead of being sliced off by the clip. */
const NATURAL_OVERHANG_PX = DEFAULT_NATURAL_CONFIG.blobRadiusXPx;

/** Draw a cloud row as N side-by-side cells, each in the user's cloud style.
 *
 *  The row is clipped **once**, generously — the point of the clip is to stop
 *  clouds bleeding into a neighbouring family (or outside the frame), not to
 *  square off the deck. Cells are not clipped against each other, so natural
 *  blobs blend across the cell boundary into one continuous deck whose colour
 *  steps with coverage. */
function drawCloudRow(
  ctx: CanvasRenderingContext2D,
  style: CloudStyle,
  xL: number, xR: number, yTop: number, yBot: number,
  cells: Array<{ color: string; fillFraction: number }>,
  theme: CrossSectionTheme,
  seed: number,
): void {
  const cellW = (xR - xL) / cells.length;
  const drawCells = () => {
    cells.forEach((cell, i) => {
      const x0 = xL + cellW * i;
      const x1 = x0 + cellW;
      if (style === 'square') {
        ctx.fillStyle = cell.color;
        ctx.fillRect(x0, yTop, cellW, yBot - yTop);
      } else if (style === 'soft') {
        paintSoftCell(ctx, theme, x0, x1, yTop, yBot, cell.color);
      } else {
        drawNaturalCloudBand(ctx, x0, x1, () => yBot, () => yTop, cell.color, cell.fillFraction, seed + i * 17);
      }
    });
  };

  if (style !== 'natural') {
    drawCells();
    return;
  }

  const vMargin = (yBot - yTop) * 0.45;
  ctx.save();
  ctx.beginPath();
  ctx.rect(xL - NATURAL_OVERHANG_PX, yTop - vMargin,
    (xR - xL) + NATURAL_OVERHANG_PX * 2, (yBot - yTop) + vMargin * 2);
  ctx.clip();
  drawCells();
  ctx.restore();
}

/** Vertical feathered fill, mirroring `paintSoft` in the cloud factory. */
function paintSoftCell(
  ctx: CanvasRenderingContext2D,
  theme: CrossSectionTheme,
  x0: number, x1: number, yTop: number, yBot: number,
  color: string,
): void {
  const h = yBot - yTop;
  if (h <= 0) return;
  const feather = theme.softClouds?.featherFraction ?? 0.15;
  const featherPx = Math.max(2, h * feather);
  const grad = ctx.createLinearGradient(0, yTop, 0, yBot);
  const transparent = color.replace(/rgba?\(([^)]+)\)/, (_m, inner: string) => {
    const [r, g, b] = inner.split(',').map((s) => s.trim());
    return `rgba(${r}, ${g}, ${b}, 0)`;
  });
  grad.addColorStop(0, transparent);
  grad.addColorStop(Math.min(featherPx / h, 0.3), color);
  grad.addColorStop(Math.max(1 - featherPx / h, 0.7), color);
  grad.addColorStop(1, transparent);
  ctx.fillStyle = grad;
  ctx.fillRect(x0, yTop, x1 - x0, h);
}

function drawSegmentedBand(
  ctx: CanvasRenderingContext2D,
  xL: number, xR: number, yTop: number, yBot: number,
  colors: string[],
): void {
  const cellW = (xR - xL) / colors.length;
  colors.forEach((color, i) => {
    if (!color || color === 'transparent') return;
    ctx.fillStyle = color;
    ctx.fillRect(xL + cellW * i, yTop, cellW, yBot - yTop);
  });
}

/** A moderate-risk convective tower: fill, hatching, anvil strip, edge box —
 *  the same four elements `thermo-convective-bg.ts` draws. */
function drawTower(
  ctx: CanvasRenderingContext2D,
  theme: CrossSectionTheme,
  x0: number, x1: number, yTop: number,
  terrainAt: (x: number) => number,
): void {
  const risk = 'moderate';
  const w = x1 - x0;
  const yBase = Math.max(terrainAt(x0), terrainAt(x1));
  const h = yBase - yTop;
  if (h <= 0) return;

  ctx.fillStyle = theme.convective.towerFill[risk] ?? 'transparent';
  ctx.fillRect(x0, yTop, w, h);

  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, yTop, w, h);
  ctx.clip();
  ctx.strokeStyle = theme.convective.hatchColor[risk] ?? 'transparent';
  ctx.lineWidth = 1;
  for (let off = -h; off < w + h; off += 8) {
    ctx.beginPath();
    ctx.moveTo(x0 + off, yTop + h);
    ctx.lineTo(x0 + off + h, yTop);
    ctx.stroke();
  }
  ctx.restore();

  ctx.fillStyle = theme.convective.stripColor[risk] ?? 'transparent';
  ctx.fillRect(x0 - 5, yTop, w + 10, 5);

  ctx.strokeStyle = theme.convective.edgeColor[risk] ?? 'transparent';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(x0, yTop, w, h);
}

function makeTerrain(L: number, W: number, baseY: number, maxH: number): (x: number) => number {
  return (x: number) => {
    const f = W > 0 ? (x - L) / W : 0;
    // Two offset sines: a broad ridge with a smaller shoulder, so the profile
    // reads as terrain rather than a single symmetric bump.
    const elev = maxH * (0.35 + 0.45 * Math.sin(f * Math.PI) + 0.2 * Math.sin(f * Math.PI * 3));
    return baseY - Math.max(0, elev);
  };
}

function drawTerrain(
  ctx: CanvasRenderingContext2D,
  theme: CrossSectionTheme,
  L: number, W: number, baseY: number,
  terrainAt: (x: number) => number,
): void {
  ctx.beginPath();
  ctx.moveTo(L, baseY);
  for (let px = 0; px <= W; px += 2) ctx.lineTo(L + px, terrainAt(L + px));
  ctx.lineTo(L + W, baseY);
  ctx.closePath();
  ctx.fillStyle = theme.terrain.fillColor;
  ctx.fill();

  ctx.beginPath();
  for (let px = 0; px <= W; px += 2) {
    const ty = terrainAt(L + px);
    if (px === 0) ctx.moveTo(L + px, ty);
    else ctx.lineTo(L + px, ty);
  }
  ctx.strokeStyle = theme.terrain.outlineColor;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function drawLine(
  ctx: CanvasRenderingContext2D,
  style: LineStyle,
  x1: number, x2: number, y: number,
): void {
  ctx.strokeStyle = style.color;
  ctx.lineWidth = style.width;
  ctx.setLineDash(style.dash ?? []);
  ctx.beginPath();
  ctx.moveTo(x1, y);
  ctx.lineTo(x2, y);
  ctx.stroke();
  ctx.setLineDash([]);
}
