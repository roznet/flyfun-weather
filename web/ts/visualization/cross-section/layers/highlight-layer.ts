/** Advisory highlight layer (issue #373): scrim (focus) + verdict ribbon (judgement).
 *
 * Two visual elements, each doing exactly one job:
 *  - **Scrim** — a translucent dim wash over the plot area with cutouts punched
 *    out where the hazard physically is, each cutout framed by a thin
 *    severity-colored outline. Dimming means "not the focus", never a verdict.
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
const RIBBON_HEIGHT = 6;
const RIBBON_GAP = 2;      // gap below the plot area before the ribbon
const CUTOUT_OUTLINE_WIDTH = 1.5;

/** Theme-aware severity colour, aligned with the advisory status colours
 *  (`--red` / `--amber` / `--green` CSS vars, which flip in dark mode).
 *  Unavailable = neutral gray. */
function severityColor(sev: HighlightSeverity): string {
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

function drawScrim(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  regions: HighlightRegion[],
): void {
  if (regions.length === 0) return;  // all-green: never dim a clean chart
  const { plotArea } = transform;

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

  // 2. Punch out each region (spotlight cutout).
  offCtx.globalCompositeOperation = 'destination-out';
  for (const region of regions) {
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
  for (const region of regions) {
    const x0 = transform.distanceToX(region.dist_from_nm);
    const x1 = transform.distanceToX(region.dist_to_nm);
    const { yTop, yBottom } = regionYSpan(region, transform);
    ctx.strokeStyle = severityColor(region.severity);
    ctx.strokeRect(x0, yTop, x1 - x0, yBottom - yTop);
  }
  ctx.restore();
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
