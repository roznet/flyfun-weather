/** Generic zone-matching renderer for band layers.
 *
 * Matches zones between adjacent route points by altitude overlap,
 * then draws smooth bands for matched pairs and tapered bands for unmatched zones.
 * Eliminates the duplicated matching pattern across icing, cloud, CAT, and inversion layers.
 */

import type { CoordTransform, VizRouteData, VizPoint } from '../../types';
import { drawSmoothBand, type BandPointData } from './base';

/** Minimum interface a zone must satisfy for matching. */
export interface MatchableZone {
  baseFt: number;
  topFt: number;
}

/** Configuration for the zone-matching renderer. */
export interface ZoneMatchConfig<T extends MatchableZone> {
  /** Extract the zone array from a VizPoint. */
  getZones: (point: VizPoint) => T[];
  /** Compute the fill color for a zone and its optional match partner. */
  getColor: (zone: T, matched: T | null) => string;
  /** Optional callback after each band segment is drawn (e.g., for hatching). */
  onBand?: (
    ctx: CanvasRenderingContext2D,
    bandPoints: BandPointData[],
    transform: CoordTransform,
    zone: T,
    matched: T | null,
  ) => void;
}

/** Render matched zone bands between adjacent route points. */
export function renderMatchedZones<T extends MatchableZone>(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
  config: ZoneMatchConfig<T>,
): void {
  const { getZones, getColor, onBand } = config;

  for (let i = 0; i < data.points.length - 1; i++) {
    const curr = data.points[i];
    const next = data.points[i + 1];
    const currZones = getZones(curr);
    const nextZones = getZones(next);
    const usedNext = new Set<number>();

    for (const cz of currZones) {
      // Find best overlap match in next point
      let bestIdx = -1;
      let bestOverlap = 0;
      for (let j = 0; j < nextZones.length; j++) {
        if (usedNext.has(j)) continue;
        const overlap = Math.min(cz.topFt, nextZones[j].topFt) -
                         Math.max(cz.baseFt, nextZones[j].baseFt);
        if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
      }

      if (bestIdx >= 0) {
        usedNext.add(bestIdx);
        const nz = nextZones[bestIdx];
        const fill = getColor(cz, nz);
        const bandPoints: BandPointData[] = [
          { distance: curr.distanceNm, base: cz.baseFt, top: cz.topFt },
          { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
        ];
        drawSmoothBand(ctx, bandPoints, transform, fill);
        onBand?.(ctx, bandPoints, transform, cz, nz);
      } else {
        const midDist = (curr.distanceNm + next.distanceNm) / 2;
        const midAlt = (cz.baseFt + cz.topFt) / 2;
        const fill = getColor(cz, null);
        const bandPoints: BandPointData[] = [
          { distance: curr.distanceNm, base: cz.baseFt, top: cz.topFt },
          { distance: midDist, base: midAlt, top: midAlt },
        ];
        drawSmoothBand(ctx, bandPoints, transform, fill);
        onBand?.(ctx, bandPoints, transform, cz, null);
      }
    }

    // Unmatched next zones: appear from midpoint
    for (let j = 0; j < nextZones.length; j++) {
      if (usedNext.has(j)) continue;
      const nz = nextZones[j];
      const midDist = (curr.distanceNm + next.distanceNm) / 2;
      const midAlt = (nz.baseFt + nz.topFt) / 2;
      const fill = getColor(nz, null);
      const bandPoints: BandPointData[] = [
        { distance: midDist, base: midAlt, top: midAlt },
        { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
      onBand?.(ctx, bandPoints, transform, nz, null);
    }
  }
}

/** Risk severity ordering for max-risk computation. */
const RISK_ORDER = ['none', 'light', 'moderate', 'severe'];

/** Return the higher of two risk levels. */
export function maxRisk(a: string, b: string): string {
  return RISK_ORDER.indexOf(a) >= RISK_ORDER.indexOf(b) ? a : b;
}
