/**
 * Single fixed-width side panel with dual-axis support.
 *
 * Renders up to two variables on a shared pressure Y-axis:
 * - Primary: line + bottom X-axis labels
 * - Secondary: line + top X-axis labels (optional)
 *
 * HW/XW (headwind/crosswind) is included as a variable option.
 */

import { SkewTTransform } from './skewt-transform';
import type { SoundingProfileLevel } from './types';
import { isDarkTheme } from '../interaction-utils';

export const SIDE_PANEL_WIDTH = 110;

export interface VariableDef {
  id: string;
  label: string;
  shortLabel: string;
  unit: string;
  color: string;
  /** Secondary color for dual-line variables (e.g. crosswind). */
  secondaryColor?: string;
  secondaryLabel?: string;
  /** Extract the value(s) from a sounding level. */
  getValue: (lv: SoundingProfileLevel, trackDeg?: number | null) => number | null;
  /** Optional secondary value extractor (e.g. crosswind alongside headwind). */
  getSecondaryValue?: (lv: SoundingProfileLevel, trackDeg?: number | null) => number | null;
  /** Fixed X range [min, max]. If omitted, auto-scaled. */
  fixedRange?: [number, number];
  /** Draw a zero line. */
  zeroLine?: boolean;
}

/** All available side panel variables. */
export const VARIABLE_REGISTRY: VariableDef[] = [
  {
    id: 'headwind',
    label: 'Headwind / Crosswind',
    shortLabel: 'HW/XW',
    unit: 'kt',
    color: '#d04040',
    secondaryColor: '#d09020',
    secondaryLabel: 'XW',
    getValue: (lv, trackDeg) => {
      if (lv.wind_speed_kt == null || lv.wind_direction_deg == null || trackDeg == null) return null;
      const rel = (lv.wind_direction_deg - trackDeg) * Math.PI / 180;
      return lv.wind_speed_kt * Math.cos(rel); // positive = headwind
    },
    getSecondaryValue: (lv, trackDeg) => {
      if (lv.wind_speed_kt == null || lv.wind_direction_deg == null || trackDeg == null) return null;
      const rel = (lv.wind_direction_deg - trackDeg) * Math.PI / 180;
      return lv.wind_speed_kt * Math.sin(rel); // positive = from right
    },
    zeroLine: true,
  },
  {
    id: 'dewpoint_depression',
    label: 'Dewpoint Depression',
    shortLabel: 'DD',
    unit: '°C',
    color: '#e07020',
    getValue: lv => lv.dewpoint_depression_c,
    fixedRange: [0, 15],
  },
  {
    id: 'relative_humidity',
    label: 'Relative Humidity',
    shortLabel: 'RH',
    unit: '%',
    color: '#2090d0',
    getValue: lv => lv.relative_humidity_pct,
    fixedRange: [0, 100],
  },
  {
    id: 'wind_speed',
    label: 'Wind Speed',
    shortLabel: 'Wind',
    unit: 'kt',
    color: '#6060c0',
    getValue: lv => lv.wind_speed_kt,
  },
  {
    id: 'icing_index',
    label: 'Icing (Ogimet-DD)',
    shortLabel: 'Ice-DD',
    unit: '',
    color: '#6495ed',
    getValue: lv => lv.icing_index,
    fixedRange: [0, 100],
  },
  {
    id: 'icing_index_nwp',
    label: 'Icing (Ogimet-NWP)',
    shortLabel: 'Ice-NWP',
    unit: '',
    color: '#4080d0',
    getValue: lv => lv.icing_index_nwp,
    fixedRange: [0, 100],
  },
  {
    id: 'sfip',
    label: 'SFIP Index',
    shortLabel: 'SFIP',
    unit: '',
    color: '#d08020',
    getValue: lv => lv.sfip_100,
    fixedRange: [0, 100],
  },
  {
    id: 'cloud_liquid_water',
    label: 'Cloud Liquid Water',
    shortLabel: 'CLW',
    unit: 'g/m³',
    color: '#20a0a0',
    getValue: lv => lv.cloud_liquid_water_g_m3,
  },
  {
    id: 'ice_mixing_ratio',
    label: 'Ice Mixing Ratio',
    shortLabel: 'ICE',
    unit: 'g/kg',
    color: '#8080d0',
    getValue: lv => lv.ice_mixing_ratio_g_kg,
  },
  {
    id: 'lapse_rate',
    label: 'Lapse Rate',
    shortLabel: 'Γ',
    unit: '°C/km',
    color: '#c04040',
    getValue: lv => lv.lapse_rate_c_per_km,
    zeroLine: true,
  },
  {
    id: 'richardson',
    label: 'Richardson Number',
    shortLabel: 'Ri',
    unit: '',
    color: '#d0a020',
    getValue: lv => lv.richardson_number != null && lv.richardson_number < 100 ? lv.richardson_number : null,
  },
  {
    id: 'vertical_velocity',
    label: 'Vertical Velocity',
    shortLabel: 'w',
    unit: 'ft/min',
    color: '#40a040',
    getValue: lv => lv.w_fpm,
    zeroLine: true,
  },
  {
    id: 'theta_e',
    label: 'Equiv. Pot. Temp.',
    shortLabel: 'θe',
    unit: 'K',
    color: '#a04080',
    getValue: lv => lv.theta_e_k,
  },
];

export function getVariableById(id: string): VariableDef | undefined {
  return VARIABLE_REGISTRY.find(v => v.id === id);
}

export interface SidePanelLayout {
  left: number;
  width: number;
  top: number;
  height: number;
  bottom: number;
}

/**
 * Render the side panel with primary and optional secondary variable.
 */
export function renderSidePanel(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  levels: SoundingProfileLevel[],
  primary: VariableDef,
  secondary: VariableDef | null,
  layout: SidePanelLayout,
  trackDeg: number | null,
): void {
  const dark = isDarkTheme();
  const textColor = dark ? '#ccc' : '#444';
  const axisColor = dark ? '#555' : '#999';
  const bgColor = dark ? 'rgba(30, 30, 30, 0.3)' : 'rgba(245, 245, 245, 0.3)';

  ctx.save();

  // Background
  ctx.fillStyle = bgColor;
  ctx.fillRect(layout.left, layout.top, layout.width, layout.height);
  ctx.strokeStyle = axisColor;
  ctx.lineWidth = 0.5;
  ctx.strokeRect(layout.left, layout.top, layout.width, layout.height);

  // Clip
  ctx.beginPath();
  ctx.rect(layout.left, layout.top, layout.width, layout.height);
  ctx.clip();

  // Render primary variable
  const primaryRange = renderVariableLine(ctx, transform, levels, primary, layout, trackDeg);

  // Render primary secondary line (e.g., crosswind for HW/XW)
  if (primary.getSecondaryValue && primary.secondaryColor) {
    renderVariableLine(ctx, transform, levels, {
      ...primary,
      color: primary.secondaryColor,
      getValue: primary.getSecondaryValue,
      getSecondaryValue: undefined,
    }, layout, trackDeg, primaryRange);
  }

  // Render secondary variable (different variable, different scale)
  let secondaryRange: [number, number] | null = null;
  if (secondary) {
    secondaryRange = renderVariableLine(ctx, transform, levels, secondary, layout, trackDeg);
  }

  ctx.restore();

  // Axes labels
  ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';

  // Primary: bottom axis
  if (primaryRange) {
    ctx.fillStyle = primary.color;
    ctx.textBaseline = 'top';
    const y = layout.bottom + 2;
    ctx.textAlign = 'left';
    ctx.fillText(fmt(primaryRange[0]), layout.left, y);
    ctx.textAlign = 'right';
    ctx.fillText(fmt(primaryRange[1]), layout.left + layout.width, y);
    ctx.textAlign = 'center';
    ctx.fillText(`${primary.shortLabel} (${primary.unit})`, layout.left + layout.width / 2, y + 10);
  }

  // Secondary: top axis
  if (secondary && secondaryRange) {
    ctx.fillStyle = secondary.color;
    ctx.textBaseline = 'bottom';
    const y = layout.top - 2;
    ctx.textAlign = 'left';
    ctx.fillText(fmt(secondaryRange[0]), layout.left, y);
    ctx.textAlign = 'right';
    ctx.fillText(fmt(secondaryRange[1]), layout.left + layout.width, y);
    ctx.textAlign = 'center';
    ctx.fillText(`${secondary.shortLabel} (${secondary.unit})`, layout.left + layout.width / 2, y - 10);
  } else if (!secondary) {
    // Title at top when no secondary
    ctx.fillStyle = primary.color;
    ctx.textBaseline = 'bottom';
    ctx.textAlign = 'center';
    ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
    let title = primary.shortLabel;
    if (primary.secondaryLabel) title += ` / ${primary.secondaryLabel}`;
    ctx.fillText(title, layout.left + layout.width / 2, layout.top - 2);
  }
}

/**
 * Render a single variable line. Returns the [min, max] range used.
 * If forceRange is provided, uses that range instead of auto-scaling.
 */
function renderVariableLine(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  levels: SoundingProfileLevel[],
  variable: VariableDef,
  layout: SidePanelLayout,
  trackDeg: number | null,
  forceRange?: [number, number] | null,
): [number, number] | null {
  // Extract data
  const points: Array<{ pressure: number; value: number }> = [];
  for (const lv of levels) {
    const val = variable.getValue(lv, trackDeg);
    if (val !== null && val !== undefined && isFinite(val)) {
      points.push({ pressure: lv.pressure_hpa, value: val });
    }
  }
  if (points.length < 2) return null;

  // Compute range
  let xMin: number, xMax: number;
  if (forceRange) {
    [xMin, xMax] = forceRange;
  } else if (variable.fixedRange) {
    [xMin, xMax] = variable.fixedRange;
  } else {
    const values = points.map(p => p.value);
    xMin = Math.min(...values);
    xMax = Math.max(...values);
    const margin = (xMax - xMin) * 0.1 || 1;
    xMin -= margin;
    xMax += margin;
    // Symmetrize around zero for zeroLine variables
    if (variable.zeroLine) {
      const absMax = Math.max(Math.abs(xMin), Math.abs(xMax));
      xMin = -absMax;
      xMax = absMax;
    }
  }
  const xRange = xMax - xMin || 1;

  // Zero line
  if (variable.zeroLine && xMin < 0 && xMax > 0) {
    const zeroX = layout.left + ((0 - xMin) / xRange) * layout.width;
    ctx.strokeStyle = isDarkTheme() ? '#555' : '#999';
    ctx.lineWidth = 0.5;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(zeroX, layout.top);
    ctx.lineTo(zeroX, layout.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Draw line
  ctx.strokeStyle = variable.color;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  let first = true;
  for (const pt of points) {
    const y = transform.pressureToY(pt.pressure);
    const x = layout.left + ((pt.value - xMin) / xRange) * layout.width;
    if (first) { ctx.moveTo(x, y); first = false; }
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  return [xMin, xMax];
}

function fmt(v: number): string {
  if (Math.abs(v) >= 100) return Math.round(v).toString();
  if (Math.abs(v) >= 10) return v.toFixed(0);
  return v.toFixed(1);
}
