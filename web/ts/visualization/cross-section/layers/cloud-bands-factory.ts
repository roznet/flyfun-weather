/** Cloud bands layer factory — orthogonal source × style axes.
 *
 * Source picks the data feed and color metric:
 *   - 'dd'  → p.cloudLayers, color from dewpoint depression
 *   - 'nwp' → p.nwpCloudLayers, color from NWP cover %
 *
 * Style picks the painter:
 *   - 'layer'  → hatched fill (classic look)
 *   - 'soft'   → feathered gradient (GRAMET-like)
 *   - 'square' → solid rectangle, opacity from value (ForeFlight-like cells)
 *
 * Layer IDs preserve the existing combined naming so persisted prefs and
 * presets keep working: 'cloud-bands', 'nwp-cloud-bands', 'soft-cloud-bands',
 * 'soft-nwp-cloud-bands'. New square layers add 'square-cloud-bands' and
 * 'square-nwp-cloud-bands'.
 */

import type {
  CrossSectionLayer,
  CoordTransform,
  VizRouteData,
  VizCloudLayer,
  VizPoint,
} from '../../types';
import { cloudFillFromDD } from '../../scales';
import {
  drawColumnBand,
  drawSmoothBand,
  hatchCloudBand,
  type BandPointData,
} from './base';
import { getActiveTheme } from '../theme';
import { renderMatchedZones } from './zone-matching';

// --- Source axis ---

export type CloudSource = 'dd' | 'nwp';

interface SourceSpec {
  getZones: (p: VizPoint) => VizCloudLayer[];
  /** Continuous fill color for a (possibly matched) zone. Used by hatched and square styles. */
  matchedColor: (cl: VizCloudLayer, matched: VizCloudLayer | null) => string;
  metricId: string;
}

function avgDD(a: VizCloudLayer, b: VizCloudLayer | null): number | undefined {
  if (!b) return a.meanDewpointDepressionC;
  if (a.meanDewpointDepressionC !== undefined && b.meanDewpointDepressionC !== undefined) {
    return (a.meanDewpointDepressionC + b.meanDewpointDepressionC) / 2;
  }
  return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC;
}

function coverageToPct(coverage: string): number {
  switch (coverage.toUpperCase()) {
    case 'OVC': return 90;
    case 'BKN': return 65;
    case 'SCT': return 35;
    case 'FEW': return 15;
    default: return 35;
  }
}

const DD_SOURCE: SourceSpec = {
  getZones: (p) => p.cloudLayers,
  matchedColor: (cl, matched) => {
    const dd = avgDD(cl, matched);
    return cloudFillFromDD(dd, cl.coverage);
  },
  metricId: 'cloud_coverage',
};

const NWP_SOURCE: SourceSpec = {
  getZones: (p) => p.nwpCloudLayers ?? [],
  matchedColor: (cl, matched) => {
    const a = cl.meanCloudCoverPct ?? coverageToPct(cl.coverage);
    const b = matched ? (matched.meanCloudCoverPct ?? coverageToPct(matched.coverage)) : a;
    return nwpCloudFill((a + b) / 2);
  },
  metricId: 'nwp_cloud_cover',
};

/** Blue-tinted fill from coverage % — used by hatched-NWP for matched-zone color. */
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

const SOURCES: Record<CloudSource, SourceSpec> = { dd: DD_SOURCE, nwp: NWP_SOURCE };

// --- Style axis ---

export type CloudStyle = 'layer' | 'soft' | 'square';

type OnBand = (
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cl: VizCloudLayer,
  matched: VizCloudLayer | null,
  source: SourceSpec,
) => void;

const COVERAGE_ALPHA: Record<string, number> = {
  OVC: 0.85,
  BKN: 0.65,
  SCT: 0.45,
  FEW: 0.15,
};

const FEATHER_FRACTION = 0.15;

function paintHatched(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cl: VizCloudLayer,
): void {
  const hatch = getActiveTheme().clouds;
  hatchCloudBand(ctx, bandPoints, transform, cl.coverage, hatch);
}

function paintSoft(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cl: VizCloudLayer,
  matched: VizCloudLayer | null,
): void {
  if (bandPoints.length === 0) return;

  const theme = getActiveTheme();
  const softConfig = (theme as any).softClouds;
  const [r, g, b] = softConfig?.fillRgb ?? [255, 255, 255];
  const configAlpha = softConfig?.coverageAlpha ?? COVERAGE_ALPHA;
  const feather = softConfig?.featherFraction ?? FEATHER_FRACTION;

  const cov = cl.coverage?.toUpperCase() ?? 'BKN';
  const alpha = configAlpha[cov] ?? COVERAGE_ALPHA[cov] ?? 0.5;

  // Modulate by DD if available (use merged value if matched).
  const dd = matched ? avgDD(cl, matched) : cl.meanDewpointDepressionC;
  let ddFactor = 1.0;
  if (dd !== undefined) {
    ddFactor = Math.max(0.3, 1.0 - dd / 4.0);
  }

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

  ctx.save();
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

  drawSmoothBand(ctx, bandPoints, transform, grad as unknown as string);
  ctx.restore();
}

function paintSquare(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cl: VizCloudLayer,
  matched: VizCloudLayer | null,
  source: SourceSpec,
): void {
  // Reuse the same continuous color scale as the hatched style, but fill
  // a solid rectangle without the hatch overlay. Gives the same DD/cover%
  // hue+alpha modulation between layers, just cleaner cells.
  const fill = source.matchedColor(cl, matched);
  drawColumnBand(ctx, bandPoints, transform, fill);
}

const STYLES: Record<CloudStyle, OnBand> = {
  layer: (ctx, bp, t, cl) => paintHatched(ctx, bp, t, cl),
  soft: (ctx, bp, t, cl, matched) => paintSoft(ctx, bp, t, cl, matched),
  square: (ctx, bp, t, cl, matched, source) => paintSquare(ctx, bp, t, cl, matched, source),
};

// --- Factory ---

export interface CloudLayerSpec {
  id: string;
  name: string;
  source: CloudSource;
  style: CloudStyle;
  defaultEnabled?: boolean;
}

export function cloudLayer(spec: CloudLayerSpec): CrossSectionLayer {
  const source = SOURCES[spec.source];
  const style = STYLES[spec.style];

  return {
    id: spec.id,
    name: spec.name,
    group: 'clouds',
    defaultEnabled: spec.defaultEnabled ?? false,
    metricId: source.metricId,

    render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
      const hasData = data.points.some((p) => source.getZones(p).length > 0);
      if (!hasData) return;

      // Single-point fallback: column per zone (square style anyway).
      if (data.points.length === 1) {
        const p = data.points[0];
        for (const cl of source.getZones(p)) {
          const bandPoints: BandPointData[] = [
            { distance: p.distanceNm, base: cl.baseFt, top: cl.topFt },
          ];
          if (spec.style === 'soft') {
            paintSoft(ctx, bandPoints, transform, cl, null);
          } else if (spec.style === 'square') {
            paintSquare(ctx, bandPoints, transform, cl, null, source);
          } else {
            drawColumnBand(ctx, bandPoints, transform, source.matchedColor(cl, null));
          }
        }
        return;
      }

      renderMatchedZones(ctx, transform, data, {
        getZones: source.getZones,
        getColor: (cl, matched) =>
          spec.style === 'layer' ? source.matchedColor(cl, matched) : 'transparent',
        onBand: (ctx, bandPoints, transform, cl, matched) => {
          style(ctx, bandPoints, transform, cl, matched, source);
        },
      });
    },
  };
}

// --- Pre-built layer instances ---

export const cloudBandsLayer = cloudLayer({
  id: 'cloud-bands',
  name: 'DD Layers',
  source: 'dd',
  style: 'layer',
});

export const nwpCloudBandsLayer = cloudLayer({
  id: 'nwp-cloud-bands',
  name: 'NWP Layers',
  source: 'nwp',
  style: 'layer',
});

export const softCloudBandsLayer = cloudLayer({
  id: 'soft-cloud-bands',
  name: 'Soft DD',
  source: 'dd',
  style: 'soft',
});

export const softNwpCloudBandsLayer = cloudLayer({
  id: 'soft-nwp-cloud-bands',
  name: 'Soft NWP',
  source: 'nwp',
  style: 'soft',
  defaultEnabled: true,
});

export const squareCloudBandsLayer = cloudLayer({
  id: 'square-cloud-bands',
  name: 'Square DD',
  source: 'dd',
  style: 'square',
});

export const squareNwpCloudBandsLayer = cloudLayer({
  id: 'square-nwp-cloud-bands',
  name: 'Square NWP',
  source: 'nwp',
  style: 'square',
});

/** Lookup table: which layer id corresponds to a given (source, style) combo. */
export const CLOUD_LAYER_BY_AXES: Record<CloudSource, Record<CloudStyle, string>> = {
  dd: {
    layer: 'cloud-bands',
    soft: 'soft-cloud-bands',
    square: 'square-cloud-bands',
  },
  nwp: {
    layer: 'nwp-cloud-bands',
    soft: 'soft-nwp-cloud-bands',
    square: 'square-nwp-cloud-bands',
  },
};

export const ALL_CLOUD_LAYER_IDS: string[] = [
  'cloud-bands', 'soft-cloud-bands', 'square-cloud-bands',
  'nwp-cloud-bands', 'soft-nwp-cloud-bands', 'square-nwp-cloud-bands',
];

/** Parse a cloud layer id back into its (source, style) axes. Returns null if not a cloud layer. */
export function parseCloudLayerId(id: string): { source: CloudSource; style: CloudStyle } | null {
  for (const source of ['dd', 'nwp'] as CloudSource[]) {
    for (const style of ['layer', 'soft', 'square'] as CloudStyle[]) {
      if (CLOUD_LAYER_BY_AXES[source][style] === id) return { source, style };
    }
  }
  return null;
}
