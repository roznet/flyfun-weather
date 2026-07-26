/** Shared HTML rendering for legend entries.
 *
 * One place decides what a legend swatch looks like, so the layer-info popup
 * and the theme preview can never disagree about how a band or a line reads.
 *
 * Swatches sit on a sky-coloured backing plate: nearly every cross-section
 * fill is semi-transparent, so a swatch shown on the popup's white card is a
 * different colour than the same fill on the chart.
 */

import type { LegendEntry } from './layer-legends';
import type { LineStyle } from './cross-section/theme';
import { escapeHtml } from '../utils';

/** CSS background reproducing a canvas stroke's colour + dash pattern.
 *
 *  Mirrors `setLineDash` semantics for a pattern of any length, not just
 *  `[on, off]`: segments alternate ink/gap, and an odd-length array repeats
 *  with the roles swapped (so `[9,3,2,3]` is dash-dot, and the swatch shows
 *  the dot instead of quietly flattening to a plain dash). */
export function lineSampleBackground(line: LineStyle): string {
  const dash = line.dash;
  if (!dash || dash.length === 0) return line.color;

  // Canvas repeats an odd-length pattern twice before it realigns.
  const pattern = dash.length % 2 === 1 ? [...dash, ...dash] : dash;
  const cycle = pattern.reduce((a, b) => a + b, 0);
  if (cycle <= 0) return line.color;

  const stops: string[] = [];
  let at = 0;
  pattern.forEach((len, i) => {
    const paint = i % 2 === 0 ? line.color : 'transparent';
    stops.push(`${paint} ${at}px`, `${paint} ${at + len}px`);
    at += len;
  });
  return `repeating-linear-gradient(90deg, ${stops.join(', ')})`;
}

/** The inner swatch markup for one entry — a dashed/solid rule for line
 *  layers, a filled (optionally hatched) block for band layers. */
export function legendSwatchInnerHtml(entry: LegendEntry): string {
  if (entry.line) {
    const h = Math.max(2, entry.line.width);
    return `<span class="legend-swatch legend-swatch-line" style="`
      + `background: ${lineSampleBackground(entry.line)}; height: ${h}px;"></span>`;
  }
  const bg = entry.hatchStyle
    ? `background: ${entry.hatchStyle}, ${entry.color};`
    : `background: ${entry.color};`;
  return `<span class="legend-swatch" style="${bg}"></span>`;
}

/** Full swatch including the sky backing plate. */
export function legendSwatchHtml(entry: LegendEntry, skyBg: string): string {
  const lineMod = entry.line ? ' legend-swatch-bg--line' : '';
  return `<span class="legend-swatch-bg${lineMod}" style="background: ${skyBg}">`
    + `${legendSwatchInnerHtml(entry)}</span>`;
}

/** Rows of `swatch · label · meaning` — the layer-info popup layout. */
export function legendRowsHtml(entries: LegendEntry[], skyBg: string): string {
  return entries.map((e) => `<div class="legend-entry">
      ${legendSwatchHtml(e, skyBg)}
      <span class="legend-label">${escapeHtml(e.label)}</span>
      <span class="legend-meaning">${escapeHtml(e.meaning)}</span>
    </div>`).join('');
}

/** Compact `swatch · label` chips for grid layouts where the meaning would
 *  crowd the columns; the meaning rides along as a tooltip. */
export function legendChipsHtml(entries: LegendEntry[], skyBg: string): string {
  return entries.map((e) => `<div class="legend-chip" title="${escapeHtml(e.meaning)}">
      ${legendSwatchHtml(e, skyBg)}
      <span class="legend-chip-label">${escapeHtml(e.label)}</span>
    </div>`).join('');
}
