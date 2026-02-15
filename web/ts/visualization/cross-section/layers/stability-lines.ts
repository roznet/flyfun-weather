/** Stability lines: LCL, LFC, EL (convective assessment levels). */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { drawSmoothLine, drawColumnLine, type PointData } from './base';

function makeStabilityLayer(
  id: string,
  name: string,
  color: string,
  width: number,
  accessor: (p: VizRouteData['points'][0]) => number | null,
  metricId?: string,
): CrossSectionLayer {
  return {
    id,
    name,
    group: 'stability',
    defaultEnabled: true,
    metricId,
    render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
      const points: PointData[] = data.points.map((p) => ({
        distance: p.distanceNm,
        value: accessor(p),
      }));

      const drawFn = mode === 'smooth' ? drawSmoothLine : drawColumnLine;
      drawFn(ctx, points, transform, { color, width, dash: [6, 4] });
    },
  };
}

export const lclLayer = makeStabilityLayer(
  'lcl', 'LCL', '#4caf50', 2,
  (p) => p.altitudeLines.lclAltitudeFt, 'lcl_altitude_ft',
);

export const lfcLayer = makeStabilityLayer(
  'lfc', 'LFC', '#ff9800', 1.5,
  (p) => p.altitudeLines.lfcAltitudeFt, 'lfc_altitude_ft',
);

export const elLayer = makeStabilityLayer(
  'el', 'EL', '#f44336', 1.5,
  (p) => p.altitudeLines.elAltitudeFt, 'el_altitude_ft',
);
