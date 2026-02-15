/** Temperature level lines: freezing (0°C), −10°C, −20°C. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { drawSmoothLine, drawColumnLine, type PointData } from './base';

function makeTemperatureLayer(
  id: string,
  name: string,
  color: string,
  width: number,
  dash: number[] | undefined,
  accessor: (p: VizRouteData['points'][0]) => number | null,
  metricId?: string,
): CrossSectionLayer {
  return {
    id,
    name,
    group: 'temperature',
    defaultEnabled: true,
    metricId,
    render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
      const points: PointData[] = data.points.map((p) => ({
        distance: p.distanceNm,
        value: accessor(p),
      }));

      const drawFn = mode === 'smooth' ? drawSmoothLine : drawColumnLine;
      drawFn(ctx, points, transform, { color, width, dash });
    },
  };
}

export const freezingLevelLayer = makeTemperatureLayer(
  'freezing-level', 'Freezing Level (0°C)', '#00bcd4', 2, undefined,
  (p) => p.altitudeLines.freezingLevelFt, 'freezing_level_ft',
);

export const minus10cLayer = makeTemperatureLayer(
  'minus-10c', '−10°C Level', '#2196f3', 1.5, undefined,
  (p) => p.altitudeLines.minus10cLevelFt, 'minus10c_level_ft',
);

export const minus20cLayer = makeTemperatureLayer(
  'minus-20c', '−20°C Level', '#1a237e', 1, [6, 4],
  (p) => p.altitudeLines.minus20cLevelFt, 'minus20c_level_ft',
);
