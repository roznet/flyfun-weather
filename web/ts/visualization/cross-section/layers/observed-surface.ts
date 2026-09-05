/** Observed radar echo and lightning at the surface (#574) — group
 * `conditions`, default OFF.
 *
 * Sibling of the existing `current-conditions` layer (METAR columns + SIGMET
 * zones), which is untouched: that one draws point reports and airspace
 * notices, this one draws remotely-sensed fields. They stack cleanly because
 * they occupy different parts of the column — METAR columns rise from the
 * terrain, this strip hugs it.
 *
 * Reflectivity is drawn as a colour strip along the terrain rather than as a
 * vertical extent, because the composite is a 2-D surface product: it says
 * *there is an echo here*, not how tall it is. Drawing it with height would
 * invent structure the data does not contain — the cloud-top layer is where
 * vertical information legitimately comes from.
 *
 * Lightning is drawn as tick marks whose count reflects flash density in the
 * selected corridor, above the echo strip.
 */

import type { CrossSectionLayer, CoordTransform, VizObservedPoint } from '../../types';
import { ageBadgeText, drawBadge } from './observed-tops';
import { observationWindowText } from '../../observed-time';

const STRIP_HEIGHT_PX = 10;
const MARK_HALF_WIDTH_NM = 4;
const FLASH_TICK_HEIGHT_PX = 9;
const MAX_FLASH_TICKS = 4;

/** dBZ → strip colour.
 *
 * Mirrors `_DBZ_STOPS` in `observed/imagery.py` stop for stop, so the map
 * overlay and the cross-section strip cannot disagree about what a given
 * reflectivity looks like. The 65 dBZ magenta was missing here while the
 * server had it, so the most intense echo on the map rendered as ordinary red
 * on the cross-section — the one case where the difference matters most.
 * Keep the two lists in step. */
export function echoColor(dbz: number): string {
  if (dbz >= 65) return '#be3cbe';
  if (dbz >= 55) return '#e13c3c';
  if (dbz >= 45) return '#f08c28';
  if (dbz >= 35) return '#f0d23c';
  if (dbz >= 20) return '#3cbe5a';
  return '#5aa0dc';
}

/** How many flash ticks to draw for a disc's flash count. */
export function flashTickCount(flashCount: number): number {
  if (flashCount <= 0) return 0;
  return Math.min(MAX_FLASH_TICKS, 1 + Math.floor(Math.log2(flashCount)));
}

export const observedSurfaceLayer: CrossSectionLayer = {
  id: 'observed-surface',
  name: 'Observed radar & lightning',
  group: 'conditions',
  defaultEnabled: false,
  metricId: 'observed_surface',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data) {
    if (data.timeAxisMode) return;
    const observed = data.observed;
    if (!observed) return;
    if (!observed.reflectivity && !observed.lightning) return;

    for (const point of observed.points) {
      drawEcho(ctx, transform, point, data.terrainProfile);
      drawFlashes(ctx, transform, point, data.terrainProfile);
    }

    // Radar and lightning frames are minutes apart and neither is an instant;
    // whichever is on screen says so for itself — including which one it is.
    // The label must come from the source actually supplying the timestamp: on
    // a briefing where OPERA is down but lightning is up, a hardcoded 'Radar'
    // would stamp a radar name on a lightning frame's age, which is exactly
    // the per-source blending this layer exists to avoid.
    const sources = [observed.reflectivity, observed.lightning].filter(s => s != null);
    for (const [index, source] of sources.entries()) {
      // Second row whenever the cloud-top layer is also drawing a badge, so
      // neither source's age is hidden behind the other.
      const row = (observed.cloudTops ? 1 : 0) + index;
      drawBadge(
        ctx,
        transform,
        ageBadgeText(source.validTime, source.ageMinutes, source.label)
          + observationWindowText(source.source, source.windowMinutes),
        row,
      );
    }
  },
};

function terrainAt(
  terrain: Array<{ distanceNm: number; elevationFt: number }> | null,
  distanceNm: number,
): number {
  if (!terrain || terrain.length === 0) return 0;
  let best = terrain[0];
  for (const p of terrain) {
    if (Math.abs(p.distanceNm - distanceNm) < Math.abs(best.distanceNm - distanceNm)) best = p;
  }
  return best.elevationFt;
}

function drawEcho(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  point: VizObservedPoint,
  terrain: Array<{ distanceNm: number; elevationFt: number }> | null,
): void {
  const x0 = transform.distanceToX(point.distanceNm - MARK_HALF_WIDTH_NM);
  const x1 = transform.distanceToX(point.distanceNm + MARK_HALF_WIDTH_NM);
  const width = Math.max(2, x1 - x0);
  const yBase = transform.altitudeToY(terrainAt(terrain, point.distanceNm));

  if (point.radarNoCoverage) {
    // A hatched strip: the radar does not see here. Distinct from a blank
    // strip, which is the radar looking and finding nothing.
    ctx.save();
    ctx.strokeStyle = 'rgba(107, 114, 128, 0.7)';
    ctx.lineWidth = 1;
    for (let offset = 0; offset < width; offset += 4) {
      ctx.beginPath();
      ctx.moveTo(x0 + offset, yBase);
      ctx.lineTo(x0 + offset + 3, yBase - STRIP_HEIGHT_PX);
      ctx.stroke();
    }
    ctx.restore();
  }
  if (point.dbz == null) return;

  ctx.save();
  ctx.globalAlpha = 0.75;
  ctx.fillStyle = echoColor(point.dbz);
  ctx.fillRect(x0, yBase - STRIP_HEIGHT_PX, width, STRIP_HEIGHT_PX);
  ctx.restore();
}

function drawFlashes(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  point: VizObservedPoint,
  terrain: Array<{ distanceNm: number; elevationFt: number }> | null,
): void {
  const ticks = flashTickCount(point.flashCount);
  if (ticks === 0) return;
  const cx = transform.distanceToX(point.distanceNm);
  const yTop = transform.altitudeToY(terrainAt(terrain, point.distanceNm)) - STRIP_HEIGHT_PX - 3;

  ctx.save();
  ctx.strokeStyle = '#7c3aed';
  ctx.lineWidth = 1.6;
  for (let i = 0; i < ticks; i++) {
    const x = cx + (i - (ticks - 1) / 2) * 3;
    ctx.beginPath();
    ctx.moveTo(x, yTop);
    ctx.lineTo(x, yTop - FLASH_TICK_HEIGHT_PX);
    ctx.stroke();
  }
  ctx.restore();
}
