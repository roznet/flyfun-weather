/** Cloud bands layer factory — orthogonal source × style axes.
 *
 * Source picks the data feed and color metric:
 *   - 'dd'  → p.cloudLayers, color from dewpoint depression
 *   - 'nwp' → p.nwpCloudLayers, color from NWP cover %
 *
 * Style picks the painter:
 *   - 'natural' → cluster of overlapping soft elliptical blobs, painted
 *                 across the full band. Defaults yield a continuous puffy
 *                 band; coverage is encoded by the source's colour/alpha
 *                 (DD: gray↔white + per-coverage alpha; NWP: brightness +
 *                 opacity by cover%). Knobs (fillFraction,
 *                 radiusCoverageScaleFloor, fillAlpha) can re-introduce
 *                 horizontal sparseness if desired.
 *   - 'soft'    → feathered vertical-gradient fill.
 *   - 'square'  → solid rectangle, opacity from value (ForeFlight-like cells).
 *
 * Layer IDs preserve the existing combined naming so persisted prefs and
 * presets keep working: 'cloud-bands', 'nwp-cloud-bands', 'soft-cloud-bands',
 * 'soft-nwp-cloud-bands'. Square layers add 'square-cloud-bands' and
 * 'square-nwp-cloud-bands'.
 */

import type {
  CrossSectionLayer,
  CoordTransform,
  VizRouteData,
  VizCloudLayer,
  VizPoint,
} from '../../types';
import { cloudFillFromDD, nwpCloudFill } from '../../scales';
import {
  drawColumnBand,
  drawSmoothBand,
  type BandPointData,
} from './base';
import { getActiveTheme } from '../theme';
import { renderMatchedZones } from './zone-matching';

// --- Source axis ---

export type CloudSource = 'dd' | 'nwp';

interface SourceSpec {
  /** Identifier mixed into per-band hashes so DD and NWP bands at the same
   *  base altitude don't draw identical puff/gap patterns. */
  key: CloudSource;
  getZones: (p: VizPoint) => VizCloudLayer[];
  /** Continuous fill color for a (possibly matched) zone. Used by natural and square styles. */
  matchedColor: (cl: VizCloudLayer, matched: VizCloudLayer | null) => string;
}

function avgDD(a: VizCloudLayer, b: VizCloudLayer | null): number | undefined {
  if (!b) return a.meanDewpointDepressionC;
  if (a.meanDewpointDepressionC !== undefined && b.meanDewpointDepressionC !== undefined) {
    return (a.meanDewpointDepressionC + b.meanDewpointDepressionC) / 2;
  }
  return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC;
}

export function coverageToPct(coverage: string): number {
  switch (coverage.toUpperCase()) {
    case 'OVC': return 90;
    case 'BKN': return 65;
    case 'SCT': return 35;
    case 'FEW': return 15;
    default: return 35;
  }
}

const DD_SOURCE: SourceSpec = {
  key: 'dd',
  getZones: (p) => p.cloudLayers,
  matchedColor: (cl, matched) => {
    const dd = avgDD(cl, matched);
    return cloudFillFromDD(dd, cl.coverage);
  },
};

const NWP_SOURCE: SourceSpec = {
  key: 'nwp',
  getZones: (p) => p.nwpCloudLayers ?? [],
  matchedColor: (cl, matched) => {
    const covA = cl.meanCloudCoverPct ?? coverageToPct(cl.coverage);
    const covB = matched ? (matched.meanCloudCoverPct ?? coverageToPct(matched.coverage)) : covA;
    return nwpCloudFill((covA + covB) / 2);
  },
};

const SOURCES: Record<CloudSource, SourceSpec> = { dd: DD_SOURCE, nwp: NWP_SOURCE };

// --- Style axis ---

export type CloudStyle = 'natural' | 'soft' | 'square';

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

// --- Natural cloud config (tunable) ---
//
// The natural style paints each band as a row of overlapping soft elliptical
// blobs. Each blob is a canvas radial gradient (wide horizontally, narrow
// vertically) with alpha falling off from a defined core to fully
// transparent at the edge. Coverage drives the probability that each slot
// gets a blob — OVC fills every slot (heavy overlap, continuous cloud), SCT
// fills ~45% (visible sky gaps), FEW leaves isolated wisps.

export interface NaturalCloudConfig {
  /** Target horizontal fill fraction per METAR coverage class (DD source).
   *  NWP source uses `meanCloudCoverPct / 100` directly. */
  fillFraction: { FEW: number; SCT: number; BKN: number; OVC: number };
  /** Floor on fill fraction (so an NWP cover% of 5% still shows a wisp). */
  minFillFraction: number;
  /** Fixed alpha applied to the source color. Coverage lives in slot
   *  density, not opacity — avoids double-encoding SCT as "fewer blobs AND
   *  lower alpha". Set to null to keep the source's coverage-modulated alpha. */
  fillAlpha: number | null;
  /** Horizontal spacing between blob slots along the route, in px. */
  blobSpacingPx: number;
  /** Nominal horizontal blob radius, in px. Should exceed blobSpacingPx so
   *  adjacent filled slots overlap into a continuous cloud at high coverage. */
  blobRadiusXPx: number;
  /** Blob vertical radius as fraction of band height. */
  blobHeightFraction: number;
  /** Per-blob x position jitter as fraction of slot spacing. */
  blobJitterX: number;
  /** Per-blob y center jitter as fraction of band height. */
  blobJitterY: number;
  /** Per-blob size variation (multiplier range = 1 ± variation/2). */
  blobSizeVariation: number;
  /** Radial-gradient stop where alpha starts fading (0..1). Larger → harder
   *  cloud cores; smaller → wispier blobs. */
  coreFraction: number;
  /** Sub-blobs per slot. Each filled slot draws this many overlapping
   *  radial-gradient blobs at jittered offsets — the union forms a lumpy,
   *  non-circular cloud silhouette. 1 → single ellipse (no deformation). */
  subBlobsPerSlot: number;
  /** Max sub-blob offset from cluster centre, as fraction of blob radius. */
  subBlobOffsetFraction: number;
  /** Sub-blob nominal size as fraction of main blob radius. */
  subBlobSizeFraction: number;
  /** Sub-blob size variation around the nominal fraction (additive jitter). */
  subBlobSizeJitter: number;
  /** Floor multiplier for coverage-driven blob shrinkage. Effective blob
   *  radius = nominal * (floor + (1-floor) * fillFraction). At fillFraction=1
   *  (OVC) blobs stay at full size and overlap into a continuous blanket; at
   *  low coverage (SCT/FEW) blobs shrink so visible sky shows between
   *  surviving clusters. Set to 1.0 to disable coverage-driven shrinkage. */
  radiusCoverageScaleFloor: number;
}

// Defaults paint a continuous puff band: every slot filled, full-size blobs
// regardless of coverage. Coverage is encoded via colour/alpha from the source
// (DD: gray↔white + per-coverage alpha; NWP: brightness + opacity by cover%),
// not by horizontal sparseness. Lower fillFraction / radiusCoverageScaleFloor
// to bring back the SCT-gaps look.
export const DEFAULT_NATURAL_CONFIG: NaturalCloudConfig = {
  fillFraction: { FEW: 1.0, SCT: 1.0, BKN: 1.0, OVC: 1.0 },
  minFillFraction: 1.0,
  fillAlpha: null,
  blobSpacingPx: 28,
  blobRadiusXPx: 36,
  blobHeightFraction: 0.6,
  blobJitterX: 0.4,
  blobJitterY: 0.15,
  blobSizeVariation: 0.5,
  coreFraction: 0.3,
  subBlobsPerSlot: 3,
  subBlobOffsetFraction: 0.35,
  subBlobSizeFraction: 0.65,
  subBlobSizeJitter: 0.25,
  radiusCoverageScaleFloor: 1.0,
};

/** Cheap, deterministic hash → [0, 1). Exported so other UI surfaces can
 *  mimic the same per-slot fill/gap decision as `paintNatural`. */
export function hash01(n: number): number {
  let h = (n + 0x9e3779b9) | 0;
  h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  h = h ^ (h >>> 16);
  return (h >>> 0) / 0x100000000;
}

function coverageBucket(cl: VizCloudLayer): keyof NaturalCloudConfig['fillFraction'] {
  const cov = cl.coverage?.toUpperCase();
  if (cov === 'OVC' || cov === 'BKN' || cov === 'SCT' || cov === 'FEW') return cov;
  return 'BKN';
}

function naturalFillFraction(
  cl: VizCloudLayer,
  matched: VizCloudLayer | null,
  config: NaturalCloudConfig,
): number {
  // Prefer NWP cover% (granular) when available; else map coverage bucket.
  const covA = cl.meanCloudCoverPct;
  const covB = matched?.meanCloudCoverPct;
  if (covA != null) {
    const pct = covB != null ? (covA + covB) / 2 : covA;
    return Math.max(config.minFillFraction, Math.min(1.0, pct / 100));
  }
  const a = config.fillFraction[coverageBucket(cl)];
  const b = matched ? config.fillFraction[coverageBucket(matched)] : a;
  return Math.max(config.minFillFraction, (a + b) / 2);
}

/** Replace the alpha channel on an `rgba()` / `rgb()` string. Returns the
 *  original string unchanged if it isn't parseable. */
function withAlpha(color: string, alpha: number): string {
  const m = color.match(/rgba?\(([^)]+)\)/);
  if (!m) return color;
  const parts = m[1].split(',').map((s) => s.trim());
  if (parts.length < 3) return color;
  return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
}

/** Draw one soft elliptical blob via a scaled circular radial gradient.
 *  Core (centre) is `cloudColor` at full alpha, fading to transparent at the
 *  outer radius. `coreFraction` is the gradient stop where the fade starts. */
function drawSoftBlob(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  rx: number,
  ry: number,
  cloudColor: string,
  coreFraction: number,
): void {
  if (rx <= 0 || ry <= 0) return;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(1, ry / rx);
  const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, rx);
  grad.addColorStop(0, cloudColor);
  grad.addColorStop(coreFraction, cloudColor);
  grad.addColorStop(1, withAlpha(cloudColor, 0));
  ctx.fillStyle = grad;
  ctx.fillRect(-rx, -rx, 2 * rx, 2 * rx);
  ctx.restore();
}

/** Paint a cloud band as overlapping soft elliptical blobs inside the
 *  envelope defined by `baseAt(x)` and `topAt(x)`. Coverage drives the
 *  probability that each slot along the route carries a blob.
 *
 *  Slots are anchored on a global x grid (`floor(x / blobSpacingPx)`) so
 *  adjacent route segments tile coherently and a slot is owned by exactly
 *  one segment (`cx ∈ [xL, xR)`), preventing double-render at boundaries.
 *
 *  Exported so other UI surfaces (theme preview, legend snippets) can render
 *  the same look as the actual cross-section. */
export function drawNaturalCloudBand(
  ctx: CanvasRenderingContext2D,
  xL: number,
  xR: number,
  baseAt: (x: number) => number,
  topAt: (x: number) => number,
  cloudColor: string,
  fillFraction: number,
  seed: number,
  config: NaturalCloudConfig = DEFAULT_NATURAL_CONFIG,
): void {
  if (xR <= xL) return;

  const slotW = config.blobSpacingPx;
  // Include neighbouring slots so jitter can pull a blob from outside the
  // raw range into our [xL, xR) ownership window.
  const slotStart = Math.floor(xL / slotW) - 1;
  const slotEnd = Math.ceil(xR / slotW) + 1;

  // Coverage-driven blob shrinkage: at low coverage, blobs are smaller so
  // even the surviving slots leave visible sky between clusters (SCT look),
  // while OVC keeps full-size blobs that overlap into a continuous blanket.
  const floor = config.radiusCoverageScaleFloor;
  const radiusScale = floor + (1 - floor) * Math.max(0, Math.min(1, fillFraction));

  for (let s = slotStart; s < slotEnd; s++) {
    if (hash01(s * 0x1f1f1f + seed) > fillFraction) continue;

    const xJ = (hash01(s * 0x2f3f4f + seed) - 0.5) * slotW * config.blobJitterX;
    const cx = (s + 0.5) * slotW + xJ;
    // Ownership rule keeps adjacent segments from drawing the same blob.
    if (cx < xL || cx >= xR) continue;

    const yTop = topAt(cx);
    const yBase = baseAt(cx);
    const bandH = yBase - yTop;
    if (bandH <= 0) continue;

    const yJ = (hash01(s * 0x5b6c79 + seed) - 0.5) * bandH * config.blobJitterY;
    const cy = (yTop + yBase) / 2 + yJ;

    const sizeJ = 1 + (hash01(s * 0x7e8f91 + seed) - 0.5) * config.blobSizeVariation;
    const rx = config.blobRadiusXPx * sizeJ * radiusScale;
    const ry = bandH * config.blobHeightFraction * sizeJ * radiusScale;

    // Paint the slot as a small cluster of overlapping sub-blobs. The union
    // of their soft alpha gradients yields a lumpy, non-circular silhouette
    // instead of a perfect ellipse. n=1 reproduces the single-ellipse style.
    const n = Math.max(1, config.subBlobsPerSlot | 0);
    const aspect = rx > 0 ? ry / rx : 1;
    for (let i = 0; i < n; i++) {
      const subSeed = (Math.imul(s, 0x9e3779b1) ^ Math.imul(i, 0x85ebca6b) ^ seed) | 0;
      // Polar offset around the cluster centre, scaled to the ellipse aspect
      // so the cluster footprint inherits the band's horizontal stretching.
      const angle = hash01(subSeed) * Math.PI * 2;
      const offsetMag = hash01(subSeed ^ 0x12345) * config.subBlobOffsetFraction * rx;
      const ox = Math.cos(angle) * offsetMag;
      const oy = Math.sin(angle) * offsetMag * aspect;
      const subSize = config.subBlobSizeFraction
        + (hash01(subSeed ^ 0x67890) - 0.5) * config.subBlobSizeJitter;
      const subRx = rx * subSize;
      const subRy = ry * subSize;
      drawSoftBlob(ctx, cx + ox, cy + oy, subRx, subRy, cloudColor, config.coreFraction);
    }
  }
}

function paintNatural(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cl: VizCloudLayer,
  matched: VizCloudLayer | null,
  source: SourceSpec,
): void {
  if (bandPoints.length < 2) return;
  const pL = bandPoints[0];
  const pR = bandPoints[1];
  if (pL.base == null || pL.top == null || pR.base == null || pR.top == null) return;

  const config = DEFAULT_NATURAL_CONFIG;
  const xL = transform.distanceToX(pL.distance);
  const xR = transform.distanceToX(pR.distance);
  if (xR <= xL) return;

  const yBaseL = transform.altitudeToY(pL.base);
  const yBaseR = transform.altitudeToY(pR.base);
  const yTopL = transform.altitudeToY(pL.top);
  const yTopR = transform.altitudeToY(pR.top);

  const segWidth = xR - xL;
  const fillFrac = naturalFillFraction(cl, matched, config);
  const baseFill = source.matchedColor(cl, matched);
  const fillStyle = config.fillAlpha != null ? withAlpha(baseFill, config.fillAlpha) : baseFill;

  const baseAt = (x: number) => yBaseL + (yBaseR - yBaseL) * (x - xL) / segWidth;
  const topAt  = (x: number) => yTopL  + (yTopR  - yTopL ) * (x - xL) / segWidth;

  // Round baseFt to nearest 100ft so small zone-boundary drift between
  // adjacent matched segments doesn't reshuffle the noise pattern. Mix in
  // the source key so DD and NWP bands at the same altitude don't share an
  // identical pattern (would look artificially correlated).
  const sourceSalt = source.key === 'nwp' ? 0xdeadbeef : 0x0;
  const bandSeed = ((Math.round(cl.baseFt / 100) ^ 0x4d36e96) ^ sourceSalt) | 0;

  drawNaturalCloudBand(ctx, xL, xR, baseAt, topAt, fillStyle, fillFrac, bandSeed, config);
}

function paintSoft(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  cl: VizCloudLayer,
  matched: VizCloudLayer | null,
): void {
  if (bandPoints.length === 0) return;

  const softConfig = getActiveTheme().softClouds;
  const [r, g, b] = softConfig?.fillRgb ?? [255, 255, 255];
  const configAlpha = softConfig?.coverageAlpha ?? COVERAGE_ALPHA;
  const feather = softConfig?.featherFraction ?? FEATHER_FRACTION;

  const cov = cl.coverage?.toUpperCase() ?? 'BKN';
  const alpha = configAlpha[cov] ?? COVERAGE_ALPHA[cov] ?? 0.5;

  // Modulate by DD if available (use merged value if matched). NWP layers
  // don't carry meanDewpointDepressionC, so dd stays undefined for them
  // and ddFactor stays 1.0 — matching the pre-refactor softNwpCloudBandsLayer.
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
  // Reuse the same continuous color scale as the natural style, but fill
  // a solid rectangle. Gives the same DD/cover% hue+alpha modulation between
  // layers, just cleaner cells.
  const fill = source.matchedColor(cl, matched);
  drawColumnBand(ctx, bandPoints, transform, fill);
}

const STYLES: Record<CloudStyle, OnBand> = {
  natural: (ctx, bp, t, cl, matched, source) => paintNatural(ctx, bp, t, cl, matched, source),
  soft: (ctx, bp, t, cl, matched) => paintSoft(ctx, bp, t, cl, matched),
  square: (ctx, bp, t, cl, matched, source) => paintSquare(ctx, bp, t, cl, matched, source),
};

// --- Factory ---

export interface CloudLayerSpec {
  id: string;
  name: string;
  source: CloudSource;
  style: CloudStyle;
  /** metrics-catalog key for the layer-info popup. Style-specific so the
   *  popup explains soft gradients vs natural puffs vs square cells, not
   *  just the underlying data source. */
  metricId: string;
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
    metricId: spec.metricId,

    render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
      const hasData = data.points.some((p) => source.getZones(p).length > 0);
      if (!hasData) return;

      // Single-point fallback: column per zone (every style collapses to a
      // simple cell here — there's no horizontal span to draw puffs in).
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
        // All three styles draw their visuals via `onBand` (soft paints a
        // gradient fill, natural paints puffs, square paints column rects),
        // so the default smooth-band fill that `renderMatchedZones` would
        // otherwise draw is suppressed by returning 'transparent' here.
        getColor: () => 'transparent',
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
  name: 'DD Natural',
  source: 'dd',
  style: 'natural',
  metricId: 'cloud_coverage',
});

export const nwpCloudBandsLayer = cloudLayer({
  id: 'nwp-cloud-bands',
  name: 'NWP Natural',
  source: 'nwp',
  style: 'natural',
  metricId: 'nwp_cloud_cover',
});

export const softCloudBandsLayer = cloudLayer({
  id: 'soft-cloud-bands',
  name: 'Soft DD',
  source: 'dd',
  style: 'soft',
  metricId: 'soft_cloud_dd',
});

export const softNwpCloudBandsLayer = cloudLayer({
  id: 'soft-nwp-cloud-bands',
  name: 'Soft NWP',
  source: 'nwp',
  style: 'soft',
  metricId: 'soft_cloud_nwp',
});

export const squareCloudBandsLayer = cloudLayer({
  id: 'square-cloud-bands',
  name: 'Square DD',
  source: 'dd',
  style: 'square',
  metricId: 'square_cloud_dd',
});

export const squareNwpCloudBandsLayer = cloudLayer({
  id: 'square-nwp-cloud-bands',
  name: 'Square NWP',
  source: 'nwp',
  style: 'square',
  metricId: 'square_cloud_nwp',
  defaultEnabled: true,
});

// SYNC: the iOS app mirrors this source×style → layer-id mapping (and
// parseCloudLayerId) in CrossSectionPresets.swift (cloudLayerId/parseCloudLayerId).
/** Lookup table: which layer id corresponds to a given (source, style) combo. */
export const CLOUD_LAYER_BY_AXES: Record<CloudSource, Record<CloudStyle, string>> = {
  dd: {
    natural: 'cloud-bands',
    soft: 'soft-cloud-bands',
    square: 'square-cloud-bands',
  },
  nwp: {
    natural: 'nwp-cloud-bands',
    soft: 'soft-nwp-cloud-bands',
    square: 'square-nwp-cloud-bands',
  },
};

/** Flat list of all cloud layer ids — derived so adding a new (source, style)
 *  combo to CLOUD_LAYER_BY_AXES updates this in one place. */
export const ALL_CLOUD_LAYER_IDS: string[] = Object.values(CLOUD_LAYER_BY_AXES)
  .flatMap((styles) => Object.values(styles));

/** Parse a cloud layer id back into its (source, style) axes. Returns null if not a cloud layer. */
export function parseCloudLayerId(id: string): { source: CloudSource; style: CloudStyle } | null {
  for (const source of ['dd', 'nwp'] as CloudSource[]) {
    for (const style of ['natural', 'soft', 'square'] as CloudStyle[]) {
      if (CLOUD_LAYER_BY_AXES[source][style] === id) return { source, style };
    }
  }
  return null;
}
