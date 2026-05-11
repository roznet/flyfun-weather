/** Theme preview: renders a fake cross-section scene showing all color families. */

import { THEMES, type ThemeId, type CrossSectionTheme } from './theme';
import { showPopupContent } from '../../components/info-popup';
import { buildPuffPath, hash01, DEFAULT_NATURAL_CONFIG } from './layers/cloud-bands-factory';

const W = 520;
const H = 320;
const PAD = 40;

export function showThemePreview(themeId: ThemeId): void {
  const theme = THEMES[themeId];
  if (!theme) return;

  const canvas = document.createElement('canvas');
  canvas.width = W * 2;
  canvas.height = H * 2;
  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;

  const ctx = canvas.getContext('2d')!;
  ctx.scale(2, 2);
  renderPreview(ctx, theme);

  const wrapper = document.createElement('div');
  wrapper.innerHTML = `<h3 style="margin:0 0 8px">${theme.label} Theme Preview</h3>`;
  wrapper.appendChild(canvas);

  showPopupContent(wrapper.outerHTML);

  // After popup is shown, replace the placeholder with actual canvas
  const popup = document.getElementById('metric-info-popup');
  if (popup) {
    const placeholder = popup.querySelector('canvas');
    if (placeholder) {
      // The HTML serialization loses the canvas content, re-render
      const newCanvas = document.createElement('canvas');
      newCanvas.width = W * 2;
      newCanvas.height = H * 2;
      newCanvas.style.width = `${W}px`;
      newCanvas.style.height = `${H}px`;
      const newCtx = newCanvas.getContext('2d')!;
      newCtx.scale(2, 2);
      renderPreview(newCtx, theme);
      placeholder.replaceWith(newCanvas);
    }
  }
}

function renderPreview(ctx: CanvasRenderingContext2D, theme: CrossSectionTheme): void {
  const plotL = PAD;
  const plotT = 20;
  const plotW = W - PAD * 2;
  const plotH = H - 50;
  const bg = theme.sky.background;

  // Sky background
  ctx.fillStyle = theme.sky.background;
  ctx.fillRect(plotL, plotT, plotW, plotH);

  // Grid lines
  ctx.strokeStyle = theme.axes.gridColor;
  ctx.lineWidth = 0.5;
  for (let i = 1; i < 5; i++) {
    const y = plotT + (plotH / 5) * i;
    ctx.beginPath();
    ctx.moveTo(plotL, y);
    ctx.lineTo(plotL + plotW, y);
    ctx.stroke();
  }

  // Waypoint line
  const wpX = plotL + plotW * 0.5;
  ctx.strokeStyle = theme.axes.waypointLineColor;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(wpX, plotT);
  ctx.lineTo(wpX, plotT + plotH);
  ctx.stroke();
  ctx.setLineDash([]);

  // NWP cloud band (top area) — rendered with the natural puff style so the
  // preview matches what the cross-section actually draws.
  const nwp = theme.nwpClouds;
  const nwpPcts = [25, 50, 75];
  const nwpLabels = ['25%', '50%', '75%'];
  for (let i = 0; i < 3; i++) {
    const pct = nwpPcts[i];
    const t = pct / 100;
    const [nbr, nbg, nbb] = nwp.brightRgb;
    const [ndr, ndg, ndb] = nwp.deltaRgb;
    const r = Math.round(nbr - ndr * t);
    const g = Math.round(nbg - ndg * t);
    const b = Math.round(nbb - ndb * t);
    const [opFloor, opScale] = nwp.opacityRange;
    const a = Math.min(opFloor + opScale, opFloor + opScale * t);
    const cloudColor = `rgba(${r}, ${g}, ${b}, ${a.toFixed(2)})`;
    const bandW = plotW / 3;
    const bx = plotL + bandW * i;
    drawPreviewPuffStrip(ctx, bx, plotT + 10, bandW, 35, t, cloudColor, i * 17);
    drawLabel(ctx, nwpLabels[i], bx + bandW / 2, plotT + 30, bg);
  }
  drawLabel(ctx, 'NWP Clouds', plotL + plotW / 2, plotT + 5, bg);

  // DD cloud band — same natural puff rendering.
  const cloudY = plotT + 50;
  const ddCoverages: Array<keyof typeof DEFAULT_NATURAL_CONFIG.fillFraction> = ['OVC', 'BKN', 'SCT'];
  const ddLabels = ['OVC', 'BKN', 'SCT'];
  const ddValues = [0, 1.5, 3]; // dewpoint depression
  for (let i = 0; i < 3; i++) {
    const dd = ddValues[i];
    const ct = Math.min(1, dd / 3);
    const [ddr, ddg, ddb] = theme.clouds.denseRgb;
    const [ttr, ttg, ttb] = theme.clouds.thinRgb;
    const r = Math.round(ddr + (ttr - ddr) * ct);
    const g = Math.round(ddg + (ttg - ddg) * ct);
    const b = Math.round(ddb + (ttb - ddb) * ct);
    const cloudColor = `rgba(${r}, ${g}, ${b}, ${(0.7 - 0.2 * ct).toFixed(2)})`;
    const bandW = plotW / 3;
    const bx = plotL + bandW * i;
    const fillFrac = DEFAULT_NATURAL_CONFIG.fillFraction[ddCoverages[i]];
    drawPreviewPuffStrip(ctx, bx, cloudY, bandW, 25, fillFrac, cloudColor, i * 23 + 11);
    drawLabel(ctx, ddLabels[i], bx + bandW / 2, cloudY + 14, bg);
  }
  drawLabel(ctx, 'DD Clouds', plotL + plotW / 2, cloudY - 4, bg);

  // Icing bands
  const icingY = cloudY + 30;
  const risks = ['light', 'moderate', 'severe'];
  const bandW = plotW / 3;
  for (let i = 0; i < risks.length; i++) {
    ctx.fillStyle = theme.icing[risks[i]] ?? 'transparent';
    ctx.fillRect(plotL + bandW * i, icingY, bandW, 22);
  }
  drawLabel(ctx, 'Icing', plotL + plotW / 2, icingY + 13, bg);

  // CAT bands
  const catY = icingY + 25;
  for (let i = 0; i < risks.length; i++) {
    ctx.fillStyle = theme.cat[risks[i]] ?? 'transparent';
    ctx.fillRect(plotL + bandW * i, catY, bandW, 22);
  }
  drawLabel(ctx, 'CAT', plotL + plotW / 2, catY + 13, bg);

  // Convective tower
  const towerX = plotL + plotW * 0.75;
  const towerW = plotW * 0.15;
  const towerTop = catY + 28;
  const towerH = 50;
  ctx.fillStyle = theme.convective.towerFill['moderate'] ?? 'rgba(255,152,0,0.25)';
  ctx.fillRect(towerX, towerTop, towerW, towerH);
  ctx.fillStyle = theme.convective.stripColor['moderate'] ?? 'rgba(255,152,0,0.75)';
  ctx.fillRect(towerX - 4, towerTop, towerW + 8, 5);
  // Hatching
  ctx.save();
  ctx.beginPath();
  ctx.rect(towerX, towerTop, towerW, towerH);
  ctx.clip();
  ctx.strokeStyle = theme.convective.hatchColor['moderate'] ?? 'rgba(200,100,0,0.35)';
  ctx.lineWidth = 1;
  for (let off = -towerH; off < towerW + towerH; off += 8) {
    ctx.beginPath();
    ctx.moveTo(towerX + off, towerTop + towerH);
    ctx.lineTo(towerX + off + towerH, towerTop);
    ctx.stroke();
  }
  ctx.restore();
  ctx.strokeStyle = theme.convective.edgeColor['moderate'] ?? 'rgba(200,100,0,0.5)';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(towerX, towerTop, towerW, towerH);
  drawLabel(ctx, 'TCU', towerX + towerW / 2, towerTop + towerH / 2, bg);

  // Inversion band
  const invY = catY + 28;
  const [ir, ig, ib] = theme.inversion.baseRgb;
  const { floor, scale, cap } = theme.inversion.opacityParams;
  const invOpacity = Math.min(cap, floor + scale * 0.7);
  ctx.fillStyle = `rgba(${ir}, ${ig}, ${ib}, ${invOpacity})`;
  ctx.fillRect(plotL, invY, plotW * 0.6, 20);
  drawLabel(ctx, 'Inversion', plotL + plotW * 0.3, invY + 12, bg);

  // Temperature lines
  const tempY = plotT + plotH * 0.35;
  drawThemeLine(ctx, theme.temperature.freezingLevel, plotL, plotL + plotW, tempY);
  drawThemeLine(ctx, theme.temperature.minus10c, plotL, plotL + plotW, tempY - 18);
  drawThemeLine(ctx, theme.temperature.minus20c, plotL, plotL + plotW, tempY - 36);
  drawLabel(ctx, '0°C / -10°C / -20°C', plotL + plotW * 0.25, tempY - 44, bg);

  // Stability lines
  const stabY = plotT + plotH * 0.65;
  drawThemeLine(ctx, theme.stability.lcl, plotL, plotL + plotW * 0.5, stabY);
  drawThemeLine(ctx, theme.stability.lfc, plotL, plotL + plotW * 0.5, stabY - 15);
  drawThemeLine(ctx, theme.stability.el, plotL, plotL + plotW * 0.5, stabY - 30);
  drawLabel(ctx, 'LCL / LFC / EL', plotL + plotW * 0.25, stabY - 38, bg);

  // Reference lines
  const refY = plotT + plotH * 0.82;
  ctx.strokeStyle = theme.reference.cruiseColor;
  ctx.lineWidth = 2.5;
  ctx.setLineDash([8, 4]);
  ctx.beginPath();
  ctx.moveTo(plotL, refY);
  ctx.lineTo(plotL + plotW, refY);
  ctx.stroke();
  ctx.strokeStyle = theme.reference.ceilingColor;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(plotL, refY + 12);
  ctx.lineTo(plotL + plotW, refY + 12);
  ctx.stroke();
  ctx.setLineDash([]);
  drawLabel(ctx, 'Cruise / Ceiling', plotL + plotW / 2, refY + 7, bg);

  // Terrain (sine wave hill)
  const terrainBaseY = plotT + plotH;
  ctx.fillStyle = theme.terrain.fillColor;
  ctx.beginPath();
  ctx.moveTo(plotL, terrainBaseY);
  for (let x = 0; x <= plotW; x += 2) {
    const elev = 15 + 20 * Math.sin((x / plotW) * Math.PI);
    ctx.lineTo(plotL + x, terrainBaseY - elev);
  }
  ctx.lineTo(plotL + plotW, terrainBaseY);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = theme.terrain.outlineColor;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let x = 0; x <= plotW; x += 2) {
    const elev = 15 + 20 * Math.sin((x / plotW) * Math.PI);
    if (x === 0) ctx.moveTo(plotL + x, terrainBaseY - elev);
    else ctx.lineTo(plotL + x, terrainBaseY - elev);
  }
  ctx.stroke();

  // Plot border
  ctx.strokeStyle = '#adb5bd';
  ctx.lineWidth = 1;
  ctx.strokeRect(plotL, plotT, plotW, plotH);
}

function drawThemeLine(
  ctx: CanvasRenderingContext2D,
  style: { color: string; width: number; dash?: number[] },
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

/** Render the natural-puff cloud rendering inside a rectangular strip so the
 *  preview matches what the cross-section actually draws. `fillFraction`
 *  drives the puff/gap pattern (1.0 = continuous blanket, 0.45 ≈ SCT, etc.).
 *  `seed` makes the puff phases stable for the preview. */
function drawPreviewPuffStrip(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number,
  fillFraction: number,
  cloudColor: string,
  seed: number,
): void {
  const config = DEFAULT_NATURAL_CONFIG;
  const baseAt = () => y + h;  // flat bottom of strip
  const topAt  = () => y;       // flat top of strip
  ctx.save();
  ctx.fillStyle = cloudColor;

  // Continuous blanket for OVC or narrow strips.
  if (fillFraction >= 0.99 || w < config.minBandWidthPx) {
    buildPuffPath(ctx, x, x + w, baseAt, topAt, seed, config);
    ctx.fill();
    ctx.restore();
    return;
  }

  // Discrete puff slots: same per-slot hash decision as `paintNatural` so
  // the preview's distribution matches what the cross-section actually draws.
  const slotW = config.puffWidthPx;
  const nSlots = Math.ceil(w / slotW);
  for (let s = 0; s < nSlots; s++) {
    if (hash01(s * 0x1f1f1f + seed) > fillFraction) continue;
    const xa = x + s * slotW;
    const xb = Math.min(x + w, xa + slotW);
    if (xb - xa < 2) continue;
    buildPuffPath(ctx, xa, xb, baseAt, topAt, seed ^ s, config);
    ctx.fill();
  }
  ctx.restore();
}

function hexLuminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

function drawLabel(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, bgColor: string): void {
  ctx.font = '9px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const m = ctx.measureText(text);
  ctx.fillStyle = bgColor + 'dd';
  ctx.fillRect(x - m.width / 2 - 2, y - 6, m.width + 4, 12);
  ctx.fillStyle = hexLuminance(bgColor) < 0.5 ? '#ddd' : '#333';
  ctx.fillText(text, x, y);
}
