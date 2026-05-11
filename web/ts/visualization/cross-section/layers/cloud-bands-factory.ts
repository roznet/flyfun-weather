/** Cloud bands layer factory — orthogonal source × style axes.
 *
 * Source picks the data feed and color metric:
 *   - 'dd'  → p.cloudLayers, color from dewpoint depression
 *   - 'nwp' → p.nwpCloudLayers, color from NWP cover %
 *
 * Style picks the painter:
 *   - 'natural' → flat-bottom puffs with bumpy tops; coverage encoded as
 *                 horizontal fill fraction (SCT = gaps between puffs, BKN =
 *                 touching puffs with valleys, OVC = continuous bumpy blanket).
 *                 Tries to look like clouds out the window.
 *   - 'soft'    → feathered vertical-gradient fill (GRAMET-like).
 *   - 'square'  → solid rectangle, opacity from value (ForeFlight-like cells).
 *
 * Layer IDs preserve the existing combined naming so persisted prefs and
 * presets keep working: 'cloud-bands', 'nwp-cloud-bands', 'soft-cloud-bands',
 * 'soft-nwp-cloud-bands'. Square layers add 'square-cloud-bands' and
 * 'square-nwp-cloud-bands'. The 'natural' style is the replacement for the
 * old hatched 'layer' style — the IDs are reused, only the rendering changed.
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
// These knobs control the puffy-cloud appearance. Tweak in this one place
// to adjust how SCT/BKN/OVC look. The aim is "what you'd see out the window":
//   - SCT: discrete puffs with sky gaps between them
//   - BKN: puffs that mostly touch, occasional gap
//   - OVC: continuous bumpy blanket
// Bump count, amplitude, and gap pattern come from a deterministic per-band
// hash so the same band keeps a stable shape across redraws.

export interface NaturalCloudConfig {
  /** Target horizontal fill fraction per METAR coverage class (DD source).
   *  NWP source uses `meanCloudCoverPct / 100` directly. */
  fillFraction: { FEW: number; SCT: number; BKN: number; OVC: number };
  /** Target horizontal extent of a single puff slot, in CSS px. */
  puffWidthPx: number;
  /** Target horizontal extent of a single bump within a puff, in CSS px. */
  humpWidthPx: number;
  /** Below this segment width, skip the gap pattern and draw one continuous
   *  bumpy fill — gaps would be too small to read at this scale. */
  minBandWidthPx: number;
  /** 0..1: max per-bump amplitude reduction (jitter so peaks aren't uniform). */
  amplitudeJitter: number;
  /** Extra px above the band top so puffs look fluffy / overflow slightly.
   *  Set to 0 for strict clipping to the band envelope. NOTE: there is
   *  intentionally no `ctx.clip` around the puff fills, so the overflow is
   *  visible. At the default 2 px this is harmless; raising this knob will
   *  let puffs visibly invade adjacent bands. Bump with care. */
  edgeOverflowPx: number;
  /** Fixed alpha applied to the source color. Coverage lives in the gap
   *  pattern, not opacity — this avoids double-encoding SCT as "fewer puffs
   *  AND lower alpha". Set to null to keep the source's coverage-modulated
   *  alpha. */
  fillAlpha: number | null;
  /** Floor on fill fraction (so an NWP cover% of 5% still shows a puff or two). */
  minFillFraction: number;
}

export const DEFAULT_NATURAL_CONFIG: NaturalCloudConfig = {
  fillFraction: { FEW: 0.20, SCT: 0.45, BKN: 0.80, OVC: 1.00 },
  puffWidthPx: 30,
  humpWidthPx: 14,
  minBandWidthPx: 50,
  amplitudeJitter: 0.25,
  edgeOverflowPx: 2,
  fillAlpha: 0.85,
  minFillFraction: 0.15,
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

/** Draw one puff path (flat bottom + bumpy top) inside [xa, xb] using linear
 *  interpolation of base/top profiles. Caller fills.
 *
 *  Exported so other UI surfaces (theme preview, legend snippets) can render
 *  the same puff geometry as the actual cross-section. */
export function buildPuffPath(
  ctx: CanvasRenderingContext2D,
  xa: number,
  xb: number,
  baseAt: (x: number) => number,
  topAt: (x: number) => number,
  seed: number,
  config: NaturalCloudConfig,
): void {
  const width = xb - xa;
  const nHumps = Math.max(1, Math.round(width / config.humpWidthPx));
  const humpStride = width / nHumps;

  ctx.beginPath();
  ctx.moveTo(xa, baseAt(xa));

  // Walk the bumpy top left→right. Each hump is a quadratic Bezier from
  // (x0, base) up over (xMid, peak) to (x1, base), giving a smooth cumulus
  // dome. Peak height is jittered per-bump for variety.
  for (let h = 0; h < nHumps; h++) {
    const x0 = xa + h * humpStride;
    const x1 = xa + (h + 1) * humpStride;
    const xMid = (x0 + x1) / 2;
    const yBase0 = baseAt(x0);
    const yBase1 = baseAt(x1);
    const yTopMid = topAt(xMid);

    const yBaseMid = (yBase0 + yBase1) / 2;
    const fullAmp = yBaseMid - yTopMid;  // positive in canvas y
    if (fullAmp <= 0) {
      ctx.lineTo(x1, yBase1);
      continue;
    }

    const j = hash01(seed * 31 + h);
    const ampScale = 1.0 - config.amplitudeJitter * j;
    const yPeak = yBaseMid - ampScale * fullAmp - config.edgeOverflowPx;
    // Quadratic Bezier control point so the curve passes through (xMid, yPeak)
    // at t = 0.5: ctrlY = 2 * peak - yBaseMid (derived from B(0.5) algebra).
    const ctrlY = 2 * yPeak - yBaseMid;
    ctx.quadraticCurveTo(xMid, ctrlY, x1, yBase1);
  }

  ctx.closePath();
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

  // Linear interpolators across the 2-point band segment.
  const baseAt = (x: number) => yBaseL + (yBaseR - yBaseL) * (x - xL) / segWidth;
  const topAt  = (x: number) => yTopL  + (yTopR  - yTopL ) * (x - xL) / segWidth;

  // Round baseFt to nearest 100ft so small zone-boundary drift between
  // adjacent matched segments doesn't reshuffle the gap pattern. Mix in
  // the source key so DD and NWP bands at the same altitude don't share
  // an identical gap pattern (would look artificially correlated).
  const sourceSalt = source.key === 'nwp' ? 0xdeadbeef : 0x0;
  const bandSeed = ((Math.round(cl.baseFt / 100) ^ 0x4d36e96) ^ sourceSalt) | 0;

  ctx.save();
  ctx.fillStyle = fillStyle;

  // OVC and short segments → one continuous bumpy blanket (no gaps).
  if (fillFrac >= 0.99 || segWidth < config.minBandWidthPx) {
    buildPuffPath(ctx, xL, xR, baseAt, topAt, bandSeed, config);
    ctx.fill();
    ctx.restore();
    return;
  }

  // Discrete puffs anchored on a global x grid so adjacent segments tile
  // coherently — slot S spans [S * puffW, (S + 1) * puffW] in canvas px.
  const puffW = config.puffWidthPx;
  const slotStart = Math.floor(xL / puffW);
  const slotEnd = Math.ceil(xR / puffW);

  for (let s = slotStart; s < slotEnd; s++) {
    if (hash01(s * 0x1f1f1f + bandSeed) > fillFrac) continue;
    const xa = Math.max(xL, s * puffW);
    const xb = Math.min(xR, (s + 1) * puffW);
    if (xb - xa < 2) continue;
    buildPuffPath(ctx, xa, xb, baseAt, topAt, bandSeed ^ s, config);
    ctx.fill();
  }

  ctx.restore();
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
  defaultEnabled: true,
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
});

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
