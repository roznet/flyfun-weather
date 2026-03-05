/** Stability lines: LCL, LFC, EL (convective assessment levels). */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { drawSmoothLine, type PointData } from './base';
import { getActiveTheme, type LineStyle } from '../theme';

function makeStabilityLayer(
  id: string,
  name: string,
  styleKey: 'lcl' | 'lfc' | 'el',
  accessor: (p: VizRouteData['points'][0]) => number | null,
  metricId?: string,
): CrossSectionLayer {
  return {
    id,
    name,
    group: 'stability',
    defaultEnabled: true,
    metricId,
    render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
      const style: LineStyle = getActiveTheme().stability[styleKey];
      const points: PointData[] = data.points.map((p) => ({
        distance: p.distanceNm,
        value: accessor(p),
      }));

      drawSmoothLine(ctx, points, transform, {
        color: style.color,
        width: style.width,
        dash: style.dash,
      });
    },
  };
}

export const lclLayer = makeStabilityLayer(
  'lcl', 'LCL', 'lcl',
  (p) => p.altitudeLines.lclAltitudeFt, 'lcl_altitude_ft',
);

export const lfcLayer = makeStabilityLayer(
  'lfc', 'LFC', 'lfc',
  (p) => p.altitudeLines.lfcAltitudeFt, 'lfc_altitude_ft',
);

export const elLayer = makeStabilityLayer(
  'el', 'EL', 'el',
  (p) => p.altitudeLines.elAltitudeFt, 'el_altitude_ft',
);
