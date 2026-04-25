/** Hewson metric colormaps (matplotlib-equivalent: RdBu_r, YlOrRd, RdYlBu_r, PuOr_r).
 *
 * The same colormap-per-metric mapping as the ``plot-hewson`` CLI
 * (src/weatherbrief/frontal/cli.py), so the map and the debug plots stay
 * visually consistent — pilots and devs see the same colors for the same
 * physics. See § 7.4 of designs/future/hewson-fields-aviation-advisories.md.
 *
 * Each ramp is 9 hex anchor colors evenly spaced in [0, 1]; we interpolate
 * linearly in sRGB between anchors. Good enough for cell rendering — we
 * don't need perceptual accuracy here.
 */

export type HewsonMetric =
  | 'theta_e'
  | 'gradient'
  | 'neg_laplacian'
  | 'tfp'
  | 'advection'
  | 'tendency';

export interface ColormapSpec {
  /** ``sequential`` for one-sided ramps (gradient), ``diverging`` for ±. */
  type: 'sequential' | 'diverging';
  /** 9 evenly-spaced hex anchors in [0, 1]. */
  ramp: string[];
  /** Default lower bound for color mapping. */
  defaultVmin: number;
  /** Default upper bound. For diverging metrics this is the symmetric |max|. */
  defaultVmax: number;
  /** Pilot-facing units (used in legend). */
  unit: string;
  /** Pilot-facing display name (legend title, popovers). */
  label: string;
  /** Short pilot-facing interpretation drawn from § 2 of the design doc. */
  blurb: string;
}

// Matplotlib RdBu_r (cold blue → white → warm red).
const RdBu_r = [
  '#053061', '#2166ac', '#4393c3', '#92c5de', '#f7f7f7',
  '#fddbc7', '#f4a582', '#d6604d', '#67001f',
];

// Matplotlib YlOrRd (yellow → orange → red, sequential).
const YlOrRd = [
  '#ffffcc', '#ffeda0', '#fed976', '#feb24c', '#fd8d3c',
  '#fc4e2a', '#e31a1c', '#bd0026', '#800026',
];

// Matplotlib RdYlBu_r (cold blue → yellow → warm red).
const RdYlBu_r = [
  '#313695', '#4575b4', '#74add1', '#abd9e9', '#ffffbf',
  '#fee090', '#fdae61', '#f46d43', '#a50026',
];

// Matplotlib PuOr_r (purple → white → orange).
const PuOr_r = [
  '#2d004b', '#542788', '#8073ac', '#b2abd2', '#f7f7f7',
  '#fee0b6', '#fdb863', '#e08214', '#7f3b08',
];

export const COLORMAPS: Record<HewsonMetric, ColormapSpec> = {
  // θe at 850 hPa runs ~285-325 K in mid-latitudes, with tropical /
  // convection-prone air starting around 335 K (per Hewson catalog
  // calibration in hewson-metrics-catalog.ts). vmax=340 keeps the full
  // tropical range visible without clipping.
  theta_e: {
    type: 'diverging',
    ramp: RdYlBu_r,
    defaultVmin: 270,
    defaultVmax: 340,
    unit: 'K',
    label: 'θe (equivalent potential temperature)',
    blurb:
      '> 335 K tropical/subtropical · 290–325 K mid-latitude · < 285 K polar (thresholds at 850 hPa).',
  },
  // Gradient is one-sided; CLI uses vmax=10 K/100 km for thermal fronts.
  // Hewson 1998 thresholds (at 850 hPa): 4–6 K/100 km is "classical front",
  // > 9 is sharp. See hewson-metrics-catalog.ts for the full per-level table.
  gradient: {
    type: 'sequential',
    ramp: YlOrRd,
    defaultVmin: 0,
    defaultVmax: 10,
    unit: 'K / 100 km',
    label: '|∇θe| (boundary intensity)',
    blurb:
      '< 1 negligible · 1–3 weak · 4–6 classical front · > 6 strong · > 9 very sharp (thresholds at 850 hPa).',
  },
  // 2nd-derivative fields use ±2 K/(100 km)² as the "sharp transition" gate.
  neg_laplacian: {
    type: 'diverging',
    ramp: RdBu_r,
    defaultVmin: -2,
    defaultVmax: 2,
    unit: 'K / (100 km)²',
    label: '−∇²θe (sharpness)',
    blurb:
      '> 2 sharp (narrow-band weather) · ≈ 0 smooth gradient · < 0 inside a broad transition.',
  },
  tfp: {
    type: 'diverging',
    ramp: PuOr_r,
    defaultVmin: -2,
    defaultVmax: 2,
    unit: 'K / (100 km)²',
    label: 'TFP (front side)',
    blurb:
      'TFP > 0 warm side, conditions deteriorating · ≈ 0 at sharpest part · < 0 cold side, improving.',
  },
  // Advection thresholds from § 2.5: 1 K/h significant, 2 K/h rapid.
  advection: {
    type: 'diverging',
    ramp: RdBu_r,
    defaultVmin: -2,
    defaultVmax: 2,
    unit: 'K / h',
    label: '−V·∇θe (warm/cold advection)',
    blurb:
      'Red = warm advection (ceilings drop, icing risk rises). Blue = cold advection (clearing, gusty showers).',
  },
  tendency: {
    type: 'diverging',
    ramp: RdBu_r,
    defaultVmin: -2,
    defaultVmax: 2,
    unit: 'K / h',
    label: '∂θe/∂t (tendency)',
    blurb:
      'Rising = warm sector arriving. Falling = post-frontal clearing or evening cooling.',
  },
};

// ---------------------------------------------------------------------------
// Color sampling
// ---------------------------------------------------------------------------

/** Hex "#rrggbb" → [r, g, b] in 0–255. */
function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
}

function rgbToHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map((c) => Math.round(c).toString(16).padStart(2, '0')).join('');
}

/** Sample a 9-anchor ramp at t ∈ [0, 1] with linear sRGB interpolation. */
function sampleRamp(ramp: string[], t: number): string {
  if (!Number.isFinite(t)) return '#888';
  const x = Math.max(0, Math.min(1, t));
  const n = ramp.length - 1;
  const idx = x * n;
  const lo = Math.floor(idx);
  const hi = Math.min(lo + 1, n);
  const frac = idx - lo;
  const a = hexToRgb(ramp[lo]);
  const b = hexToRgb(ramp[hi]);
  return rgbToHex(
    a[0] + (b[0] - a[0]) * frac,
    a[1] + (b[1] - a[1]) * frac,
    a[2] + (b[2] - a[2]) * frac,
  );
}

/** Map a value to a hex color using the metric's ramp and the supplied range.
 *
 * For ``diverging`` metrics, ``vmin``/``vmax`` are typically symmetric ±V; the
 * midpoint of the ramp lands at 0. Returns a transparent gray for non-finite
 * input (matches the NaN cells in the precompute snapshot).
 */
export function colorFor(
  metric: HewsonMetric,
  value: number | null,
  vmin: number,
  vmax: number,
): string {
  if (value === null || !Number.isFinite(value)) return 'rgba(0,0,0,0)';
  if (vmax === vmin) return sampleRamp(COLORMAPS[metric].ramp, 0.5);
  const t = (value - vmin) / (vmax - vmin);
  return sampleRamp(COLORMAPS[metric].ramp, t);
}

/** Generate a CSS linear-gradient string for a colorbar legend. */
export function gradientCss(metric: HewsonMetric): string {
  const ramp = COLORMAPS[metric].ramp;
  const stops = ramp.map((c, i) => `${c} ${(i / (ramp.length - 1)) * 100}%`);
  return `linear-gradient(to right, ${stops.join(', ')})`;
}
