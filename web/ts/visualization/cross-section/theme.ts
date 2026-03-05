/** Cross-section color theme system.
 *
 * Centralizes all color values used by cross-section layers into a single
 * typed theme object. Supports multiple built-in themes (standard,
 * high-contrast, colorblind-safe) switchable at runtime.
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
}

// --- Theme IDs ---

export type ThemeId = 'standard' | 'high-contrast' | 'colorblind';

// --- Standard theme (current production values) ---

const STANDARD_THEME: CrossSectionTheme = {
  id: 'standard',
  label: 'Standard',

  sky: {
    background: '#87CEEB',
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
    denseRgb: [170, 170, 175],
    thinRgb: [245, 245, 248],
    coverageAlpha: {
      sct: [0.40, 0.55],
      bkn: [0.50, 0.80],
      ovc: [0.60, 0.92],
    },
    fallbackGray: [190, 190, 195],
  },

  nwpClouds: {
    brightRgb: [230, 233, 245],
    deltaRgb: [65, 63, 60],
    opacityRange: [0.25, 0.50],
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
};

// --- High-contrast theme ---

const HIGH_CONTRAST_THEME: CrossSectionTheme = {
  ...STANDARD_THEME,
  id: 'high-contrast',
  label: 'High Contrast',

  sky: {
    background: '#b5dcf0',
  },

  axes: {
    gridColor: 'rgba(255, 255, 255, 0.50)',
    waypointLineColor: 'rgba(255, 255, 255, 0.60)',
  },

  terrain: {
    fillColor: '#7a6040',
    outlineColor: '#524028',
  },

  temperature: {
    freezingLevel: { color: '#00acc1', width: 2.5 },
    minus10c: { color: '#1e88e5', width: 2 },
    minus20c: { color: '#0d1b5e', width: 1.5, dash: [6, 4] },
  },

  stability: {
    lcl: { color: '#388e3c', width: 2.5, dash: [6, 4] },
    lfc: { color: '#ef6c00', width: 2, dash: [6, 4] },
    el: { color: '#d32f2f', width: 2, dash: [6, 4] },
  },

  icing: {
    none: 'transparent',
    light: 'rgba(100, 149, 237, 0.50)',
    moderate: 'rgba(255, 165, 0, 0.60)',
    severe: 'rgba(220, 53, 69, 0.70)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(100, 149, 237, 0.65)',
    moderate: 'rgba(255, 165, 0, 0.70)',
    severe: 'rgba(220, 53, 69, 0.80)',
  },

  cat: {
    none: 'transparent',
    light: 'rgba(255, 193, 7, 0.35)',
    moderate: 'rgba(255, 152, 0, 0.55)',
    severe: 'rgba(220, 53, 69, 0.70)',
  },

  convective: {
    ...STANDARD_THEME.convective,
    towerFill: {
      marginal: 'rgba(180, 180, 180, 0.25)',
      low: 'rgba(255, 235, 59, 0.28)',
      moderate: 'rgba(255, 152, 0, 0.38)',
      high: 'rgba(220, 53, 69, 0.45)',
      extreme: 'rgba(183, 28, 28, 0.50)',
    },
    hatchColor: {
      marginal: 'rgba(140, 140, 140, 0.25)',
      low: 'rgba(180, 160, 0, 0.30)',
      moderate: 'rgba(200, 100, 0, 0.50)',
      high: 'rgba(200, 40, 40, 0.55)',
      extreme: 'rgba(150, 20, 20, 0.65)',
    },
  },

  inversion: {
    baseRgb: [233, 30, 99],
    opacityParams: { floor: 0.25, scale: 0.55, maxStrengthC: 3, cap: 0.80 },
  },
};

// --- Colorblind-safe theme (blue/orange, no red/green) ---

const COLORBLIND_SAFE_THEME: CrossSectionTheme = {
  ...STANDARD_THEME,
  id: 'colorblind',
  label: 'Colorblind Safe',

  temperature: {
    freezingLevel: { color: '#0077bb', width: 2 },
    minus10c: { color: '#33bbee', width: 1.5 },
    minus20c: { color: '#004488', width: 1, dash: [6, 4] },
  },

  stability: {
    lcl: { color: '#009988', width: 2, dash: [6, 4] },
    lfc: { color: '#ee7733', width: 1.5, dash: [6, 4] },
    el: { color: '#cc3311', width: 1.5, dash: [4, 2, 1, 2] },
  },

  icing: {
    none: 'transparent',
    light: 'rgba(51, 187, 238, 0.35)',
    moderate: 'rgba(238, 119, 51, 0.45)',
    severe: 'rgba(204, 51, 17, 0.55)',
  },

  sfipIcing: {
    none: 'transparent',
    light: 'rgba(51, 187, 238, 0.50)',
    moderate: 'rgba(238, 119, 51, 0.55)',
    severe: 'rgba(204, 51, 17, 0.65)',
  },

  cat: {
    none: 'transparent',
    light: 'rgba(238, 119, 51, 0.20)',
    moderate: 'rgba(238, 119, 51, 0.40)',
    severe: 'rgba(204, 51, 17, 0.55)',
  },

  convective: {
    riskColors: {
      none: 'transparent',
      marginal: 'rgba(160, 160, 160, 0.08)',
      low: 'rgba(238, 204, 51, 0.10)',
      moderate: 'rgba(238, 119, 51, 0.15)',
      high: 'rgba(204, 51, 17, 0.20)',
      extreme: 'rgba(136, 34, 85, 0.25)',
    },
    bgWash: {
      marginal: 'rgba(200, 200, 200, 0.04)',
      low: 'rgba(238, 204, 51, 0.06)',
      moderate: 'rgba(238, 119, 51, 0.08)',
      high: 'rgba(204, 51, 17, 0.10)',
      extreme: 'rgba(136, 34, 85, 0.14)',
    },
    towerFill: {
      marginal: 'rgba(180, 180, 180, 0.15)',
      low: 'rgba(238, 204, 51, 0.18)',
      moderate: 'rgba(238, 119, 51, 0.25)',
      high: 'rgba(204, 51, 17, 0.30)',
      extreme: 'rgba(136, 34, 85, 0.35)',
    },
    hatchColor: {
      marginal: 'rgba(140, 140, 140, 0.15)',
      low: 'rgba(180, 160, 0, 0.20)',
      moderate: 'rgba(200, 100, 20, 0.35)',
      high: 'rgba(180, 40, 15, 0.40)',
      extreme: 'rgba(120, 30, 70, 0.50)',
    },
    stripColor: {
      marginal: 'rgba(160, 160, 160, 0.4)',
      low: 'rgba(238, 204, 51, 0.5)',
      moderate: 'rgba(238, 119, 51, 0.75)',
      high: 'rgba(204, 51, 17, 0.85)',
      extreme: 'rgba(136, 34, 85, 0.9)',
    },
    edgeColor: {
      marginal: 'rgba(140, 140, 140, 0.25)',
      low: 'rgba(180, 160, 0, 0.3)',
      moderate: 'rgba(200, 100, 20, 0.5)',
      high: 'rgba(180, 40, 15, 0.6)',
      extreme: 'rgba(120, 30, 70, 0.7)',
    },
    cbLabelColor: {
      moderate: 'rgba(200, 100, 20, 0.8)',
      high: 'rgba(180, 40, 15, 0.9)',
      extreme: 'rgba(120, 30, 70, 0.95)',
    },
  },

  inversion: {
    baseRgb: [170, 51, 119],
    opacityParams: { floor: 0.15, scale: 0.5, maxStrengthC: 3, cap: 0.65 },
  },
};

// --- Theme registry ---

export const THEMES: Record<ThemeId, CrossSectionTheme> = {
  'standard': STANDARD_THEME,
  'high-contrast': HIGH_CONTRAST_THEME,
  'colorblind': COLORBLIND_SAFE_THEME,
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
