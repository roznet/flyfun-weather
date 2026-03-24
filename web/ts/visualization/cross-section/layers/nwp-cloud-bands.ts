/** NWP cloud layers: blue-tinted fills from model cloud parameterization.
 *
 * Renders server-computed NWP cloud layers (from GRIB diagnostics or
 * synthesized from Open-Meteo cloud % narrowed by DD envelope + inversions).
 * No client-side heuristic narrowing — boundaries come from the backend.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { drawColumnBand, hatchCloudBand, type BandPointData } from './base';
import { getActiveTheme } from '../theme';
import { renderMatchedZones } from './zone-matching';

/** Map coverage category to a representative percentage for fill opacity. */
function coverageToPct(coverage: string): number {
  switch (coverage) {
    case 'OVC': return 90;
    case 'BKN': return 65;
    case 'SCT': return 35;
    default: return 35;
  }
}

/** Blue-tinted fill from coverage percentage — distinct from DD cloud layers. */
function nwpCloudFill(pct: number): string {
  const theme = getActiveTheme().nwpClouds;
  const t = Math.min(1, Math.max(0, pct / 100));
  const [br, bg, bb] = theme.brightRgb;
  const [dr, dg, db] = theme.deltaRgb;
  const r = Math.round(br - dr * t);
  const g = Math.round(bg - dg * t);
  const b = Math.round(bb - db * t);
  const [opFloor, opScale] = theme.opacityRange;
  const opacity = Math.min(opFloor + opScale + 0.001, opFloor + opScale * t);
  return `rgba(${r}, ${g}, ${b}, ${opacity.toFixed(2)})`;
}

export const nwpCloudBandsLayer: CrossSectionLayer = {
  id: 'nwp-cloud-bands',
  name: 'NWP Layers',
  group: 'clouds',
  defaultEnabled: true,
  metricId: 'nwp_cloud_cover',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const hasData = data.points.some((p) => p.nwpCloudLayers.length > 0);
    if (!hasData) return;

    const hatch = getActiveTheme().clouds;

    // Single point fallback
    if (data.points.length === 1) {
      const p = data.points[0];
      for (const cl of p.nwpCloudLayers) {
        const pct = coverageToPct(cl.coverage);
        const fill = nwpCloudFill(pct);
        const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: cl.baseFt, top: cl.topFt }];
        drawColumnBand(ctx, bandPoints, transform, fill);
      }
      return;
    }

    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.nwpCloudLayers,
      getColor: (cl, matched) => {
        const avgPct = matched
          ? (coverageToPct(cl.coverage) + coverageToPct(matched.coverage)) / 2
          : coverageToPct(cl.coverage);
        return nwpCloudFill(avgPct);
      },
      onBand: (ctx, bandPoints, transform, cl) => {
        hatchCloudBand(ctx, bandPoints, transform, cl.coverage, hatch);
      },
    });
  },
};
