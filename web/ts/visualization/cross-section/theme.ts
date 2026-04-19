/** Cross-section color theme system.
 *
 * Centralizes all color values used by cross-section layers into a single
 * typed theme object. Supports multiple built-in themes (standard,
 * high-contrast) switchable at runtime.
 *
 * Usage: import { getActiveTheme } from './theme';
 *        const t = getActiveTheme();
 *        ctx.fillStyle = t.terrain.fillColor;
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

  coverageOpacity: Record<string, number>;

  /** Distinct colors assigned to models in consensus-outline mode. */
  compareModelColors: string[];
}

// --- Theme IDs ---

export type ThemeId = 'standard' | 'high-contrast' | 'gramet';

// --- Standard theme (current production values) ---

const STANDARD_THEME: CrossSectionTheme = {
  id: 'standard',
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

  stability: {
    lcl: { color: '#4caf50', width: 2, dash: [6, 4] },
    lfc: { color: '#ff9800', width: 1.5, dash: [6, 4] },
    el: { color: '#f44336', width: 1.5, dash: [6, 4] },
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
    light: 'rgba(100, 149, 237, 0.35)',
    moderate: 'rgba(255, 165, 0, 0.45)',
    severe: 'rgba(220, 53, 69, 0.55)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(100, 149, 237, 0.50)',
    moderate: 'rgba(255, 165, 0, 0.55)',
    severe: 'rgba(220, 53, 69, 0.65)',
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

  coverageOpacity: {
    sct: 0.25,
    bkn: 0.50,
    ovc: 0.75,
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

  stability: {
    lcl: { color: '#69f0ae', width: 2.5, dash: [6, 4] },
    lfc: { color: '#ffab40', width: 2, dash: [6, 4] },
    el: { color: '#ff5252', width: 2, dash: [6, 4] },
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
    light: 'rgba(91, 184, 232, 0.55)',
    moderate: 'rgba(24, 120, 184, 0.65)',
    severe: 'rgba(26, 24, 80, 0.75)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(91, 184, 232, 0.65)',
    moderate: 'rgba(24, 120, 184, 0.75)',
    severe: 'rgba(26, 24, 80, 0.85)',
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
};

// --- GRAMET theme (CloudPath-inspired, optimized for soft cloud overlays) ---

const GRAMET_THEME: CrossSectionTheme = {
  ...STANDARD_THEME,
  id: 'gramet' as ThemeId,
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
    minus10c: { color: '#22CC44', width: 1.5, dash: [6, 4] },
    minus20c: { color: '#22CC44', width: 1, dash: [4, 4] },
  },

  reference: {
    cruiseColor: '#e0e0e0',
    ceilingColor: '#ce93d8',
  },

  // Icing: blue tint like CloudPath
  icing: {
    none: 'transparent',
    light: 'rgba(91, 184, 232, 0.40)',
    moderate: 'rgba(24, 120, 184, 0.55)',
    severe: 'rgba(10, 40, 120, 0.70)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(91, 184, 232, 0.50)',
    moderate: 'rgba(24, 120, 184, 0.65)',
    severe: 'rgba(10, 40, 120, 0.80)',
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

  // Soft cloud config
  softClouds: {
    fillRgb: [255, 255, 255],
    coverageAlpha: { OVC: 0.85, BKN: 0.65, SCT: 0.45, FEW: 0.15 },
    featherFraction: 0.15,
  },
} as CrossSectionTheme & { sld: Record<string, string>; softClouds: any };

// --- Theme registry ---

export const THEMES: Record<ThemeId, CrossSectionTheme> = {
  'standard': STANDARD_THEME,
  'high-contrast': HIGH_CONTRAST_THEME,
  'gramet': GRAMET_THEME,
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
