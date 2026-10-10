/** Runway + wind widget (#758) — the drawing. All rules live in
 * `runway-wind-core.ts`.
 *
 * SYNC — paired with
 * app/flyfun-weather/flyfun-weather/Views/Shared/RunwayWindView.swift. This
 * file is the SVG equivalent of that SwiftUI drawing and holds no rules of its
 * own: a position, a label or a string changes in `runway-wind-core.ts` and in
 * `RunwayWindRules.swift`, not here.
 *
 * The SVG uses `viewBox="0 0 100 100"` over the core's unit square, so one
 * drawing scales to any box (the core's geometry is resolution-free). Colours
 * are CSS tokens, so light and dark follow the page.
 *
 * The widget takes a dial and a size and never knows where it is placed; the
 * departure/destination pair is a layout container only.
 */

import { escapeHtml } from '../../utils';
import { formatHhmmZ } from '../../helpers/live-layer';
import {
  CENTER,
  RIM_R,
  componentsLine,
  crosswindText,
  crosswindTone,
  endLine,
  ghostLine,
  missingWindLabel,
  runwayBars,
  visibleLabels,
  windArrow,
  windLabel,
  windMark,
  type Pt,
  type RunwayWindDial,
  type RunwayWindSize,
  type WindArrow,
} from './runway-wind-core';
import type { WindAtAirport } from '../../store/types';

const S = 100; // unit square → viewBox

function n(v: number): string {
  return (v * S).toFixed(1);
}

function arrowSvg(a: WindArrow, cls: string): string {
  // Head: a small triangle at `to`, pointing from `from` to `to`.
  const dx = a.to.x - a.from.x;
  const dy = a.to.y - a.from.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const head = 0.045;
  const wing = 0.028;
  const base: Pt = { x: a.to.x - ux * head, y: a.to.y - uy * head };
  const left: Pt = { x: base.x - uy * wing, y: base.y + ux * wing };
  const right: Pt = { x: base.x + uy * wing, y: base.y - ux * wing };
  const gust = a.gustTo
    ? `<line class="rw-gust" x1="${n(a.to.x)}" y1="${n(a.to.y)}" x2="${n(a.gustTo.x)}" y2="${n(a.gustTo.y)}"/>`
    : '';
  return `<g class="${cls}">`
    + `<line class="rw-shaft" x1="${n(a.from.x)}" y1="${n(a.from.y)}" x2="${n(base.x)}" y2="${n(base.y)}"/>`
    + gust
    + `<polygon class="rw-head" points="${n(a.to.x)},${n(a.to.y)} ${n(left.x)},${n(left.y)} ${n(right.x)},${n(right.y)}"/>`
    + `</g>`;
}

/** The dial alone, as an SVG string. */
export function runwayWindSvg(dial: RunwayWindDial, size: RunwayWindSize): string {
  const runways = dial.picture?.runways ?? [];
  const bars = runwayBars(runways);
  const parts: string[] = [];

  parts.push(`<circle class="rw-rim" cx="${n(CENTER)}" cy="${n(CENTER)}" r="${n(RIM_R)}"/>`);
  if (size !== 'inline') {
    parts.push(`<text class="rw-north" x="${n(CENTER)}" y="${n(CENTER - RIM_R + 0.07)}">N</text>`);
  }
  for (const b of bars) {
    const cls = b.hard === false ? 'rw-bar rw-bar-soft' : 'rw-bar';
    parts.push(`<line class="${cls}" x1="${n(b.a.x)}" y1="${n(b.a.y)}" x2="${n(b.b.x)}" y2="${n(b.b.y)}"/>`);
  }
  for (const l of visibleLabels(bars, size, dial.primary?.best_end ?? null)) {
    const best = l.ident === dial.primary?.best_end ? ' rw-label-best' : '';
    parts.push(`<text class="rw-label${best}" x="${n(l.at.x)}" y="${n(l.at.y)}">${escapeHtml(l.ident)}</text>`);
  }

  if (dial.ghost && size !== 'inline') {
    const ghost = windArrow(dial.ghost.wind);
    if (ghost) parts.push(arrowSvg(ghost, 'rw-arrow rw-ghost'));
  }

  const mark = windMark(dial.primary, missingWindLabel(dial));
  if (mark.kind === 'arrow') {
    if (mark.arc) {
      const large = mark.arc.sweepDeg > 180 ? 1 : 0;
      parts.push(`<path class="rw-arc" d="M ${n(mark.arc.from.x)} ${n(mark.arc.from.y)} `
        + `A ${n(mark.arc.r)} ${n(mark.arc.r)} 0 ${large} 1 ${n(mark.arc.to.x)} ${n(mark.arc.to.y)}"/>`);
    }
    parts.push(arrowSvg(mark.arrow, 'rw-arrow'));
  } else if (mark.kind === 'vrb') {
    parts.push(`<circle class="rw-vrb" cx="${n(CENTER)}" cy="${n(CENTER)}" r="${n(mark.r)}"/>`);
    if (size !== 'inline') parts.push(`<text class="rw-mark-label" x="${n(CENTER)}" y="${n(0.9)}">${escapeHtml(mark.label)}</text>`);
  } else if (mark.kind === 'calm') {
    parts.push(`<circle class="rw-calm" cx="${n(CENTER)}" cy="${n(CENTER)}" r="${n(0.03)}"/>`);
    if (size !== 'inline') parts.push(`<text class="rw-mark-label" x="${n(CENTER)}" y="${n(0.9)}">Calm</text>`);
  } else if (size !== 'inline') {
    parts.push(`<text class="rw-mark-label rw-missing" x="${n(CENTER)}" y="${n(0.9)}">${escapeHtml(mark.label)}</text>`);
  }

  const title = dial.primary ? componentsLine(dial.primary, missingWindLabel(dial)) : missingWindLabel(dial);
  return `<svg class="rw-svg rw-${size}" viewBox="0 0 ${S} ${S}" role="img" aria-label="${escapeHtml(`${dial.icao}: ${title}`)}">`
    + parts.join('')
    + `</svg>`;
}

/** A text line with only its crosswind part toned (amber/red); green is quiet. */
function lineHtml(text: string, w: WindAtAirport | null, cls: string, key: string): string {
  const tone = crosswindTone(w?.advisory ?? null);
  const cross = crosswindText(w);
  const at = tone && cross ? text.lastIndexOf(cross) : -1;
  const body = at >= 0
    ? escapeHtml(text.slice(0, at)) + `<span class="rw-tone-${tone}">${escapeHtml(cross!)}</span>`
      + escapeHtml(text.slice(at + cross!.length))
    : escapeHtml(text);
  return `<div class="${cls}" data-rw-line="${key}">${body}</div>`;
}

/** One dial with its heading, text lines and the all-ends list. */
export function runwayWindDialHtml(dial: RunwayWindDial, size: RunwayWindSize): string {
  if (size === 'inline') return runwayWindSvg(dial, size);
  const missing = missingWindLabel(dial);
  const primary = dial.primary;
  const head = `<div class="rw-head"><span class="rw-role">${dial.role}</span> `
    + `<span class="rw-icao">${escapeHtml(dial.icao)}</span>`
    + (primary ? `<span class="rw-wind">METAR ${escapeHtml(formatHhmmZ(primary.wind.time))} ${escapeHtml(windLabel(primary.wind))}</span>` : '')
    + `</div>`;
  const lines = [lineHtml(componentsLine(primary, missing), primary, 'rw-line', 'primary')];
  if (size === 'regular' && dial.ghost) {
    lines.push(lineHtml(ghostLine(dial.ghost), dial.ghost, 'rw-line rw-line-ghost', 'ghost'));
  }
  const ends = primary?.ends ?? [];
  const details = ends.length > 1
    ? `<details class="rw-details"><summary>All runways</summary><ul>`
      + ends.map((e) => `<li>${escapeHtml(endLine(e, primary!.wind))}</li>`).join('')
      + `</ul><p class="rw-note">Head/crosswind on each runway end from the latest METAR. `
      + `“up to” is the worst case over a variable range or gust. True north up; runway numbers as painted.</p></details>`
    : '';
  return `<div class="rw-dial" data-rw-role="${dial.role}" data-rw-icao="${escapeHtml(dial.icao)}">`
    + head
    + `<div class="rw-plot">${runwayWindSvg(dial, size)}</div>`
    + lines.join('')
    + details
    + `</div>`;
}

/** The departure + destination pair: a layout container, nothing more. */
export function runwayWindPairHtml(dials: RunwayWindDial[], size: RunwayWindSize = 'regular'): string {
  if (!dials.length) return '';
  return `<div class="rw-pair">${dials.map((d) => runwayWindDialHtml(d, size)).join('')}</div>`;
}
