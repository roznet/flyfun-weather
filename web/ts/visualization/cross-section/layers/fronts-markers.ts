/** Front markers (experimental, #196) — first Phase D.3 cross-section layer.
 *
 *  A front is a sloping 3-D surface, so when the linker associates a boundary
 *  across 925/850/700 (`data.fronts.chains`) we draw it as ONE slanted line
 *  through the per-level crossing positions — tilting back over the cold air
 *  with height — rather than independent vertical markers. Color = kind
 *  (cold=blue, warm=red, quasi=purple), weight = intensity, opacity = depth
 *  (single-level chains draw faint — shallow/suspect). A node dot marks each
 *  level the front was detected at; the segments are extrapolated (dashed) to
 *  the column edges to suggest the surface spanning the flight band.
 *
 *  Pre-linking packs carry no chains → we fall back to the original vertical
 *  marker per crossing. Advisory-only, free-atmosphere boundaries — not drawn
 *  SIGWX, and blind to low IMC / fog (§10a.2). Phase D.4 (GRIB era) will replace
 *  these with continuous-altitude θe from a per-briefing stencil. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import type { FrontChain } from '../../../types/fronts';
import {
  frontColor, frontKindLabel, frontAlpha, frontDash, frontIsConvective,
  FRONT_INTENSITY_WEIGHT,
} from '../../front-style';

const KM_PER_NM = 1.852;

/** Pressure level → representative MSL altitude (ft), matching the backend
 *  `_LEVEL_FT` in tasks/fronts.py. Drives the vertical placement of each node. */
const LEVEL_FT: Record<number, number> = { 925: 2_500, 850: 5_000, 700: 10_000 };

interface Pt { x: number; y: number; }

/** Extrapolate the line through (a, b) to the edge y-coordinate `yEdge`,
 *  returning the x there. Falls back to b.x when the segment is horizontal. */
function extendTo(a: Pt, b: Pt, yEdge: number): number {
  const dy = b.y - a.y;
  if (Math.abs(dy) < 1e-6) return b.x;
  const t = (yEdge - a.y) / dy;
  return a.x + t * (b.x - a.x);
}

function chainAlpha(chain: FrontChain): number {
  return chain.n_levels >= 2 ? 0.95 : 0.5;  // single-level = shallow/suspect → faint
}

function chainWeight(chain: FrontChain): number {
  return Math.max(...chain.nodes.map((n) => FRONT_INTENSITY_WEIGHT[n.intensity] ?? 2));
}

function renderChain(
  ctx: CanvasRenderingContext2D, transform: CoordTransform, chain: FrontChain,
  yTop: number, yBottom: number,
): void {
  const pts: Pt[] = chain.nodes.map((n) => ({
    x: transform.distanceToX(n.distance_km / KM_PER_NM),
    y: transform.altitudeToY(LEVEL_FT[n.level_hPa] ?? 5_000),
  }));
  if (pts.length === 0) return;

  const color = frontColor(chain.kind);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = chainWeight(chain);
  ctx.globalAlpha = chainAlpha(chain);

  if (pts.length === 1) {
    // Single-level (unlinked / shallow) front: a full-height vertical line like
    // the original marker, but dashed + faint so it reads as "one level only,
    // less certain" — only a vertically-linked front earns the solid slant.
    const x = pts[0].x;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(x, yTop);
    ctx.lineTo(x, yBottom);
    ctx.stroke();
    ctx.setLineDash([]);
  } else {
    // Dashed extrapolation beyond the outermost detected levels (the surface
    // continues past them). pts are ordered bottom→top (925→850→700), so pts[0]
    // is the visually-bottom node and pts[last] the top. Extrapolate above the
    // top node along the *top* segment's slope, and below the bottom node along
    // the *bottom* segment's slope, so the dashed meets the solid without a kink.
    const topNode = pts[pts.length - 1];
    const botNode = pts[0];
    const topX = extendTo(pts[pts.length - 2], topNode, yTop);   // top segment → above top node
    const botX = extendTo(pts[1], botNode, yBottom);             // bottom segment → below bottom node
    ctx.setLineDash([5, 5]);
    ctx.globalAlpha = chainAlpha(chain) * 0.5;
    ctx.beginPath();
    ctx.moveTo(topX, yTop);
    ctx.lineTo(topNode.x, topNode.y);
    ctx.moveTo(botNode.x, botNode.y);
    ctx.lineTo(botX, yBottom);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.globalAlpha = chainAlpha(chain);
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.stroke();
  }

  // Node dot at each detected level; a small triangle below a convective node
  // (towers above an overflown front) — mirrors the vertical-marker glyph so the
  // convective indicator isn't lost when a front renders as a chain.
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
    ctx.fill();
    if (chain.nodes[i].co_location === 'convective') {
      const ty = p.y + 5;
      ctx.beginPath();
      ctx.moveTo(p.x, ty);
      ctx.lineTo(p.x - 5, ty + 8);
      ctx.lineTo(p.x + 5, ty + 8);
      ctx.closePath();
      ctx.fill();
    }
  }

  // Kind-initial chip — at the column top for a single-level line, at the
  // visually-top node (pts[last] = 700 hPa) for a slanted chain.
  const top = pts.length === 1 ? pts[0] : pts[pts.length - 1];
  const label = frontKindLabel(chain.kind).charAt(0).toUpperCase();
  ctx.font = 'bold 11px system-ui, sans-serif';
  const tw = ctx.measureText(label).width;
  const pad = 3;
  const chipW = tw + pad * 2;
  const chipH = 15;
  const chipY = pts.length === 1 ? yTop : Math.max(yTop, top.y - chipH - 4);
  ctx.fillStyle = color;
  ctx.fillRect(top.x - chipW / 2, chipY, chipW, chipH);
  ctx.fillStyle = '#ffffff';
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';
  ctx.fillText(label, top.x, chipY + chipH / 2);
}

/** Pre-linking fallback: one vertical marker per merged crossing (the original
 *  rendering, kept for packs generated before vertical linking landed). */
function renderVerticalMarkers(
  ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData,
  yTop: number, yBottom: number,
): void {
  const fronts = data.fronts;
  if (!fronts) return;
  for (const c of fronts.crossings) {
    const x = transform.distanceToX(c.distance_km / KM_PER_NM);
    const color = frontColor(c.kind);
    ctx.globalAlpha = frontAlpha(c);
    ctx.strokeStyle = color;
    ctx.lineWidth = FRONT_INTENSITY_WEIGHT[c.intensity] ?? 2;
    ctx.setLineDash(frontDash(c));
    ctx.beginPath();
    ctx.moveTo(x, yTop);
    ctx.lineTo(x, yBottom);
    ctx.stroke();
    ctx.setLineDash([]);

    const label = frontKindLabel(c.kind).charAt(0).toUpperCase();
    ctx.font = 'bold 11px system-ui, sans-serif';
    const tw = ctx.measureText(label).width;
    const pad = 3;
    const chipW = tw + pad * 2;
    const chipH = 15;
    ctx.fillStyle = color;
    ctx.fillRect(x - chipW / 2, yTop, chipW, chipH);
    ctx.fillStyle = '#ffffff';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    ctx.fillText(label, x, yTop + chipH / 2);

    if (frontIsConvective(c)) {
      const ty = yTop + chipH + 2;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, ty);
      ctx.lineTo(x - 5, ty + 8);
      ctx.lineTo(x + 5, ty + 8);
      ctx.closePath();
      ctx.fill();
    }
  }
}

export const frontsMarkersLayer: CrossSectionLayer = {
  id: 'fronts-markers',
  name: 'Air-mass boundary (experimental)',
  group: 'fronts',
  defaultEnabled: false,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const fronts = data.fronts;
    if (!fronts) return;
    // Front distances are great-circle km; the X axis is in nautical miles.
    if (data.timeAxisMode) return;  // single-airport time view has no route distance

    const chains = fronts.chains ?? [];
    if (chains.length === 0 && fronts.crossings.length === 0) return;

    const { top, height } = transform.plotArea;
    const yTop = top;
    const yBottom = top + height;

    ctx.save();
    const savedAlpha = ctx.globalAlpha;
    if (chains.length > 0) {
      // Draw the slanted, vertically-linked fronts. Visual encoding: colour =
      // kind, weight = intensity, opacity = depth (single-level chains faint).
      for (const chain of chains) renderChain(ctx, transform, chain, yTop, yBottom);
    } else {
      // Pre-linking pack — original vertical markers (colour=kind, weight=
      // intensity, dash=wet/dry, opacity=persistence, triangle=convective).
      renderVerticalMarkers(ctx, transform, data, yTop, yBottom);
    }
    ctx.globalAlpha = savedAlpha;
    ctx.restore();
  },
};
