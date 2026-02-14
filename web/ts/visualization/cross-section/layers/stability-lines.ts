/** Stability lines: LCL, LFC, EL (convective assessment levels). */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { drawSmoothLine, drawColumnLine, type PointData } from './base';

function makeStabilityLayer(
  id: string,
  name: string,
  color: string,
  width: number,
  accessor: (p: VizRouteData['points'][0]) => number | null,
): CrossSectionLayer {
  return {
    id,
    name,
    group: 'stability',
    defaultEnabled: true,
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
  (p) => p.altitudeLines.lclAltitudeFt,
);

export const lfcLayer = makeStabilityLayer(
  'lfc', 'LFC', '#ff9800', 1.5,
  (p) => p.altitudeLines.lfcAltitudeFt,
);

export const elLayer = makeStabilityLayer(
  'el', 'EL', '#f44336', 1.5,
  (p) => p.altitudeLines.elAltitudeFt,
);
