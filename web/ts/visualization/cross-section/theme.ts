/** Cross-section color theme system.
 *
 * Centralizes all color values used by cross-section layers into a single
 * typed theme object. Supports multiple built-in themes (standard,
 * high-contrast) switchable at runtime.
 *
 * Usage: import { getActiveTheme } from './theme';
 *        const t = getActiveTheme();
 *        ctx.fillStyle = t.terrain.fillColor;
 *
 * SYNC: the iOS app mirrors these themes (color values + IDs) in
 * app/flyfun-weather/flyfun-weather/Views/CrossSection/CrossSectionTheme.swift.
 * iOS ports only the fields its layers render (no nightShading / obscuration /
 * compareModelColors / sld yet) and themes colors but not line widths/dashes.
 * Keep the two in lockstep when editing palettes (#320).
 */

// --- Theme interface ---

export interface LineStyle {
  color: string;
  width: number;
  dash?: number[];
}

export interface CrossSectionTheme {
  id: ThemeId;
  label: string;

  sky: {
    background: string;
  };

  axes: {
    gridColor: string;
    waypointLineColor: string;
  };

  terrain: {
    fillColor: string;
    outlineColor: string;
  };

  temperature: {
    freezingLevel: LineStyle;
    minus10c: LineStyle;
    minus20c: LineStyle;
  };

  stability: {
    lcl: LineStyle;
    lfc: LineStyle;
    el: LineStyle;
  };

  reference: {
    cruiseColor: string;
    ceilingColor: string;
  };

  clouds: {
    /** Dense cloud RGB (DD ≈ 0). */
    denseRgb: [number, number, number];
    /** Thin cloud RGB (DD ≈ 3). */
    thinRgb: [number, number, number];
    /** Coverage alpha ranges: [min, max] per coverage class. */
    coverageAlpha: Record<string, [number, number]>;
    /** Fallback opacity when DD is undefined. */
    fallbackGray: [number, number, number];
    /** Fixed grid spacing in pixels for hatch lines. */
    hatchGridPx: number;
    /** Hatch line width per coverage class (pixels). At gridPx = solid. */
    hatchLineWidth: Record<string, number>;
    /** Hatch line color. */
    hatchColor: string;
  };

  nwpClouds: {
    /** Bright RGB (low cover). */
    brightRgb: [number, number, number];
    /** Dark delta per channel (subtracted at 100% cover). */
    deltaRgb: [number, number, number];
    /** Opacity range [floor, scale] → opacity = floor + scale * (pct/100). */
    opacityRange: [number, number];
  };

  icing: Record<string, string>;
  sfipIcing: Record<string, string>;
  cat: Record<string, string>;

  convective: {
    riskColors: Record<string, string>;
    bgWash: Record<string, string>;
    towerFill: Record<string, string>;
    hatchColor: Record<string, string>;
    stripColor: Record<string, string>;
    edgeColor: Record<string, string>;
    cbLabelColor: Record<string, string>;
  };

  inversion: {
    baseRgb: [number, number, number];
    /** Opacity params: [floor, scale, maxStrengthC, cap]. */
    opacityParams: { floor: number; scale: number; maxStrengthC: number; cap: number };
  };

  /** Surface obscuration (fog / LIFR) band colors and hatch settings.
   *  Keyed by aviation flight category so `theme.obscuration[severity]`
   *  reads naturally at the call site. Themes that prefer a solid fill
   *  or alternative texture can override `hatchLineWidth` /
   *  `hatchSpacingPx`. */
  obscuration: {
    lifr: string;
    ifr: string;
    mvfr: string;
    hatchColor: string;
    hatchSpacingPx: number;
    hatchLineWidth: number;
  };

  /** Night/twilight column tint for the night-shading layer (#227). Drawn at
   *  the back of the stack; twilight is a light wash, night is darker. */
  nightShading: {
    twilight: string;
    night: string;
  };

  /** Optional soft-cloud rendering config. When omitted the soft style
   *  falls back to defaults defined in the cloud-bands factory. */
  softClouds?: {
    fillRgb: [number, number, number];
    coverageAlpha: Record<string, number>;
    featherFraction: number;
  };

  coverageOpacity: Record<string, number>;

  /** Observed layers (#574): measured data, drawn so it can never be mistaken
   *  for the forecast bands beside it.
   *
   *  Cloud tops are coloured by **temperature**, because temperature is what
   *  the instrument actually measures — height is derived from it against a
   *  model profile. The ramp follows the enhanced-IR convention pilots already
   *  read on satellite imagery (warm → cold: blue, cyan, green, yellow,
   *  orange, red), with one deliberate departure: the conventional warm end is
   *  grayscale, and gray here is indistinguishable from the NWP cloud bands.
   *  Warm tops use a desaturated blue instead.
   *
   *  `tempStops` are [°C, cssColor], warmest first. Interpolation is nearest-
   *  stop, not blended: an invented intermediate colour would imply a
   *  precision the 2 km retrieval does not have. */
  observed: {
    /** Sequential light→dark ramp for a band's SHARE of the disc, [fraction,
     *  cssColor], lightest first.
     *
     *  Share rather than temperature is what the cross-section colour carries,
     *  because the vertical axis already encodes altitude and cloud-top
     *  temperature is very nearly a function of it — colouring by temperature
     *  spends the channel on something the reader can already see. How much of
     *  the disc a band holds is the thing position cannot show.
     *
     *  Temperature keeps the `tempStops` ramp, which the MAP uses: there is no
     *  altitude axis there, so temperature is genuinely new information. */
    shareStops: Array<[number, string]>;
    tempStops: Array<[number, string]>;
    /** Fallback when a top carries no temperature (older cached frame). */
    tempUnknown: string;
    /** Hatching that says "depth unknown" below a top, and the off-scale box. */
    hatchColor: string;
    /** Outline of the highest-top cap, and the off-scale arrow. */
    capColor: string;
    /** Cap outline when the retrieval flags the disc multi-layer-suspect. */
    capMultiLayerColor: string;
    /** "The sensor does not look here" — never the same as "saw nothing". */
    noCoverageColor: string;
  };

  /** Distinct colors assigned to models in consensus-outline mode. */
  compareModelColors: string[];
}

/** Enhanced-IR ramp, warmest first. Shared starting point for the themes that
 *  do not need to diverge; see `CrossSectionTheme.observed` for why the warm
 *  end is blue rather than the conventional grayscale. */
/** Sequential share ramp, lightest first. A single hue so it reads as one
 *  quantity getting stronger, and blue-violet so it cannot be confused with
 *  the radar ramp (green→red) or the NWP cloud bands (gray/white). */
/** Band share of the LOOKED-AT SKY → colour. Breakpoints, not the colours,
 *  carry the calibration, and they start at the 5% drawing floor: a stop below
 *  it would be spent on bands that are never drawn. Above it, measured over
 *  real packs, the surviving bands run from the floor to about half the sky —
 *  a 20 NM disc splits its cloud across a dozen fine 10-FL bands, so a fifth
 *  of the sky in one of them is already a big band. */
export const SHARE_STOPS: Array<[number, string]> = [
  [0.05, '#6377b8'],
  [0.07, '#576aa8'],
  [0.10, '#4b5c96'],
  [0.15, '#3f4e84'],
  [0.22, '#333f6d'],
  [0.35, '#282f54'],
  [0.55, '#1c2039'],
];

export const IR_TEMP_STOPS: Array<[number, string]> = [
  [15, '#8fa8c8'],   // desaturated blue — warm, low cloud
  [0, '#7f9dc4'],
  [-15, '#6b8fc0'],
  [-30, '#4a7fd0'],  // conventional ramp starts here
  [-40, '#3fb7d8'],
  [-50, '#4cc76a'],
  [-55, '#e0d84a'],
  [-60, '#e8a33c'],
  [-70, '#d94f3d'],
  [-80, '#a3243a'],
];

// --- Theme IDs ---

export type ThemeId = 'standard' | 'high-contrast' | 'gramet' | 'light';

// --- Standard theme (current production values) ---

const STANDARD_THEME: CrossSectionTheme = {
  id: 'standard',
  nightShading: {
    twilight: 'rgba(40, 40, 90, 0.18)',
    night: 'rgba(15, 15, 45, 0.38)',
  },
  label: 'Standard',

  sky: {
    background: '#7395DB',
  },

  axes: {
    gridColor: 'rgba(255, 255, 255, 0.35)',
    waypointLineColor: 'rgba(255, 255, 255, 0.45)',
  },

  terrain: {
    fillColor: '#8B7355',
    outlineColor: '#6B5B45',
  },

  temperature: {
    freezingLevel: { color: '#00bcd4', width: 2 },
    minus10c: { color: '#2196f3', width: 1.5 },
    minus20c: { color: '#1a237e', width: 1, dash: [6, 4] },
  },

  // The parcel triplet is green/orange/red — the classic red-green confusion
  // set — so hue alone cannot identify these lines for a colour-blind pilot
  // (simulated protanopia puts LCL and EL at ΔE 9–20, i.e. the same colour).
  // Each level therefore carries a structurally distinct stroke as its primary
  // cue, readable even in monochrome: LCL dotted, LFC dashed, EL dash-dot.
  // SYNC: mirrored in iOS `StabilityLinesLayer.swift` (`dash(for:)`).
  stability: {
    lcl: { color: '#4caf50', width: 2, dash: [2, 4] },
    lfc: { color: '#ff9800', width: 1.5, dash: [6, 4] },
    el: { color: '#f44336', width: 1.5, dash: [9, 3, 2, 3] },
  },

  reference: {
    cruiseColor: '#374151',
    ceilingColor: '#9467bd',
  },

  clouds: {
    denseRgb: [140, 140, 150],
    thinRgb: [250, 250, 255],
    coverageAlpha: {
      few: [0.20, 0.35],
      sct: [0.50, 0.65],
      bkn: [0.60, 0.88],
      ovc: [0.70, 0.95],
    },
    fallbackGray: [180, 180, 185],
    hatchGridPx: 8,
    hatchLineWidth: { few: 1, sct: 2, bkn: 5, ovc: 8 },
    hatchColor: 'rgba(255, 255, 255, 0.5)',
  },

  nwpClouds: {
    brightRgb: [245, 245, 255],
    deltaRgb: [105, 105, 100],
    opacityRange: [0.30, 0.55],
  },

  icing: {
    none: 'transparent',
    light: 'rgba(185, 170, 230, 0.70)',
    moderate: 'rgba(120, 100, 215, 0.85)',
    severe: 'rgba(65, 35, 155, 0.93)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(185, 170, 230, 0.78)',
    moderate: 'rgba(120, 100, 215, 0.92)',
    severe: 'rgba(65, 35, 155, 1.00)',
  },

  cat: {
    none: 'transparent',
    light: 'rgba(255, 193, 7, 0.20)',
    moderate: 'rgba(255, 152, 0, 0.40)',
    severe: 'rgba(220, 53, 69, 0.55)',
  },

  convective: {
    riskColors: {
      none: 'transparent',
      marginal: 'rgba(160, 160, 160, 0.08)',
      low: 'rgba(255, 235, 59, 0.10)',
      moderate: 'rgba(255, 152, 0, 0.15)',
      high: 'rgba(220, 53, 69, 0.20)',
      extreme: 'rgba(183, 28, 28, 0.25)',
    },
    bgWash: {
      marginal: 'rgba(200, 200, 200, 0.04)',
      low: 'rgba(255, 235, 59, 0.06)',
      moderate: 'rgba(255, 152, 0, 0.08)',
      high: 'rgba(220, 53, 69, 0.10)',
      extreme: 'rgba(183, 28, 28, 0.14)',
    },
    towerFill: {
      marginal: 'rgba(180, 180, 180, 0.15)',
      low: 'rgba(255, 235, 59, 0.18)',
      moderate: 'rgba(255, 152, 0, 0.25)',
      high: 'rgba(220, 53, 69, 0.30)',
      extreme: 'rgba(183, 28, 28, 0.35)',
    },
    hatchColor: {
      marginal: 'rgba(140, 140, 140, 0.15)',
      low: 'rgba(180, 160, 0, 0.20)',
      moderate: 'rgba(200, 100, 0, 0.35)',
      high: 'rgba(200, 40, 40, 0.40)',
      extreme: 'rgba(150, 20, 20, 0.50)',
    },
    stripColor: {
      marginal: 'rgba(160, 160, 160, 0.4)',
      low: 'rgba(255, 235, 59, 0.5)',
      moderate: 'rgba(255, 152, 0, 0.75)',
      high: 'rgba(220, 53, 69, 0.85)',
      extreme: 'rgba(183, 28, 28, 0.9)',
    },
    edgeColor: {
      marginal: 'rgba(140, 140, 140, 0.25)',
      low: 'rgba(180, 160, 0, 0.3)',
      moderate: 'rgba(200, 100, 0, 0.5)',
      high: 'rgba(200, 40, 40, 0.6)',
      extreme: 'rgba(150, 20, 20, 0.7)',
    },
    cbLabelColor: {
      moderate: 'rgba(200, 100, 0, 0.8)',
      high: 'rgba(200, 40, 40, 0.9)',
      extreme: 'rgba(150, 20, 20, 0.95)',
    },
  },

  inversion: {
    baseRgb: [233, 30, 99],
    opacityParams: { floor: 0.15, scale: 0.5, maxStrengthC: 3, cap: 0.65 },
  },

  obscuration: {
    lifr: 'rgba(168, 85, 247, 0.65)',  // purple
    ifr: 'rgba(239, 68, 68, 0.55)',    // red
    mvfr: 'rgba(245, 158, 11, 0.50)',  // amber
    hatchColor: 'rgba(255, 255, 255, 0.65)',
    hatchSpacingPx: 8,
    hatchLineWidth: 1.5,
  },

  coverageOpacity: {
    sct: 0.25,
    bkn: 0.50,
    ovc: 0.75,
  },

  observed: {
    shareStops: SHARE_STOPS,
    tempStops: IR_TEMP_STOPS,
    tempUnknown: 'rgba(143, 168, 200, 0.75)',
    hatchColor: 'rgba(190, 210, 235, 0.55)',
    capColor: '#e8eef8',
    capMultiLayerColor: '#f0a94c',
    noCoverageColor: 'rgba(150, 160, 175, 0.75)',
  },

  compareModelColors: [
    '#e6194b', // red
    '#3cb44b', // green
    '#4363d8', // blue
    '#f58231', // orange
    '#911eb4', // purple
    '#42d4f4', // cyan
  ],
};

// --- High-contrast theme ---

const HIGH_CONTRAST_THEME: CrossSectionTheme = {
  ...STANDARD_THEME,
  id: 'high-contrast',
  nightShading: {
    twilight: 'rgba(10, 10, 30, 0.28)',
    night: 'rgba(0, 0, 0, 0.48)',
  },
  label: 'High Contrast',

  sky: {
    background: '#1B3060',
  },

  axes: {
    gridColor: 'rgba(255, 255, 255, 0.30)',
    waypointLineColor: 'rgba(255, 255, 255, 0.40)',
  },

  terrain: {
    fillColor: '#4A3A28',
    outlineColor: '#6B5B45',
  },

  temperature: {
    freezingLevel: { color: '#00e5ff', width: 2.5 },
    minus10c: { color: '#42a5f5', width: 2 },
    minus20c: { color: '#7c4dff', width: 1.5, dash: [6, 4] },
  },

  // Dash roles per STANDARD: dotted / dashed / dash-dot.
  stability: {
    lcl: { color: '#69f0ae', width: 2.5, dash: [2, 4] },
    lfc: { color: '#ffab40', width: 2, dash: [6, 4] },
    el: { color: '#ff5252', width: 2, dash: [9, 3, 2, 3] },
  },

  reference: {
    cruiseColor: '#e0e0e0',
    ceilingColor: '#ce93d8',
  },

  clouds: {
    denseRgb: [70, 70, 70],
    thinRgb: [230, 230, 230],
    coverageAlpha: {
      few: [0.15, 0.30],
      sct: [0.45, 0.60],
      bkn: [0.55, 0.85],
      ovc: [0.65, 0.95],
    },
    fallbackGray: [120, 120, 120],
    hatchGridPx: 8,
    hatchLineWidth: { few: 1, sct: 2, bkn: 5, ovc: 8 },
    hatchColor: 'rgba(180, 180, 180, 0.45)',
  },

  nwpClouds: {
    brightRgb: [230, 230, 230],
    deltaRgb: [150, 150, 150],
    opacityRange: [0.30, 0.55],
  },

  icing: {
    none: 'transparent',
    light: 'rgba(200, 220, 240, 0.70)',
    moderate: 'rgba(154, 176, 224, 0.80)',
    severe: 'rgba(132, 112, 216, 0.90)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(200, 220, 240, 0.80)',
    moderate: 'rgba(154, 176, 224, 0.90)',
    severe: 'rgba(132, 112, 216, 1.00)',
  },

  cat: {
    none: 'transparent',
    light: 'rgba(24, 136, 72, 0.40)',
    moderate: 'rgba(152, 184, 48, 0.55)',
    severe: 'rgba(200, 208, 16, 0.70)',
  },

  convective: {
    riskColors: {
      none: 'transparent',
      marginal: 'rgba(248, 160, 32, 0.10)',
      low: 'rgba(248, 160, 32, 0.18)',
      moderate: 'rgba(240, 120, 32, 0.25)',
      high: 'rgba(216, 80, 32, 0.32)',
      extreme: 'rgba(232, 24, 24, 0.40)',
    },
    bgWash: {
      marginal: 'rgba(248, 160, 32, 0.06)',
      low: 'rgba(248, 160, 32, 0.10)',
      moderate: 'rgba(240, 120, 32, 0.14)',
      high: 'rgba(216, 80, 32, 0.18)',
      extreme: 'rgba(232, 24, 24, 0.22)',
    },
    towerFill: {
      marginal: 'rgba(248, 160, 32, 0.20)',
      low: 'rgba(248, 160, 32, 0.28)',
      moderate: 'rgba(240, 120, 32, 0.38)',
      high: 'rgba(216, 80, 32, 0.48)',
      extreme: 'rgba(232, 24, 24, 0.55)',
    },
    hatchColor: {
      marginal: 'rgba(248, 160, 32, 0.25)',
      low: 'rgba(248, 160, 32, 0.35)',
      moderate: 'rgba(240, 120, 32, 0.50)',
      high: 'rgba(216, 80, 32, 0.60)',
      extreme: 'rgba(232, 24, 24, 0.70)',
    },
    stripColor: {
      marginal: 'rgba(248, 160, 32, 0.45)',
      low: 'rgba(248, 160, 32, 0.55)',
      moderate: 'rgba(240, 120, 32, 0.75)',
      high: 'rgba(216, 80, 32, 0.85)',
      extreme: 'rgba(232, 24, 24, 0.92)',
    },
    edgeColor: {
      marginal: 'rgba(248, 160, 32, 0.30)',
      low: 'rgba(248, 160, 32, 0.40)',
      moderate: 'rgba(240, 120, 32, 0.55)',
      high: 'rgba(216, 80, 32, 0.65)',
      extreme: 'rgba(232, 24, 24, 0.75)',
    },
    cbLabelColor: {
      moderate: 'rgba(240, 120, 32, 0.85)',
      high: 'rgba(216, 80, 32, 0.92)',
      extreme: 'rgba(232, 24, 24, 0.95)',
    },
  },

  inversion: {
    baseRgb: [255, 82, 82],
    opacityParams: { floor: 0.25, scale: 0.55, maxStrengthC: 3, cap: 0.80 },
  },

  // Brighter, more opaque fills than standard to keep the band readable
  // against the deep navy sky. Hatch picked up from STANDARD via
  // inheritance is fine (white-on-dark reads well).
  obscuration: {
    lifr: 'rgba(192, 132, 252, 0.75)',
    ifr: 'rgba(248, 113, 113, 0.65)',
    mvfr: 'rgba(251, 191, 36, 0.60)',
    hatchColor: 'rgba(255, 255, 255, 0.70)',
    hatchSpacingPx: 8,
    hatchLineWidth: 1.5,
  },

  // Same temperature ramp — the enhanced-IR convention is the point, and
  // changing hues per theme would mean a pilot reads the same colour as two
  // different temperatures. Only the surrounding strokes go pure, so the
  // marker survives the deep navy sky and the heavier bands around it.
  observed: {
    // Runs the other way: this theme's sky is a deep navy (#1B3060), so a
    // DARKER band is less visible, not more. What must increase with share is
    // contrast against the sky, and on a dark sky that means brightening.
    // Same meaning, same direction of emphasis, inverted luminance.
    shareStops: [
      [0.05, '#3f5590'], [0.07, '#5570ab'], [0.10, '#6d8ac6'],
      [0.15, '#87a4de'], [0.22, '#a3bdec'], [0.35, '#c2d6f7'],
      [0.55, '#e6efff'],
    ],
    tempStops: IR_TEMP_STOPS,
    tempUnknown: 'rgba(170, 195, 230, 0.9)',
    hatchColor: 'rgba(255, 255, 255, 0.8)',
    capColor: '#ffffff',
    capMultiLayerColor: '#ffc046',
    noCoverageColor: 'rgba(255, 255, 255, 0.85)',
  },
};

// --- GRAMET theme (CloudPath-inspired, optimized for soft cloud overlays) ---

const GRAMET_THEME: CrossSectionTheme = {
  ...STANDARD_THEME,
  id: 'gramet' as ThemeId,
  observed: {
    ...STANDARD_THEME.observed,
    // This sky (#2B5DA8) is darker than standard's, and the standard ramp
    // descends through it — a mid-share band would sit at almost the sky's own
    // luminance and disappear. Ascends instead, same meaning, same direction
    // of emphasis.
    shareStops: [
      [0.05, '#5b83c4'], [0.07, '#7599d2'], [0.10, '#8fafdf'],
      [0.15, '#a9c4ea'], [0.22, '#c3d8f3'], [0.35, '#dae9fa'],
      [0.55, '#f0f6ff'],
    ],
  },
  nightShading: {
    twilight: 'rgba(20, 20, 60, 0.20)',
    night: 'rgba(5, 5, 30, 0.42)',
  },
  label: 'GRAMET',

  sky: {
    background: '#2B5DA8',
  },

  axes: {
    gridColor: 'rgba(255, 255, 255, 0.25)',
    waypointLineColor: 'rgba(255, 255, 255, 0.35)',
  },

  terrain: {
    fillColor: '#8B6914',
    outlineColor: '#6B5010',
  },

  temperature: {
    freezingLevel: { color: '#FF4444', width: 2.5 },
    // −10 and −20 deliberately share the autorouter green: they are always
    // stacked in that order, and the dash pattern separates them.
    minus10c: { color: '#22CC44', width: 1.5, dash: [6, 4] },
    minus20c: { color: '#22CC44', width: 1, dash: [4, 4] },
  },

  stability: {
    ...STANDARD_THEME.stability,
    // Standard's green LCL is unusable here — it would be a third green dashed
    // line against the isotherms, at the same 6/4 dash as −10°C, with no
    // stacking order to tell them apart. Cyan is the one hue GRAMET leaves
    // free (freezing is red, icing mint, CAT amber, inversion pink, ceiling
    // lilac) and reads as the condensation level. Dash stays the shared
    // dotted LCL role.
    lcl: { color: '#00E5FF', width: 2, dash: [2, 4] },
  },

  reference: {
    cruiseColor: '#e0e0e0',
    ceilingColor: '#ce93d8',
  },

  // Icing: mint→teal green to match autorouter GRAMET
  icing: {
    none: 'transparent',
    light: 'rgba(170, 230, 205, 0.45)',
    moderate: 'rgba(110, 200, 165, 0.60)',
    severe: 'rgba(45, 130, 100, 0.75)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(170, 230, 205, 0.55)',
    moderate: 'rgba(110, 200, 165, 0.70)',
    severe: 'rgba(45, 130, 100, 0.85)',
  },

  // SLD: red overlay
  sld: {
    none: 'transparent',
    light: 'rgba(220, 53, 69, 0.30)',
    moderate: 'rgba(220, 53, 69, 0.45)',
    severe: 'rgba(220, 53, 69, 0.60)',
  },

  convective: {
    ...STANDARD_THEME.convective,
    cbLabelColor: {
      moderate: 'rgba(255, 180, 40, 1.0)',
      high: 'rgba(255, 80, 60, 1.0)',
      extreme: 'rgba(255, 50, 30, 1.0)',
    },
  },

  // Slightly cooler obscuration palette so the band sits naturally next
  // to the GRAMET green icing without competing for attention.
  obscuration: {
    lifr: 'rgba(155, 90, 220, 0.65)',
    ifr: 'rgba(220, 70, 70, 0.55)',
    mvfr: 'rgba(230, 160, 50, 0.50)',
    hatchColor: 'rgba(255, 255, 255, 0.60)',
    hatchSpacingPx: 8,
    hatchLineWidth: 1.5,
  },

  // Soft cloud config
  softClouds: {
    fillRgb: [255, 255, 255],
    coverageAlpha: { OVC: 0.85, BKN: 0.65, SCT: 0.45, FEW: 0.15 },
    featherFraction: 0.15,
  },
} as CrossSectionTheme & { sld: Record<string, string> };

// --- Light theme (Windy-inspired: white sky, gray clouds) ---

/** Light-theme temperature ramp.
 *
 *  The cold half is untouched — those hues carry the meaning and must read the
 *  same on every theme. Only the warm end changes: a desaturated blue that
 *  works on the dark sky is nearly invisible on white, so the warm stops move
 *  to a deeper slate-blue. Still blue rather than the conventional grayscale,
 *  for the same reason: gray collides with the cloud bands. */
const LIGHT_IR_TEMP_STOPS: Array<[number, string]> = [
  [15, '#5c7899'],
  [0, '#4e6e94'],
  [-15, '#42648f'],
  [-30, '#2f66b8'],
  ...IR_TEMP_STOPS.filter(([c]) => c <= -40),
];

const LIGHT_THEME: CrossSectionTheme = {
  ...STANDARD_THEME,
  id: 'light' as ThemeId,
  observed: {
    // On a white sky the pale end of the standard ramp vanishes, so the whole
    // ramp shifts darker while keeping the same light→dark direction.
    shareStops: [
      [0.05, '#aab7d6'], [0.07, '#8b9bc7'], [0.10, '#6d7fb6'],
      [0.15, '#5265a4'], [0.22, '#3e4f8e'], [0.35, '#2e3c74'],
      [0.55, '#1f2a58'],
    ],
    tempStops: LIGHT_IR_TEMP_STOPS,
    tempUnknown: 'rgba(92, 120, 153, 0.8)',
    hatchColor: 'rgba(70, 95, 130, 0.6)',
    capColor: '#1f2937',
    capMultiLayerColor: '#b45309',
    noCoverageColor: 'rgba(107, 114, 128, 0.8)',
  },
  nightShading: {
    twilight: 'rgba(60, 60, 110, 0.15)',
    night: 'rgba(30, 30, 70, 0.32)',
  },
  label: 'Light',

  sky: {
    background: '#F8F9FB',
  },

  axes: {
    gridColor: 'rgba(20, 30, 50, 0.18)',
    waypointLineColor: 'rgba(20, 30, 50, 0.32)',
  },

  terrain: {
    fillColor: '#A48256',
    outlineColor: '#7A5E3D',
  },

  temperature: {
    freezingLevel: { color: '#0277BD', width: 2 },
    minus10c: { color: '#1565C0', width: 1.5 },
    minus20c: { color: '#0D47A1', width: 1, dash: [6, 4] },
  },

  // Dash roles per STANDARD: dotted / dashed / dash-dot.
  stability: {
    lcl: { color: '#2E7D32', width: 2, dash: [2, 4] },
    lfc: { color: '#E65100', width: 1.5, dash: [6, 4] },
    el: { color: '#C62828', width: 1.5, dash: [9, 3, 2, 3] },
  },

  reference: {
    cruiseColor: '#212121',
    ceilingColor: '#6A1B9A',
  },

  // Clouds: dark gray on white (Windy-style). Both denseRgb and thinRgb darker
  // than the white sky so even thin clouds remain visible.
  clouds: {
    denseRgb: [70, 80, 95],
    thinRgb: [195, 200, 210],
    coverageAlpha: {
      few: [0.15, 0.30],
      sct: [0.40, 0.55],
      bkn: [0.55, 0.80],
      ovc: [0.70, 0.90],
    },
    fallbackGray: [150, 155, 165],
    hatchGridPx: 8,
    hatchLineWidth: { few: 1, sct: 2, bkn: 5, ovc: 8 },
    hatchColor: 'rgba(50, 60, 75, 0.45)',
  },

  nwpClouds: {
    brightRgb: [225, 228, 232],
    deltaRgb: [155, 160, 165],
    opacityRange: [0.35, 0.70],
  },

  // Icing: lavender→indigo, same family as standard but slightly more saturated
  icing: {
    none: 'transparent',
    light: 'rgba(170, 140, 220, 0.55)',
    moderate: 'rgba(110, 80, 200, 0.72)',
    severe: 'rgba(60, 30, 145, 0.88)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(170, 140, 220, 0.65)',
    moderate: 'rgba(110, 80, 200, 0.82)',
    severe: 'rgba(60, 30, 145, 0.95)',
  },

  // CAT: oranges→red (yellows would vanish on white)
  cat: {
    none: 'transparent',
    light: 'rgba(255, 152, 0, 0.30)',
    moderate: 'rgba(245, 124, 0, 0.50)',
    severe: 'rgba(198, 40, 40, 0.65)',
  },

  convective: {
    riskColors: {
      none: 'transparent',
      marginal: 'rgba(120, 120, 120, 0.10)',
      low: 'rgba(255, 193, 7, 0.18)',
      moderate: 'rgba(245, 124, 0, 0.28)',
      high: 'rgba(220, 53, 69, 0.35)',
      extreme: 'rgba(136, 14, 79, 0.40)',
    },
    bgWash: {
      marginal: 'rgba(120, 120, 120, 0.05)',
      low: 'rgba(255, 193, 7, 0.10)',
      moderate: 'rgba(245, 124, 0, 0.14)',
      high: 'rgba(220, 53, 69, 0.18)',
      extreme: 'rgba(136, 14, 79, 0.22)',
    },
    towerFill: {
      marginal: 'rgba(120, 120, 120, 0.20)',
      low: 'rgba(255, 193, 7, 0.28)',
      moderate: 'rgba(245, 124, 0, 0.40)',
      high: 'rgba(220, 53, 69, 0.50)',
      extreme: 'rgba(136, 14, 79, 0.55)',
    },
    hatchColor: {
      marginal: 'rgba(100, 100, 100, 0.25)',
      low: 'rgba(180, 130, 0, 0.35)',
      moderate: 'rgba(200, 80, 0, 0.50)',
      high: 'rgba(180, 30, 30, 0.60)',
      extreme: 'rgba(100, 10, 50, 0.70)',
    },
    stripColor: {
      marginal: 'rgba(120, 120, 120, 0.50)',
      low: 'rgba(255, 193, 7, 0.65)',
      moderate: 'rgba(245, 124, 0, 0.80)',
      high: 'rgba(220, 53, 69, 0.88)',
      extreme: 'rgba(136, 14, 79, 0.92)',
    },
    edgeColor: {
      marginal: 'rgba(100, 100, 100, 0.35)',
      low: 'rgba(180, 130, 0, 0.45)',
      moderate: 'rgba(200, 80, 0, 0.60)',
      high: 'rgba(180, 30, 30, 0.70)',
      extreme: 'rgba(100, 10, 50, 0.80)',
    },
    cbLabelColor: {
      moderate: 'rgba(200, 80, 0, 0.92)',
      high: 'rgba(180, 30, 30, 0.95)',
      extreme: 'rgba(100, 10, 50, 1.00)',
    },
  },

  inversion: {
    baseRgb: [194, 24, 91],
    opacityParams: { floor: 0.20, scale: 0.55, maxStrengthC: 3, cap: 0.75 },
  },

  // Darker hatch on light theme — white-on-pale-fog vanishes.
  obscuration: {
    lifr: 'rgba(126, 34, 206, 0.55)',
    ifr: 'rgba(220, 38, 38, 0.50)',
    mvfr: 'rgba(202, 138, 4, 0.50)',
    hatchColor: 'rgba(30, 30, 45, 0.55)',
    hatchSpacingPx: 8,
    hatchLineWidth: 1.5,
  },

  // Soft cloud fill: dark gray on white (inverts the GRAMET white-on-blue)
  softClouds: {
    fillRgb: [70, 80, 95],
    coverageAlpha: { OVC: 0.55, BKN: 0.40, SCT: 0.25, FEW: 0.10 },
    featherFraction: 0.15,
  },
};

// --- Theme registry ---

export const THEMES: Record<ThemeId, CrossSectionTheme> = {
  'standard': STANDARD_THEME,
  'high-contrast': HIGH_CONTRAST_THEME,
  'gramet': GRAMET_THEME,
  'light': LIGHT_THEME,
};

// --- Module-level getter/setter ---

let activeTheme: CrossSectionTheme = STANDARD_THEME;

export function getActiveTheme(): CrossSectionTheme {
  return activeTheme;
}

export function setActiveTheme(id: ThemeId): void {
  const theme = THEMES[id];
  if (theme) {
    activeTheme = theme;
  }
}

export function getActiveThemeId(): ThemeId {
  return activeTheme.id;
}
