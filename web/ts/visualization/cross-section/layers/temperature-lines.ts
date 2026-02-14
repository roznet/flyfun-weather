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
): CrossSectionLayer {
  return {
    id,
    name,
    group: 'temperature',
    defaultEnabled: true,
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
  (p) => p.altitudeLines.freezingLevelFt,
);

export const minus10cLayer = makeTemperatureLayer(
  'minus-10c', '−10°C Level', '#2196f3', 1.5, undefined,
  (p) => p.altitudeLines.minus10cLevelFt,
);

export const minus20cLayer = makeTemperatureLayer(
  'minus-20c', '−20°C Level', '#1a237e', 1, [6, 4],
  (p) => p.altitudeLines.minus20cLevelFt,
);
