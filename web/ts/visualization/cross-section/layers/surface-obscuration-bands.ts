/** Surface obscuration bands: diagonal-hatched fills for fog/LIFR signal.
 *
 * Diagonal hatching (rather than horizontal cloud-style) is a deliberate
 * semantic choice: fog is a *visibility* obstruction, not a cloud band.
 * The visual difference makes it obvious at a glance that a section
 * masked by this layer is not the same kind of weather as a stratus
 * deck above the airport.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizPoint, VizSurfaceObscuration } from '../../types';
import { getActiveTheme } from '../theme';
import { drawSmoothBand, buildBandPath, type BandPointData } from './base';

type ObscurationTheme = ReturnType<typeof getActiveTheme>['obscuration'];

function severityColor(theme: ObscurationTheme, severity: VizSurfaceObscuration['severity']): string {
  return theme[severity];
}

export const surfaceObscurationBandsLayer: CrossSectionLayer = {
  id: 'surface-obscuration-bands',
  name: 'Surface obscuration',
  group: 'obscuration',
  defaultEnabled: false,
  metricId: 'visibility_m',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const obsPoints = data.points.filter((p) => p.surfaceObscuration !== null);
    if (obsPoints.length === 0) return;

    const theme = getActiveTheme().obscuration;

    // Group adjacent points with the same severity into runs so the
    // smooth-band primitive can render a continuous band per run. A
    // change in severity ends the current run.
    const runs: VizPoint[][] = [];
    let current: VizPoint[] = [];
    let currentSeverity: VizSurfaceObscuration['severity'] | null = null;

    for (const p of data.points) {
      const sev = p.surfaceObscuration?.severity ?? null;
      if (sev === null) {
        if (current.length) { runs.push(current); current = []; }
        currentSeverity = null;
        continue;
      }
      if (currentSeverity !== null && sev !== currentSeverity) {
        runs.push(current);
        current = [];
      }
      current.push(p);
      currentSeverity = sev;
    }
    if (current.length) runs.push(current);

    for (const run of runs) {
      const severity = run[0].surfaceObscuration!.severity;
      const bandPoints: BandPointData[] = run.map((p) => ({
        distance: p.distanceNm,
        base: p.surfaceObscuration!.baseFt,
        top: p.surfaceObscuration!.topFt,
      }));

      drawSmoothBand(ctx, bandPoints, transform, severityColor(theme, severity));
      drawDiagonalHatch(ctx, bandPoints, transform, theme);
    }
  },
};

/** Draw 45° hatching inside the smooth-band path, snapped to a global
 *  grid so adjacent runs align. Uses the same Fritsch-Carlson spline
 *  geometry as `drawSmoothBand` for the clip path so hatch lines never
 *  bleed past the smooth fill at curve peaks/troughs. */
function drawDiagonalHatch(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  theme: ObscurationTheme,
): void {
  const valid = bandPoints.filter((p) => p.base !== null && p.top !== null);
  if (valid.length < 2) return;

  ctx.save();
  buildBandPath(ctx, valid, transform);
  ctx.clip();

  ctx.strokeStyle = theme.hatchColor;
  ctx.lineWidth = theme.hatchLineWidth;
  ctx.setLineDash([]);

  const xs = valid.map((p) => transform.distanceToX(p.distance));
  const baseYs = valid.map((p) => transform.altitudeToY(p.base!));
  const topYs = valid.map((p) => transform.altitudeToY(p.top!));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...topYs);
  const maxY = Math.max(...baseYs);

  // Diagonal lines y = x + c. Iterate over c on a fixed grid so adjacent
  // runs align. Spacing is along the perpendicular to the lines, which
  // for 45° equals spacingPx / √2 along c, but rounding to the grid in
  // pixel space is good enough visually.
  const spacing = Math.max(2, theme.hatchSpacingPx);
  const cMin = minY - maxX;
  const cMax = maxY - minX;
  const startC = Math.ceil(cMin / spacing) * spacing;
  for (let c = startC; c <= cMax; c += spacing) {
    // Line: y = x + c, clipped to bounding box.
    const x0 = Math.max(minX, minY - c);
    const x1 = Math.min(maxX, maxY - c);
    if (x1 <= x0) continue;
    ctx.beginPath();
    ctx.moveTo(x0, x0 + c);
    ctx.lineTo(x1, x1 + c);
    ctx.stroke();
  }

  ctx.restore();
}
