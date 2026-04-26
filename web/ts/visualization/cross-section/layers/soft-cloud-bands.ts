/** Soft cloud bands: gradient-edge fills with coverage-proportional opacity.
 *
 * Replaces hatching with smooth, feathered fills for a GRAMET-like look.
 * Two exported layers: one for DD-derived clouds, one for NWP clouds.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizCloudLayer } from '../../types';
import { getActiveTheme } from '../theme';
import { renderMatchedZones } from './zone-matching';
import type { BandPointData } from './base';
import { drawSmoothBand } from './base';

// Coverage to opacity mapping (METAR categories, uppercase to match .toUpperCase() lookup)
const COVERAGE_ALPHA: Record<string, number> = {
  OVC: 0.85,
  BKN: 0.65,
  SCT: 0.45,
  FEW: 0.15,
};

/** Feather fraction: top/bottom 15% of band height fades in/out. */
const FEATHER_FRACTION = 0.15;

function softCloudFill(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cloud: VizCloudLayer,
): void {
  if (bandPoints.length === 0) return;

  const theme = getActiveTheme();
  const softConfig = (theme as any).softClouds;
  const [r, g, b] = softConfig?.fillRgb ?? [255, 255, 255];
  const configAlpha = softConfig?.coverageAlpha ?? COVERAGE_ALPHA;
  const feather = softConfig?.featherFraction ?? FEATHER_FRACTION;

  // Determine alpha from coverage
  const cov = cloud.coverage?.toUpperCase() ?? 'BKN';
  const alpha = configAlpha[cov] ?? COVERAGE_ALPHA[cov] ?? 0.5;

  // Modulate by dewpoint depression if available (denser cloud = slightly darker)
  let ddFactor = 1.0;
  if (cloud.meanDewpointDepressionC !== undefined) {
    ddFactor = Math.max(0.3, 1.0 - cloud.meanDewpointDepressionC / 4.0);
  }

  // Find bounding box Y for the gradient
  let minY = Infinity, maxY = -Infinity;
  for (const bp of bandPoints) {
    if (bp.top == null || bp.base == null) continue;
    const yTop = transform.altitudeToY(bp.top);
    const yBase = transform.altitudeToY(bp.base);
    minY = Math.min(minY, yTop, yBase);
    maxY = Math.max(maxY, yTop, yBase);
  }

  const bandHeight = maxY - minY;
  if (bandHeight <= 0) return;

  const featherPx = Math.max(2, bandHeight * feather);

  // Build the band path (same as drawSmoothBand but we need to clip)
  ctx.save();

  // Create vertical gradient with feathered edges
  const grad = ctx.createLinearGradient(0, minY, 0, maxY);
  const fillAlpha = alpha * ddFactor;
  const color = `rgba(${r}, ${g}, ${b}, ${fillAlpha})`;
  const transparent = `rgba(${r}, ${g}, ${b}, 0)`;

  const topStop = Math.min(featherPx / bandHeight, 0.3);
  const botStop = Math.max(1.0 - featherPx / bandHeight, 0.7);

  grad.addColorStop(0, transparent);
  grad.addColorStop(topStop, color);
  grad.addColorStop(botStop, color);
  grad.addColorStop(1, transparent);

  // Draw the band with the gradient fill
  drawSmoothBand(ctx, bandPoints, transform, grad as unknown as string);

  ctx.restore();
}

function avgDD(a: VizCloudLayer, b: VizCloudLayer): number | undefined {
  if (a.meanDewpointDepressionC !== undefined && b.meanDewpointDepressionC !== undefined) {
    return (a.meanDewpointDepressionC + b.meanDewpointDepressionC) / 2;
  }
  return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC;
}

export const softCloudBandsLayer: CrossSectionLayer = {
  id: 'soft-cloud-bands',
  name: 'Soft DD',
  group: 'clouds',
  defaultEnabled: false,
  metricId: 'soft_cloud_dd',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const maxLayers = data.points.reduce((max, p) => Math.max(max, p.cloudLayers.length), 0);
    if (maxLayers === 0) return;

    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.cloudLayers,
      getColor: () => 'transparent',
      onBand: (ctx, bandPoints, transform, cl, matched) => {
        const merged: VizCloudLayer = matched
          ? { ...cl, meanDewpointDepressionC: avgDD(cl, matched) }
          : cl;
        softCloudFill(ctx, bandPoints, transform, merged);
      },
    });
  },
};

export const softNwpCloudBandsLayer: CrossSectionLayer = {
  id: 'soft-nwp-cloud-bands',
  name: 'Soft NWP',
  group: 'clouds',
  defaultEnabled: true,
  metricId: 'soft_cloud_nwp',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const maxLayers = data.points.reduce((max, p) => Math.max(max, (p.nwpCloudLayers ?? []).length), 0);
    if (maxLayers === 0) return;

    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.nwpCloudLayers ?? [],
      getColor: () => 'transparent',
      onBand: (ctx, bandPoints, transform, cl) => {
        softCloudFill(ctx, bandPoints, transform, cl);
      },
    });
  },
};
