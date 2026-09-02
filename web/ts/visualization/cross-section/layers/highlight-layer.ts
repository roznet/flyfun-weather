/** Advisory highlight layer (issue #373): scrim (focus) + verdict ribbon (judgement).
 *
 * Two visual elements, each doing exactly one job:
 *  - **Scrim** — a translucent dim wash over the plot area with cutouts punched
 *    out where the hazard physically is, each cutout framed by a thin
 *    severity-colored outline (dashed when the region's depth is an estimate
 *    borrowed from another analysis track — `tower_estimated`, #592). A region
 *    whose depth is *unknown* (`tower_unresolved`) is not a spotlight at all: it
 *    draws a short dashed stub on the plot floor and punches nothing.
 *    Dimming means "not the focus", never a verdict.
 *    Composed on an offscreen canvas (so `destination-out` punches the wash, not
 *    the sky/axes beneath) then drawn onto the main canvas. No scrim at all when
 *    there are no flagged regions (the all-green case — never dim a clean chart).
 *  - **Verdict ribbon** — a ~6px strip in the bottom margin (below the plot,
 *    above the distance labels) partitioning the whole route into
 *    green/amber/red/gray for this advisory. Renders even in the all-green case
 *    (an explicit "checked: clear the whole way"). Draws in the margin, so the
 *    layer opts out of plot-area clipping via `clipToPlot: false`.
 *
 * Geometry is derived reactively (briefing-main attaches `data.advisoryHighlights`);
 * this layer is pure rendering. It no-ops when there is no highlight data.
 *
 * SYNC: the iOS app mirrors this renderer (geometry constants + composition
 * rules) in app/flyfun-weather/flyfun-weather/Views/CrossSection/Layers/
 * HighlightLayer.swift (#374) — keep the two in lockstep.
 */

import type {
  CrossSectionLayer,
  CoordTransform,
  VizRouteData,
} from '../../types';
import type { HighlightRegion, HighlightSeverity } from '../../../types/advisories';
import { cssVar, isDarkTheme } from '../../interaction-utils';
import { HIGHLIGHT_LAYER_ID } from '../advisory-highlights';

/** Ribbon strip geometry within the bottom margin. The ribbon occupies
 *  [plotArea.bottom + RIBBON_GAP, + RIBBON_GAP + RIBBON_HEIGHT] = [+2, +8], which
 *  sits ABOVE the distance-axis labels — axes.ts pushes those to DISTANCE_LABEL_DY
 *  (+11) / WAYPOINT_LABEL_DY (+25) to keep this strip clear (#373). Keep in sync
 *  with those offsets so the ribbon never paints over the tick labels. */
export const RIBBON_HEIGHT = 6;
export const RIBBON_GAP = 2;      // gap below the plot area before the ribbon
const CUTOUT_OUTLINE_WIDTH = 1.5;

/** Regions whose depth is UNKNOWN rather than full-column (#592). They get no
 *  cutout and no box: a terrain-to-top rectangle is the strongest possible claim
 *  about vertical extent, drawn exactly where we know the least, and it read as
 *  a rendering bug ("tall empty boxes over clear sky"). They draw as a short
 *  dashed stub rising off the plot floor instead — "a cell is here, depth
 *  unknown" — with the verdict ribbon underneath carrying the along-route
 *  extent. NOT to be confused with the genuinely full-column kinds
 *  (`precip_column`, `freezing_precip_column`), where the column IS the hazard. */
const DEPTH_UNKNOWN_KINDS = new Set(['tower_unresolved']);
/** Regions at least one of whose bounds was borrowed from another analysis
 *  track (a convective tower drawn on thermodynamic base/top over an NWP-graded
 *  point, #592). The box is real but its depth is an estimate, so it is outlined
 *  dashed rather than solid. */
const ESTIMATED_KINDS = new Set(['tower_estimated']);
/** Stub height (px) and dash patterns. SYNC: mirrored on iOS. */
const DEPTH_UNKNOWN_STUB_HEIGHT = 14;
const DEPTH_UNKNOWN_DASH = [3, 3];
const ESTIMATED_DASH = [4, 3];

/** Theme-aware severity colour, aligned with the advisory status colours
 *  (`--red` / `--amber` / `--green` CSS vars, which flip in dark mode).
 *  Unavailable = neutral gray. Exported so the ribbon-hover tooltip (#412)
 *  swatches match the strip exactly. */
export function severityColor(sev: HighlightSeverity): string {
  switch (sev) {
    case 'red': return cssVar('--red', '#dc3545');
    case 'amber': return cssVar('--amber', '#cc8800');
    case 'green': return cssVar('--green', '#198754');
    default: return isDarkTheme() ? '#6b7280' : '#9ca3af';  // unavailable
  }
}

/** The dim-wash colour for the scrim, light/dark variants. */
function scrimWash(): string {
  return isDarkTheme() ? 'rgba(0, 0, 0, 0.42)' : 'rgba(15, 23, 42, 0.34)';
}

/** Vertical extent of a region rect. base/top null → terrain-to-top (full column). */
function regionYSpan(region: HighlightRegion, transform: CoordTransform): { yTop: number; yBottom: number } {
  const { plotArea } = transform;
  const yTop = region.top_ft != null
    ? transform.altitudeToY(region.top_ft)
    : plotArea.top;
  const yBottom = region.base_ft != null
    ? transform.altitudeToY(region.base_ft)
    : plotArea.top + plotArea.height;
  return { yTop, yBottom };
}

/** A short dashed stub off the plot floor at the region's mid-x: "flagged here,
 *  depth unknown". Drawn on the main canvas (after the scrim composite) so it is
 *  never dimmed by the wash. */
function drawDepthUnknownMarker(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  region: HighlightRegion,
): void {
  const { plotArea } = transform;
  const x0 = transform.distanceToX(region.dist_from_nm);
  const x1 = transform.distanceToX(region.dist_to_nm);
  const xMid = (x0 + x1) / 2;
  const yFloor = plotArea.top + plotArea.height;
  ctx.save();
  ctx.strokeStyle = severityColor(region.severity);
  ctx.lineWidth = CUTOUT_OUTLINE_WIDTH;
  ctx.setLineDash(DEPTH_UNKNOWN_DASH);
  ctx.beginPath();
  ctx.moveTo(xMid, yFloor);
  ctx.lineTo(xMid, yFloor - DEPTH_UNKNOWN_STUB_HEIGHT);
  ctx.stroke();
  ctx.restore();
}

function drawScrim(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  regions: HighlightRegion[],
): void {
  if (regions.length === 0) return;  // all-green: never dim a clean chart
  const { plotArea } = transform;
  // Depth-unknown regions are not spotlights — there is no place on the y axis
  // to point at — so they neither punch nor outline. When they are ALL a model
  // has, there is nothing to spotlight and the wash is skipped entirely: dimming
  // the whole chart to highlight nothing is worse than not dimming it.
  const cutouts = regions.filter((r) => !DEPTH_UNKNOWN_KINDS.has(r.kind));
  const depthUnknown = regions.filter((r) => DEPTH_UNKNOWN_KINDS.has(r.kind));
  if (cutouts.length === 0) {
    for (const region of depthUnknown) drawDepthUnknownMarker(ctx, transform, region);
    return;
  }

  // Compose the dim wash + cutouts on an offscreen canvas so `destination-out`
  // punches only the wash, not the sky/axes/weather beneath (compare-mode
  // precedent). The main ctx is already dpr-scaled (coords in CSS px); mirror
  // that on the offscreen so geometry lines up 1:1.
  const devW = ctx.canvas.width;
  const devH = ctx.canvas.height;
  const dpr = window.devicePixelRatio || 1;
  const off = document.createElement('canvas');
  off.width = devW;
  off.height = devH;
  const offCtx = off.getContext('2d');
  if (!offCtx) return;
  offCtx.scale(dpr, dpr);

  // 1. Fill the plot rect with the dim wash.
  offCtx.fillStyle = scrimWash();
  offCtx.fillRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);

  // 2. Punch out each region (spotlight cutout). The punch MUST be fully opaque:
  //    `destination-out` yields outAlpha = destAlpha * (1 - srcAlpha), so punching
  //    with the wash's own translucent fillStyle would erase only ~a third of it and
  //    leave the spotlight dimmed (and double-punched overlaps brighter than single
  //    ones). Mirrors iOS's `.color(.black)`.
  offCtx.globalCompositeOperation = 'destination-out';
  offCtx.fillStyle = '#000';
  for (const region of cutouts) {
    const x0 = transform.distanceToX(region.dist_from_nm);
    const x1 = transform.distanceToX(region.dist_to_nm);
    const { yTop, yBottom } = regionYSpan(region, transform);
    offCtx.fillRect(x0, yTop, x1 - x0, yBottom - yTop);
  }
  offCtx.globalCompositeOperation = 'source-over';

  // 3. Composite the scrim onto the main canvas.
  ctx.drawImage(off, 0, 0, devW, devH, 0, 0, devW / dpr, devH / dpr);

  // 4. Stroke each cutout with a severity-coloured outline (directly on main
  //    canvas, clipped to the plot area so a full-column outline doesn't bleed).
  ctx.save();
  ctx.beginPath();
  ctx.rect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
  ctx.clip();
  ctx.lineWidth = CUTOUT_OUTLINE_WIDTH;
  for (const region of cutouts) {
    const x0 = transform.distanceToX(region.dist_from_nm);
    const x1 = transform.distanceToX(region.dist_to_nm);
    const { yTop, yBottom } = regionYSpan(region, transform);
    ctx.strokeStyle = severityColor(region.severity);
    // Dashed = the depth is an estimate borrowed from another track (#592).
    ctx.setLineDash(ESTIMATED_KINDS.has(region.kind) ? ESTIMATED_DASH : []);
    ctx.strokeRect(x0, yTop, x1 - x0, yBottom - yTop);
  }
  ctx.setLineDash([]);
  ctx.restore();

  // 5. Depth-unknown markers, outside the clip (they sit on the plot floor) and
  //    after the composite so the wash never dims them.
  for (const region of depthUnknown) drawDepthUnknownMarker(ctx, transform, region);
}

function drawRibbon(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  const ribbon = data.advisoryHighlights?.ribbon ?? [];
  if (ribbon.length === 0) return;
  const { plotArea } = transform;
  const y = plotArea.top + plotArea.height + RIBBON_GAP;

  ctx.save();
  for (const seg of ribbon) {
    const x0 = transform.distanceToX(seg.dist_from_nm);
    const x1 = transform.distanceToX(seg.dist_to_nm);
    const w = x1 - x0;
    if (w <= 0) continue;
    ctx.fillStyle = severityColor(seg.severity);
    ctx.fillRect(x0, y, w, RIBBON_HEIGHT);
  }
  ctx.restore();
}

export const highlightLayer: CrossSectionLayer = {
  id: HIGHLIGHT_LAYER_ID,
  name: 'Highlight',
  group: 'highlight',
  defaultEnabled: false,
  // The ribbon renders in the bottom margin (below the plot), so this layer must
  // NOT be clipped to the plot area. The scrim manages its own clipping.
  clipToPlot: false,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const highlights = data.advisoryHighlights;
    if (!highlights) return;  // no tracked advisory / old pack / model has no data
    drawScrim(ctx, transform, highlights.regions);
    drawRibbon(ctx, transform, data);
  },
};
