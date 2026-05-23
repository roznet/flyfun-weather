/** Current conditions overlay (D-0): METAR airport columns + route SIGMET zones.
 *
 * Both data sources are model-independent and already keyed in route-distance
 * terms on the snapshot, so this layer just projects them onto the existing
 * CoordTransform — no per-point transform, no re-fetch.
 *
 *  - METAR airports: a column ±2 nm around the airport's along-route position,
 *    5000 ft tall from the terrain surface, filled with the flight-category
 *    color (VFR/MVFR/IFR/LIFR). Where columns overlap, the airport closest to
 *    the route draws on top.
 *  - SIGMETs: a red diagonally-hatched zone spanning the enroute extent on X
 *    and the vertical band on Y, labeled with the hazard, deeper red for
 *    SEV/EMBD.
 */

import type { CrossSectionLayer, CoordTransform, VizMetarColumn, VizSigmetZone } from '../../types';
import { flightCategoryColor } from '../../scales';

const COLUMN_HALF_WIDTH_NM = 2;
export const COLUMN_HEIGHT_FT = 5000;
const SIGMET_MIN_SPAN_NM = 5;

/** ±2 nm column span (nm) around an airport's along-route position. */
export function columnSpanNm(enrouteNm: number): [number, number] {
  return [enrouteNm - COLUMN_HALF_WIDTH_NM, enrouteNm + COLUMN_HALF_WIDTH_NM];
}

/** SIGMET enroute span (nm), widened to a 5 nm minimum centered on the midpoint. */
export function sigmetSpanNm(fromNm: number, toNm: number): [number, number] {
  const lo = Math.min(fromNm, toNm);
  const hi = Math.max(fromNm, toNm);
  if (hi - lo >= SIGMET_MIN_SPAN_NM) return [lo, hi];
  const mid = (lo + hi) / 2;
  return [mid - SIGMET_MIN_SPAN_NM / 2, mid + SIGMET_MIN_SPAN_NM / 2];
}

/** Airports ordered for drawing: farthest-from-route first so the airport
 *  closest to the route draws last (on top) where columns overlap. */
export function sortColumnsForDraw(airports: VizMetarColumn[]): VizMetarColumn[] {
  return [...airports].sort((a, b) => b.distanceFromRouteNm - a.distanceFromRouteNm);
}

/** SEV/EMBD qualifiers get the deeper-red severe styling. */
export function isSevereSigmet(qualifier: string | null): boolean {
  if (!qualifier) return false;
  const q = qualifier.toUpperCase();
  return q.includes('SEV') || q.includes('EMBD');
}

export const currentConditionsLayer: CrossSectionLayer = {
  id: 'current-conditions',
  name: 'Current conditions',
  group: 'conditions',
  defaultEnabled: false,
  metricId: 'current_conditions',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data) {
    // The airport-profile drawer renders this same cross-section in
    // time-axis mode (one airport, time on X), where a distance-based
    // overlay is meaningless.
    if (data.timeAxisMode) return;
    const cc = data.currentConditions;
    if (!cc) return;

    // SIGMET zones first (broad hazard context), then METAR columns on top so
    // the airport flight category — the go/no-go readout — stays legible.
    for (const s of cc.sigmets) drawSigmetZone(ctx, transform, s);
    drawMetarColumns(ctx, transform, cc.airports);
  },
};

function drawMetarColumns(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  airports: VizMetarColumn[],
): void {
  if (airports.length === 0) return;
  const { plotArea } = transform;

  // Fills + outlines: farthest-from-route first → closest on top.
  for (const a of sortColumnsForDraw(airports)) {
    const [d0, d1] = columnSpanNm(a.enrouteDistanceNm);
    const x0 = transform.distanceToX(d0);
    const x1 = transform.distanceToX(d1);
    const yBase = transform.altitudeToY(a.baseFt);
    const yTop = transform.altitudeToY(a.baseFt + COLUMN_HEIGHT_FT);
    const color = flightCategoryColor(a.flightCategory);

    ctx.save();
    ctx.globalAlpha = 0.32;
    ctx.fillStyle = color;
    ctx.fillRect(x0, yTop, x1 - x0, yBase - yTop);
    ctx.restore();

    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x0, yTop, x1 - x0, yBase - yTop);
  }

  // Labels: closest-to-route first (label priority), with X-collision
  // avoidance so clustered corridor airports (<4 nm apart) don't overprint.
  ctx.save();
  ctx.font = '600 11px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  ctx.textAlign = 'center';
  const placed: Array<[number, number]> = [];
  const byProximity = [...airports].sort((a, b) => a.distanceFromRouteNm - b.distanceFromRouteNm);
  for (const a of byProximity) {
    const cx = transform.distanceToX(a.enrouteDistanceNm);
    const label = `${a.icao} ${a.flightCategory}`;
    const w = ctx.measureText(label).width;
    const lx0 = cx - w / 2;
    const lx1 = cx + w / 2;
    if (placed.some(([p0, p1]) => lx0 < p1 + 2 && lx1 > p0 - 2)) continue;
    placed.push([lx0, lx1]);

    const yTop = transform.altitudeToY(a.baseFt + COLUMN_HEIGHT_FT);
    const ty = Math.max(plotArea.top + 2, yTop + 3);
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.strokeText(label, cx, ty);
    ctx.fillStyle = '#111827';
    ctx.fillText(label, cx, ty);
  }
  ctx.restore();
}

function drawSigmetZone(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  s: VizSigmetZone,
): void {
  const { plotArea } = transform;
  const top = plotArea.top;
  const bottom = plotArea.top + plotArea.height;

  const [from, to] = sigmetSpanNm(s.enrouteFromNm, s.enrouteToNm);
  let x0 = transform.distanceToX(from);
  let x1 = transform.distanceToX(to);
  if (x1 < x0) [x0, x1] = [x1, x0];

  // Vertical band: a null bound spans the full plot height (unknown extent).
  let yTop = s.topFt != null ? transform.altitudeToY(s.topFt) : top;
  let yBase = s.baseFt != null ? transform.altitudeToY(s.baseFt) : bottom;
  yTop = Math.max(top, Math.min(yTop, bottom));
  yBase = Math.max(top, Math.min(yBase, bottom));
  if (yBase < yTop) [yTop, yBase] = [yBase, yTop];

  const severe = isSevereSigmet(s.qualifier);
  const rgb = severe ? '160, 0, 0' : '200, 45, 45';

  ctx.save();
  ctx.fillStyle = `rgba(${rgb}, ${severe ? 0.26 : 0.16})`;
  ctx.fillRect(x0, yTop, x1 - x0, yBase - yTop);
  drawRectDiagonalHatch(
    ctx, x0, yTop, x1, yBase,
    `rgba(${rgb}, ${severe ? 0.7 : 0.5})`, severe ? 2 : 1.5,
  );
  ctx.restore();

  ctx.strokeStyle = `rgba(${rgb}, 0.9)`;
  ctx.lineWidth = severe ? 2 : 1.5;
  ctx.strokeRect(x0, yTop, x1 - x0, yBase - yTop);

  // Hazard label inside the zone (top-left).
  const label = s.qualifier ? `${s.qualifier} ${s.hazard}` : s.hazard;
  ctx.save();
  ctx.font = '700 11px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';
  const tx = x0 + 4;
  const ty = yTop + 4;
  ctx.lineWidth = 3;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.85)';
  ctx.strokeText(label, tx, ty);
  ctx.fillStyle = `rgb(${rgb})`;
  ctx.fillText(label, tx, ty);
  ctx.restore();
}

/** 45° diagonal hatching clipped to a rectangle, snapped to a global grid so
 *  adjacent zones align (mirrors the surface-obscuration hatch). */
function drawRectDiagonalHatch(
  ctx: CanvasRenderingContext2D,
  x0: number, y0: number, x1: number, y1: number,
  color: string, lineWidth: number,
): void {
  if (x1 <= x0 || y1 <= y0) return;
  const spacing = 8;
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, y0, x1 - x0, y1 - y0);
  ctx.clip();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash([]);

  // Lines y = x + c on a fixed c-grid.
  const cMin = y0 - x1;
  const cMax = y1 - x0;
  const startC = Math.ceil(cMin / spacing) * spacing;
  for (let c = startC; c <= cMax; c += spacing) {
    const ax = Math.max(x0, y0 - c);
    const bx = Math.min(x1, y1 - c);
    if (bx <= ax) continue;
    ctx.beginPath();
    ctx.moveTo(ax, ax + c);
    ctx.lineTo(bx, bx + c);
    ctx.stroke();
  }
  ctx.restore();
}
