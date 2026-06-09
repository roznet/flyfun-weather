/** Night/twilight shading + sun boundary markers (#227) — one layer.
 *
 * Two parts, drawn in a single pass at the very back of the stack:
 *  1. Full-height column tint behind the weather — light twilight wash, darker
 *     night band. Terrain (drawn later) masks the below-surface tint.
 *  2. Sun boundary markers at each in-flight transition (sunset, civil dusk,
 *     civil dawn, sunrise): a vertical line + sun glyph + Z-time label. The
 *     glyph/label sit in the clear top strip so they stay readable; the line
 *     shows through the translucent weather bands.
 *
 * Sourced from precomputed `nightIntervals` (from `manifest.sun`).
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizNightInterval } from '../../types';
import { getActiveTheme } from '../theme';

type Kind = 'Sunset' | 'Civil dusk' | 'Civil dawn' | 'Sunrise';

interface Boundary {
  nm: number;
  time: string;
  kind: Kind;
}

// Region brightness levels for classifying a transition.
const NIGHT = 0, TWILIGHT = 1, DAY = 2;

// Sun glyph colour: warm gold for horizon crossings (sunset/sunrise), muted
// indigo for the −6° civil dusk/dawn. Theme-independent — a sun reads as a sun.
const GOLD = '#f4b740';
const INDIGO = '#9aa7d0';
const isHorizon = (k: Kind) => k === 'Sunset' || k === 'Sunrise';

/** Brightness level just at distance `nm` (probe sits strictly inside one side). */
function levelAt(nm: number, intervals: VizNightInterval[]): number {
  for (const iv of intervals) {
    if (nm >= iv.startNm && nm <= iv.endNm) return iv.phase === 'night' ? NIGHT : TWILIGHT;
  }
  return DAY;
}

/** Label a transition from `left` to `right` brightness. */
function classify(left: number, right: number): Kind | null {
  if (left === right) return null;
  if (right < left) return left === DAY ? 'Sunset' : 'Civil dusk';  // darkening
  return right === DAY ? 'Sunrise' : 'Civil dawn';                  // lightening
}

function hhmmZ(iso: string): string {
  try {
    const t = new Date(iso).getTime() + 30_000;  // round to the nearest minute
    return new Date(t).toISOString().slice(11, 16) + 'Z';
  } catch {
    return '';
  }
}

/** Collect interior transitions (skip boundaries at the route ends — those mean
 *  the flight simply starts/ends already dark, not an in-flight crossing). */
function boundaries(data: VizRouteData): Boundary[] {
  const ivs = data.nightIntervals;
  if (ivs.length === 0) return [];
  const total = data.totalDistanceNm;
  const endEps = Math.max(0.5, total * 0.002);
  const eps = Math.max(0.1, total * 0.001);

  // Unique edge distances (twilight.end often coincides with night.start).
  const edges = new Map<number, string>();
  for (const iv of ivs) {
    edges.set(Math.round(iv.startNm * 100), iv.startTime);
    edges.set(Math.round(iv.endNm * 100), iv.endTime);
  }

  const out: Boundary[] = [];
  for (const [keyed, time] of edges) {
    const nm = keyed / 100;
    if (nm <= endEps || nm >= total - endEps) continue;
    const kind = classify(levelAt(nm - eps, ivs), levelAt(nm + eps, ivs));
    if (kind) out.push({ nm, time, kind });
  }
  out.sort((a, b) => a.nm - b.nm);
  return out;
}

function drawSun(ctx: CanvasRenderingContext2D, cx: number, cy: number, color: string): void {
  const r = 4.5;
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  for (let i = 0; i < 8; i++) {
    const a = (Math.PI / 4) * i;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * (r + 1.5), cy + Math.sin(a) * (r + 1.5));
    ctx.lineTo(cx + Math.cos(a) * (r + 4), cy + Math.sin(a) * (r + 4));
    ctx.stroke();
  }
}

function drawMarkers(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData): void {
  if (data.timeAxisMode) return;  // single-airport time view has no route distance
  const bnds = boundaries(data);
  if (bnds.length === 0) return;

  const theme = getActiveTheme();
  const { top, height, left, width } = transform.plotArea;
  const yBottom = top + height;

  ctx.save();
  ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ctx.textBaseline = 'bottom';

  for (const b of bnds) {
    const x = transform.distanceToX(b.nm);
    if (x < left || x > left + width) continue;
    const color = isHorizon(b.kind) ? GOLD : INDIGO;

    // Vertical boundary line.
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.7;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(x, top + 22);
    ctx.lineTo(x, yBottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // Sun glyph at the top.
    drawSun(ctx, x, top + 10, color);

    // Label chip: "Sunset 19:54Z" centred under the glyph.
    const label = `${b.kind} ${hhmmZ(b.time)}`;
    const tw = ctx.measureText(label).width;
    const chipX = Math.min(Math.max(x - tw / 2 - 3, left + 1), left + width - tw - 5);
    ctx.fillStyle = theme.sky.background + 'dd';
    ctx.fillRect(chipX, top + 18, tw + 6, 13);
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    ctx.fillText(label, chipX + 3, top + 30);
  }
  ctx.restore();
}

export const nightShadingLayer: CrossSectionLayer = {
  id: 'night-shading',
  name: 'Sun & Night',
  group: 'sun',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    if (data.nightIntervals.length === 0) return;

    const { plotArea } = transform;
    const top = plotArea.top;
    const height = plotArea.height;
    const theme = getActiveTheme().nightShading;

    // 1. Column tint.
    for (const interval of data.nightIntervals) {
      const x0 = transform.distanceToX(interval.startNm);
      const x1 = transform.distanceToX(interval.endNm);
      const w = x1 - x0;
      if (w <= 0) continue;
      ctx.fillStyle = interval.phase === 'night' ? theme.night : theme.twilight;
      ctx.fillRect(x0, top, w, height);
    }

    // 2. Sunset/sunrise boundary markers (same layer).
    drawMarkers(ctx, transform, data);
  },
};
