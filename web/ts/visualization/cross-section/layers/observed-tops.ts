/** Observed satellite cloud tops (#574) — group `conditions`, **default ON**.
 *
 * This layer is the whole cross-check. It renders in the same space as the
 * NWP cloud bands, so "model says FL120, satellite saw FL280" is visible to
 * the eye with nobody computing it. Phase 1 deliberately computes no verdict:
 * the comparison is the pilot's to make, and the two things are drawn in
 * unmistakably different styles so it stays obvious which is measured and
 * which is forecast.
 *
 * Three things the drawing has to be honest about:
 *
 *  - **The retrieval commits to one top per pixel.** A cirrus-over-stratus
 *    stack shows up only in aggregate, so each route point draws its FL-band
 *    histogram as stacked ticks rather than a single line. A line would be a
 *    claim the data does not support.
 *  - **No coverage is not a clear sky.** Points where the retrieval could not
 *    answer draw a hatched "no data" mark at the top of the plot, never a gap
 *    (which reads as "nothing up there").
 *  - **The frame has an age.** A badge in the corner carries the valid time
 *    and how old it is, because this layer and the NWP bands under it are not
 *    contemporaneous.
 */

import type { CrossSectionLayer, CoordTransform, VizObserved, VizObservedPoint } from '../../types';

/** Half-width of a point's mark on the X axis, in nm. */
const MARK_HALF_WIDTH_NM = 4;
/** A band has to hold this share of the disc's cloudy pixels to be drawn. */
const MIN_BIN_FRACTION = 0.05;
/** How far the "depth unknown" hatching hangs below a top marker, px.
 *  Deliberately short: long enough to read as "there is cloud under this",
 *  short enough that it cannot be mistaken for measured vertical extent. */
const HATCH_DEPTH_PX = 9;
/** Fixed height of the off-scale box at the chart ceiling, px. Fixed because
 *  the real value has no position on this chart — the badge and the hover
 *  carry the number instead. */
const ABOVE_SCALE_BOX_PX = 14;
/** Half-width of the up arrow inside that box, px. */
const ARROW_PX = 4;

const TOP_COLOR = '#111827';
const BIN_COLOR = 'rgba(17, 24, 39, 0.55)';
const MULTILAYER_COLOR = '#b45309';
const NO_COVERAGE_COLOR = 'rgba(107, 114, 128, 0.75)';

/** Bands worth drawing at this point, strongest-signal filter applied. */
export function significantBins(point: VizObservedPoint) {
  return point.topsBins.filter((b) => b.fraction >= MIN_BIN_FRACTION);
}

/** True when this point's mark should read "we could not see", not "clear". */
export function isNoCoverage(point: VizObservedPoint): boolean {
  return point.topsNoCoverage;
}

/** Points with a cloud top, a band, or an explicit no-coverage state. */
export function drawablePoints(observed: VizObserved): VizObservedPoint[] {
  return observed.points.filter(
    (p) => p.topsNoCoverage || p.topsHighestFt != null || significantBins(p).length > 0,
  );
}

/** "14:00Z · 12 min old" — the badge text for a source's frame. */
export function ageBadgeText(validTime: string, ageMinutes: number, label: string): string {
  const stamp = new Date(validTime);
  const hhmm = Number.isNaN(stamp.getTime())
    ? '--:--'
    : `${String(stamp.getUTCHours()).padStart(2, '0')}:${String(stamp.getUTCMinutes()).padStart(2, '0')}Z`;
  const age = ageMinutes < 1 ? 'just now' : `${Math.round(ageMinutes)} min old`;
  return `${label} ${hhmm} · ${age}`;
}

export const observedTopsLayer: CrossSectionLayer = {
  id: 'observed-tops',
  name: 'Observed cloud tops',
  group: 'conditions',
  // On by default: this is the one layer that turns four collected data
  // streams into an answer a pilot can act on, and it is useless if nobody
  // finds it.
  defaultEnabled: true,
  metricId: 'observed_tops',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data) {
    if (data.timeAxisMode) return;
    const observed = data.observed;
    if (!observed || !observed.cloudTops) return;

    for (const point of drawablePoints(observed)) {
      drawPoint(ctx, transform, point);
    }
    // When the cap line is off-scale the badge is the only place the number
    // survives, so carry it there rather than leaving the pilot to infer
    // "higher than the chart" from a row of chevrons.
    const highest = highestTopFt(observed);
    const aboveScale = topsAboveScale(observed, transform);
    const suffix = aboveScale && highest != null ? ` \u00b7 tops to ${flLabel(highest)}` : '';
    drawBadge(
      ctx,
      transform,
      ageBadgeText(observed.cloudTops.validTime, observed.cloudTops.ageMinutes, 'Satellite') + suffix,
    );
  },
};

function drawPoint(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  point: VizObservedPoint,
): void {
  const { plotArea } = transform;
  const x0 = transform.distanceToX(point.distanceNm - MARK_HALF_WIDTH_NM);
  const x1 = transform.distanceToX(point.distanceNm + MARK_HALF_WIDTH_NM);
  const width = Math.max(2, x1 - x0);

  if (isNoCoverage(point)) {
    // Hatched mark at the top of the column: the satellite could not answer
    // here. Drawn rather than skipped — a gap reads as "nothing up there".
    ctx.save();
    ctx.strokeStyle = NO_COVERAGE_COLOR;
    ctx.lineWidth = 1;
    const y = plotArea.top + 6;
    for (let offset = 0; offset < width; offset += 4) {
      ctx.beginPath();
      ctx.moveTo(x0 + offset, y);
      ctx.lineTo(x0 + offset + 3, y + 6);
      ctx.stroke();
    }
    ctx.restore();
    return;
  }

  // One marker per populated FL band. NOT a filled band: this product carries
  // no cloud base at all, so a solid rect spanning the bin reads as "cloud
  // occupies FL150-250" when the data only says "this share of the TOPS is
  // somewhere in FL150-250". Drawn as a capped bar with a few short hatch
  // strokes hanging below it — the cap is what we measured, the hatching is
  // the depth we cannot see.
  ctx.save();
  for (const bin of significantBins(point)) {
    const yLo = transform.altitudeToY(bin.loFt);
    const yHi = transform.altitudeToY(bin.hiFt);
    if (yLo < plotArea.top) continue;  // wholly above the chart; the arrow says so
    const alpha = 0.3 + 0.5 * Math.min(1, bin.fraction);

    // The band the tops sit in.
    ctx.globalAlpha = alpha;
    ctx.fillStyle = BIN_COLOR;
    ctx.fillRect(x0, Math.max(yHi, plotArea.top), width, Math.max(2, yLo - Math.max(yHi, plotArea.top)));

    // Unknown depth below it, deliberately short so it cannot be read as extent.
    ctx.globalAlpha = alpha * 0.7;
    ctx.strokeStyle = BIN_COLOR;
    ctx.lineWidth = 1;
    for (let offset = 0; offset < width; offset += 4) {
      ctx.beginPath();
      ctx.moveTo(x0 + offset, yLo);
      ctx.lineTo(x0 + offset - HATCH_DEPTH_PX * 0.5, yLo + HATCH_DEPTH_PX);
      ctx.stroke();
    }
  }
  ctx.restore();

  if (point.topsHighestFt == null) return;

  const color = point.topsMultiLayerFraction > 0.1 ? MULTILAYER_COLOR : TOP_COLOR;
  const yTop = transform.altitudeToY(point.topsHighestFt);

  // Above the chart's ceiling the cap line has nowhere to go. A GA
  // cross-section is scaled to the aircraft's flight ceiling — 18,000 ft is
  // typical — while satellite tops routinely sit at FL350+, so on a normal
  // piston briefing the single most important number this layer produces is
  // off-scale. Clipping it silently leaves only the minority FL bands visible,
  // which reads as "tops are around FL200" when they are nowhere near it.
  // Draw an explicit above-scale chevron instead; the badge carries the value.
  if (yTop < plotArea.top) {
    // A fixed-height hatched box pinned to the chart ceiling, with an arrow:
    // "the top is above this chart". The height is fixed and meaningless on
    // purpose — scaling it to the real value would invent a position for
    // something that has none here. Hover carries the number.
    ctx.save();
    const yBox = plotArea.top + 1;
    ctx.globalAlpha = 0.5;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    for (let offset = 0; offset < width + ABOVE_SCALE_BOX_PX; offset += 4) {
      const sx = x0 + offset;
      ctx.beginPath();
      ctx.moveTo(Math.min(sx, x1), yBox);
      ctx.lineTo(Math.max(x0, sx - ABOVE_SCALE_BOX_PX), yBox + ABOVE_SCALE_BOX_PX);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = color;
    ctx.strokeRect(x0, yBox, width, ABOVE_SCALE_BOX_PX);

    // Up arrow, centred.
    const cx = (x0 + x1) / 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(cx, yBox + 2);
    ctx.lineTo(cx - ARROW_PX, yBox + 2 + ARROW_PX);
    ctx.lineTo(cx + ARROW_PX, yBox + 2 + ARROW_PX);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
    return;
  }

  // The highest observed top: a solid cap, visually distinct from the soft
  // NWP cloud bands underneath so measured and modelled never blur together.
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x0, yTop);
  ctx.lineTo(x1, yTop);
  ctx.stroke();
  ctx.restore();
}

/** Highest observed top anywhere on the route, in ft. `null` when nothing was
 *  retrieved — used to put the number in the badge when the cap line itself is
 *  above the chart. */
export function highestTopFt(observed: VizObserved): number | null {
  let best: number | null = null;
  for (const p of observed.points) {
    if (p.topsHighestFt != null && (best == null || p.topsHighestFt > best)) {
      best = p.topsHighestFt;
    }
  }
  return best;
}

/** True when that highest top sits above the plotted altitude range. */
export function topsAboveScale(observed: VizObserved, transform: CoordTransform): boolean {
  const highest = highestTopFt(observed);
  if (highest == null) return false;
  return transform.altitudeToY(highest) < transform.plotArea.top;
}

/** "FL381" for a height in feet. */
export function flLabel(ft: number): string {
  return `FL${Math.round(ft / 100)}`;
}

/** Height of one badge row, including its gap. */
export const BADGE_ROW_HEIGHT_PX = 15;

/**
 * Age badge, top-right of the plot. Each observed source carries its own.
 *
 * `row` stacks them: with both observed layers enabled, drawing at one fixed
 * position let whichever layer rendered last paint over the other, hiding one
 * source's age entirely. That is the same "one age for four sources" outcome
 * the design rules out, reached by z-order instead of by string concatenation
 * — and the layers really are minutes apart, so the hidden number was never
 * the one on top.
 */
export function drawBadge(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  text: string,
  row = 0,
): void {
  const { plotArea } = transform;
  ctx.save();
  ctx.font = '600 10px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  ctx.textAlign = 'right';
  const x = plotArea.left + plotArea.width - 6;
  const y = plotArea.top + 4 + row * BADGE_ROW_HEIGHT_PX;
  const width = ctx.measureText(text).width;
  ctx.fillStyle = 'rgba(255, 255, 255, 0.82)';
  ctx.fillRect(x - width - 5, y - 2, width + 10, BADGE_ROW_HEIGHT_PX);
  ctx.fillStyle = '#374151';
  ctx.fillText(text, x, y);
  ctx.restore();
}
