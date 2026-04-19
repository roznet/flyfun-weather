/**
 * Headwind/crosswind side panel for the Skew-T diagram.
 *
 * Shares the Y-axis (pressure) with the Skew-T.
 * Shows headwind/tailwind and crosswind at each pressure level
 * relative to the route leg heading (track_deg).
 */

import { SkewTTransform } from './skewt-transform';
import { SoundingProfileLevel, PlotArea } from './types';
import { isDarkTheme } from '../interaction-utils';

const PANEL_WIDTH = 80; // pixels
const HEADWIND_COLOR = '#d04040';   // Red = headwind
const TAILWIND_COLOR = '#30a030';   // Green = tailwind
const CROSSWIND_COLOR = '#d09020';  // Amber = crosswind

export interface WindPanelLayout {
  /** X offset of the wind panel from the left of its canvas area. */
  left: number;
  /** Width of the wind panel. */
  width: number;
  /** Plot area Y coords (shared with Skew-T). */
  top: number;
  height: number;
  bottom: number;
}

export function getWindPanelWidth(): number {
  return PANEL_WIDTH;
}

interface WindAtLevel {
  pressureHPa: number;
  headwindKt: number;   // positive = headwind, negative = tailwind
  crosswindKt: number;  // positive = from right
}

function computeWindComponents(
  levels: SoundingProfileLevel[],
  trackDeg: number,
): WindAtLevel[] {
  return levels
    .filter(lv => lv.wind_speed_kt !== null && lv.wind_direction_deg !== null)
    .map(lv => {
      const relativeWind = (lv.wind_direction_deg! - trackDeg) * Math.PI / 180;
      return {
        pressureHPa: lv.pressure_hpa,
        headwindKt: lv.wind_speed_kt! * Math.cos(relativeWind),
        crosswindKt: lv.wind_speed_kt! * Math.sin(relativeWind),
      };
    });
}

/**
 * Render the headwind/crosswind side panel.
 */
export function renderWindPanel(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  levels: SoundingProfileLevel[],
  trackDeg: number | null,
  layout: WindPanelLayout,
): void {
  if (trackDeg === null || levels.length < 2) return;

  const dark = isDarkTheme();
  const textColor = dark ? '#ccc' : '#444';
  const axisColor = dark ? '#555' : '#999';
  const bgColor = dark ? 'rgba(30, 30, 30, 0.3)' : 'rgba(245, 245, 245, 0.3)';

  const winds = computeWindComponents(levels, trackDeg);
  if (winds.length < 2) return;

  // Find max wind magnitude for X-axis scaling
  const maxWind = Math.max(
    ...winds.map(w => Math.max(Math.abs(w.headwindKt), Math.abs(w.crosswindKt))),
    10, // minimum scale
  );
  const scale = layout.width / 2 / maxWind; // center = 0, edges = ±maxWind

  ctx.save();

  // Background
  ctx.fillStyle = bgColor;
  ctx.fillRect(layout.left, layout.top, layout.width, layout.height);

  // Border
  ctx.strokeStyle = axisColor;
  ctx.lineWidth = 0.5;
  ctx.strokeRect(layout.left, layout.top, layout.width, layout.height);

  // Zero line
  const centerX = layout.left + layout.width / 2;
  ctx.strokeStyle = axisColor;
  ctx.setLineDash([2, 2]);
  ctx.beginPath();
  ctx.moveTo(centerX, layout.top);
  ctx.lineTo(centerX, layout.bottom);
  ctx.stroke();
  ctx.setLineDash([]);

  // Clip to panel
  ctx.beginPath();
  ctx.rect(layout.left, layout.top, layout.width, layout.height);
  ctx.clip();

  // Draw headwind profile line
  ctx.lineWidth = 1.5;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  let first = true;
  for (const w of winds) {
    const y = transform.pressureToY(w.pressureHPa);
    const x = centerX + w.headwindKt * scale;
    if (first) { ctx.moveTo(x, y); first = false; }
    else ctx.lineTo(x, y);
  }
  // Color by overall headwind direction
  ctx.strokeStyle = HEADWIND_COLOR;
  ctx.stroke();

  // Draw colored segments for headwind (red) vs tailwind (green)
  for (let i = 0; i < winds.length - 1; i++) {
    const w0 = winds[i];
    const w1 = winds[i + 1];
    const y0 = transform.pressureToY(w0.pressureHPa);
    const y1 = transform.pressureToY(w1.pressureHPa);
    const x0 = centerX + w0.headwindKt * scale;
    const x1 = centerX + w1.headwindKt * scale;
    const avgHW = (w0.headwindKt + w1.headwindKt) / 2;

    ctx.strokeStyle = avgHW > 0 ? HEADWIND_COLOR : TAILWIND_COLOR;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
  }

  // Draw crosswind profile line (amber, thinner)
  ctx.lineWidth = 1.0;
  ctx.strokeStyle = CROSSWIND_COLOR;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  first = true;
  for (const w of winds) {
    const y = transform.pressureToY(w.pressureHPa);
    const x = centerX + w.crosswindKt * scale;
    if (first) { ctx.moveTo(x, y); first = false; }
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.restore();

  // Panel title
  ctx.save();
  ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.fillStyle = textColor;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  ctx.fillText('HW / XW', centerX, layout.top - 2);

  // Scale labels
  ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'top';
  const scaleLabel = Math.round(maxWind);
  ctx.textAlign = 'left';
  ctx.fillText(`${scaleLabel}`, layout.left + layout.width - 2, layout.bottom + 2);
  ctx.textAlign = 'right';
  ctx.fillText(`-${scaleLabel}`, layout.left + 2, layout.bottom + 2);
  ctx.textAlign = 'center';
  ctx.fillText('0', centerX, layout.bottom + 2);
  ctx.fillText('kt', centerX, layout.bottom + 12);

  // Legend
  ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'top';
  const legendY = layout.bottom + 24;
  ctx.fillStyle = HEADWIND_COLOR;
  ctx.fillText('HW', centerX - 18, legendY);
  ctx.fillStyle = TAILWIND_COLOR;
  ctx.fillText('TW', centerX + 18, legendY);

  ctx.restore();
}
