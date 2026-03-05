/** Temperature level lines: freezing (0°C), −10°C, −20°C. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { drawSmoothLine, type PointData } from './base';
import { getActiveTheme, type LineStyle } from '../theme';

function makeTemperatureLayer(
  id: string,
  name: string,
  styleKey: 'freezingLevel' | 'minus10c' | 'minus20c',
  accessor: (p: VizRouteData['points'][0]) => number | null,
  metricId?: string,
): CrossSectionLayer {
  return {
    id,
    name,
    group: 'temperature',
    defaultEnabled: true,
    metricId,
    render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
      const style: LineStyle = getActiveTheme().temperature[styleKey];
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

export const freezingLevelLayer = makeTemperatureLayer(
  'freezing-level', 'Freezing Level (0°C)', 'freezingLevel',
  (p) => p.altitudeLines.freezingLevelFt, 'freezing_level_ft',
);

export const minus10cLayer = makeTemperatureLayer(
  'minus-10c', '−10°C Level', 'minus10c',
  (p) => p.altitudeLines.minus10cLevelFt, 'minus10c_level_ft',
);

export const minus20cLayer = makeTemperatureLayer(
  'minus-20c', '−20°C Level', 'minus20c',
  (p) => p.altitudeLines.minus20cLevelFt, 'minus20c_level_ft',
);
