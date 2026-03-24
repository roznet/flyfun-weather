/** Cloud layer bands: gray-gradient fills from dewpoint depression (sounding-derived). */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizCloudLayer } from '../../types';
import { cloudFillFromDD } from '../../scales';
import { drawColumnBand, hatchCloudBand, type BandPointData } from './base';
import { getActiveTheme } from '../theme';
import { renderMatchedZones } from './zone-matching';

export const cloudBandsLayer: CrossSectionLayer = {
  id: 'cloud-bands',
  name: 'DD Layers',
  group: 'clouds',
  defaultEnabled: true,
  metricId: 'cloud_coverage',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const maxLayers = data.points.reduce((max, p) => Math.max(max, p.cloudLayers.length), 0);
    if (maxLayers === 0) return;

    const hatch = getActiveTheme().clouds;

    // Single-point fallback: column bands
    if (data.points.length === 1) {
      const p = data.points[0];
      for (const cl of p.cloudLayers) {
        const fill = cloudFillFromDD(cl.meanDewpointDepressionC, cl.coverage);
        const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: cl.baseFt, top: cl.topFt }];
        drawColumnBand(ctx, bandPoints, transform, fill);
      }
      return;
    }

    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.cloudLayers,
      getColor: (cl, matched) => {
        const dd = matched ? avgDD(cl, matched) : cl.meanDewpointDepressionC;
        return cloudFillFromDD(dd, cl.coverage);
      },
      onBand: (ctx, bandPoints, transform, cl) => {
        hatchCloudBand(ctx, bandPoints, transform, cl.coverage, hatch);
      },
    });
  },
};

/** Average DD of two cloud layers; falls back to undefined if both missing. */
function avgDD(a: VizCloudLayer, b: VizCloudLayer): number | undefined {
  if (a.meanDewpointDepressionC !== undefined && b.meanDewpointDepressionC !== undefined) {
    return (a.meanDewpointDepressionC + b.meanDewpointDepressionC) / 2;
  }
  return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC;
}
