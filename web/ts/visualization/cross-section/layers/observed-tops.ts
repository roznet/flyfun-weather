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

import type { CrossSectionLayer, CoordTransform, VizObserved, VizObservedPoint, VizObservedTopBin } from '../../types';
import { getActiveTheme } from '../theme';

/** Half-width of a point's mark on the X axis, in nm. */
const MARK_HALF_WIDTH_NM = 4;
/** A band has to hold this share of the disc's cloudy pixels to be drawn.
 *
 *  1%: below that a band is one or two pixels out of hundreds, and drawing it
 *  gives a stray retrieval the same visual weight as a real deck.
 *
 *  Safe to discard here only because the HIGHEST top is drawn separately, from
 *  `topsHighestFt`, and never passes through this filter — so a single cold
 *  pixel still gets its cap line or its off-scale arrow even when its band is
 *  too thin to draw. Losing the tail entirely was the objection to a high
 *  floor; the cap line is what answers it. */
const MIN_BIN_FRACTION = 0.01;
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
/** Spacing of the horizontal rules that fill a band, px. Closer together the
 *  bigger that band's share of the disc — density is a second, redundant
 *  encoding of the same number the opacity carries, because a chart read in a
 *  cockpit at a glance should not depend on judging one faint alpha. */
const RULE_SPACING_MIN_PX = 2.5;
const RULE_SPACING_MAX_PX = 7;
/** Constant: the share is already carried by colour and by rule density, and a
 *  third redundant encoding muddies both. */
const BAND_ALPHA = 0.85;

/** Colour for a cloud-top temperature, from the active theme's ramp.
 *
 *  Temperature — not height — because temperature is what the instrument
 *  measures; height is derived from it against a model profile. The ramp
 *  follows the enhanced-IR convention pilots already read on satellite
 *  imagery, except at the warm end, where the conventional grayscale is
 *  replaced by a desaturated blue: gray here is indistinguishable from the
 *  NWP cloud bands this layer exists to be compared against.
 *
 *  Nearest stop, never interpolated. A blended intermediate colour would imply
 *  a precision the 2 km retrieval does not have. */
export function shareColor(fraction: number): string {
  const stops = getActiveTheme().observed.shareStops;
  let best = stops[0];
  for (const stop of stops) {
    if (fraction >= stop[0]) best = stop;
  }
  return best[1];
}

/** Colour for a cloud-top temperature. Used by the MAP, where there is no
 *  altitude axis and temperature is genuinely new information. The
 *  cross-section colours by share instead — see `shareColor`. */
export function tempColor(celsius: number | null): string {
  const theme = getActiveTheme();
  if (celsius == null) return theme.observed.tempUnknown;
  let best = theme.observed.tempStops[0];
  let bestGap = Infinity;
  for (const stop of theme.observed.tempStops) {
    const gap = Math.abs(stop[0] - celsius);
    if (gap < bestGap) { bestGap = gap; best = stop; }
  }
  return best[1];
}

/** Bands worth drawing at this point, strongest-signal filter applied. */
export function significantBins(point: VizObservedPoint) {
  return point.topsBins.filter((b) => b.fraction >= MIN_BIN_FRACTION);
}

/** Contiguous runs of populated bands — the decks.
 *
 *  A gap between runs is a real, measured absence of cloud top, and it is the
 *  thing coarse bins destroyed: one station had decks at FL7-31, FL60-92 and
 *  FL302-370 with nothing between, rendered as slabs implying continuous cloud
 *  from the surface to FL150. Only the BASE of each run gets the "depth
 *  unknown" hatching, because that is the one edge where cloud genuinely
 *  continues below into air the satellite cannot see. */
export function bandRuns(bins: VizObservedTopBin[]): VizObservedTopBin[][] {
  const sorted = [...bins].sort((a, b) => a.loFt - b.loFt);
  const runs: VizObservedTopBin[][] = [];
  for (const bin of sorted) {
    const current = runs[runs.length - 1];
    const previous = current?.[current.length - 1];
    if (previous && Math.abs(bin.loFt - previous.hiFt) < 1) current.push(bin);
    else runs.push([bin]);
  }
  return runs;
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
    ctx.strokeStyle = getActiveTheme().observed.noCoverageColor;
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
  const theme = getActiveTheme();

  const bins = significantBins(point);
  // Normalised against this point's own busiest band, so the dominant deck
  // reads strongest whatever the absolute counts are. Fine bands hold a much
  // smaller share each than the old buckets did, and an absolute scale would
  // render every one of them uniformly faint.
  const peakFraction = bins.reduce((m, b) => Math.max(m, b.fraction), 0) || 1;

  ctx.save();
  for (const run of bandRuns(bins)) {
    for (const bin of run) {
      const yLo = transform.altitudeToY(bin.loFt);
      const yHi = transform.altitudeToY(bin.hiFt);
      if (yLo < plotArea.top) continue;  // above the chart; the arrow says so
      const top = Math.max(yHi, plotArea.top);
      const height = Math.max(1.5, yLo - top);
      const share = bin.fraction / peakFraction;
      // Colour carries the band's SHARE of the disc. The vertical axis already
      // says how high it is, and cloud-top temperature is nearly a function of
      // height, so colouring by temperature spent the channel on information
      // the reader already had. Share is what position cannot show. (The map
      // keeps the temperature ramp — no altitude axis there.)
      const bandColor = shareColor(bin.fraction);

      // Filled with HORIZONTAL RULES, not a solid block. A solid fill at an
      // altitude reads as a physical layer sitting there; this is a tally —
      // "this share of the tops in the disc fell in this band". Rules say
      // "counted" the way a solid says "substance", and they cannot be
      // confused with the diagonal hatching, which everywhere in this layer
      // means "unknown" (depth below a deck, or no retrieval at all).
      //
      // Density carries the share as well as opacity: a band holding most of
      // the pixels is closely ruled, a band holding a handful is sparse.
      ctx.globalAlpha = BAND_ALPHA;
      ctx.strokeStyle = bandColor;
      ctx.lineWidth = 1;
      const spacing = RULE_SPACING_MAX_PX - (RULE_SPACING_MAX_PX - RULE_SPACING_MIN_PX) * share;
      // Always at least one rule, so a single-pixel band is still visible —
      // it is often the coldest top on the chart.
      for (let y = top + 0.5; y < top + height; y += spacing) {
        ctx.beginPath();
        ctx.moveTo(x0, y);
        ctx.lineTo(x0 + width, y);
        ctx.stroke();
      }
      // A light edge so the band's extent stays legible when rules are sparse.
      ctx.globalAlpha = BAND_ALPHA * 0.6;
      ctx.strokeRect(x0, top, width, height);
    }

    // Only under the base of the deck: that is the one edge where cloud really
    // does continue down into air the satellite cannot see. Hatching under
    // every band would re-imply the continuous slab this change removes.
    const base = run[run.length - 1];
    const yBase = transform.altitudeToY(base.loFt);
    if (yBase >= plotArea.top) {
      ctx.globalAlpha = 0.45;
      ctx.strokeStyle = theme.observed.hatchColor;
      ctx.lineWidth = 1;
      for (let offset = 0; offset < width; offset += 4) {
        ctx.beginPath();
        ctx.moveTo(x0 + offset, yBase);
        ctx.lineTo(x0 + offset - HATCH_DEPTH_PX * 0.5, yBase + HATCH_DEPTH_PX);
        ctx.stroke();
      }
    }
  }
  ctx.restore();

  if (point.topsHighestFt == null) return;

  const color = point.topsMultiLayerFraction > 0.1
    ? theme.observed.capMultiLayerColor
    : theme.observed.capColor;
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
    //
    // Coloured by the SHARE of the disc whose tops are above the ceiling, the
    // same convention as every other band. It used to take the cap colour,
    // which put a single-value encoding next to a row of share-encoded bands
    // and made a box holding 2% of the disc look identical to one holding 90%.
    const ceilingFt = transform.yToAltitude(plotArea.top);
    const aboveShare = point.topsBins
      .filter((b) => b.loFt >= ceilingFt)
      .reduce((sum, b) => sum + b.fraction, 0);
    const boxColor = aboveShare > 0 ? shareColor(aboveShare) : color;

    ctx.save();
    const yBox = plotArea.top + 1;
    ctx.globalAlpha = 0.5;
    ctx.strokeStyle = boxColor;
    ctx.lineWidth = 1;
    for (let offset = 0; offset < width + ABOVE_SCALE_BOX_PX; offset += 4) {
      const sx = x0 + offset;
      ctx.beginPath();
      ctx.moveTo(Math.min(sx, x1), yBox);
      ctx.lineTo(Math.max(x0, sx - ABOVE_SCALE_BOX_PX), yBox + ABOVE_SCALE_BOX_PX);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = boxColor;
    ctx.strokeRect(x0, yBox, width, ABOVE_SCALE_BOX_PX);

    // Up arrow, centred. Keeps the cap colour: it marks the highest top, which
    // is a single value, not a share — and it must stay legible against
    // whatever share colour the box took.
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
