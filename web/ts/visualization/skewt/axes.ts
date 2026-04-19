/**
 * Renders axes and labels for the Skew-T diagram:
 * - Left Y-axis: pressure (hPa)
 * - Right Y-axis: flight level (FL)
 * - Bottom X-axis: temperature (°C), following the skew
 *
 * Also renders level markers (LCL, LFC, EL, freezing level)
 * and the indices panel.
 */

import { SkewTTransform } from './skewt-transform';
import { SoundingProfileData, PlotArea } from './types';
import { isDarkTheme, cssVar } from '../interaction-utils';

// Standard pressure levels to label on the Y-axis
const PRESSURE_LABELS = [1000, 925, 850, 700, 500, 400, 300, 250];

// Standard atmosphere pressure → FL mapping
const PRESSURE_TO_FL: Record<number, string> = {
  1000: '000', 925: '025', 850: '050', 700: '100',
  600: '140', 500: '185', 400: '235', 300: '300', 250: '340',
};

// Level marker styles
const MARKERS: Record<string, { label: string; color: string; dash: number[] }> = {
  lcl: { label: 'LCL', color: '#30a030', dash: [4, 4] },
  lfc: { label: 'LFC', color: '#e08020', dash: [4, 4] },
  el:  { label: 'EL',  color: '#d03030', dash: [4, 4] },
  freezing: { label: '0°C', color: '#00b0dc', dash: [6, 3] },
};

/** Render pressure axis labels on the left and FL labels on the right. */
export function renderAxes(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
): void {
  const plot = transform.plotArea;
  const dark = isDarkTheme();
  const textColor = dark ? '#ccc' : '#444';
  const axisColor = dark ? '#555' : '#999';

  ctx.save();

  // Plot area border
  ctx.strokeStyle = axisColor;
  ctx.lineWidth = 1;
  ctx.strokeRect(plot.left, plot.top, plot.width, plot.height);

  // Y-axis labels
  ctx.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'middle';

  for (const p of PRESSURE_LABELS) {
    if (!transform.isPressureVisible(p)) continue;
    const y = transform.pressureToY(p);

    // Left: pressure in hPa
    ctx.fillStyle = textColor;
    ctx.textAlign = 'right';
    ctx.fillText(`${p}`, plot.left - 4, y);

    // Right: flight level
    const fl = PRESSURE_TO_FL[p];
    if (fl) {
      ctx.textAlign = 'left';
      ctx.fillText(`FL${fl}`, plot.right + 4, y);
    }
  }

  // Bottom: temperature labels along the skew
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const bottomP = transform.config.pBottom;
  for (let t = -60; t <= 40; t += 10) {
    const x = transform.temperatureToX(t, bottomP);
    if (x >= plot.left - 5 && x <= plot.right + 5) {
      ctx.fillStyle = t === 0 ? '#00b0dc' : textColor;
      ctx.fillText(`${t}°`, x, plot.bottom + 4);
    }
  }

  // Axis titles
  ctx.fillStyle = textColor;
  ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';

  // Left title (rotated)
  ctx.save();
  ctx.translate(plot.left - 30, plot.top + plot.height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('Pressure (hPa)', 0, 0);
  ctx.restore();

  ctx.restore();
}

/** Render horizontal dashed lines at LCL, LFC, EL, and freezing level. */
export function renderLevelMarkers(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  data: SoundingProfileData,
): void {
  const plot = transform.plotArea;
  const indices = data.indices;
  if (!indices) return;

  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.left, plot.top, plot.width, plot.height);
  ctx.clip();

  const markerPressures: Array<{ key: string; pressureHPa: number | null }> = [
    { key: 'lcl', pressureHPa: indices.lcl_pressure_hpa as number | null },
    { key: 'lfc', pressureHPa: indices.lfc_pressure_hpa as number | null },
    { key: 'el', pressureHPa: indices.el_pressure_hpa as number | null },
    { key: 'freezing', pressureHPa: pressureFromAltitude(indices.freezing_level_ft as number | null) },
  ];

  ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textBaseline = 'bottom';
  ctx.textAlign = 'left';

  for (const { key, pressureHPa } of markerPressures) {
    if (pressureHPa === null || pressureHPa === undefined) continue;
    if (!transform.isPressureVisible(pressureHPa)) continue;

    const marker = MARKERS[key];
    const y = transform.pressureToY(pressureHPa);

    ctx.strokeStyle = marker.color;
    ctx.lineWidth = 1.2;
    ctx.setLineDash(marker.dash);
    ctx.beginPath();
    ctx.moveTo(plot.left, y);
    ctx.lineTo(plot.right, y);
    ctx.stroke();

    // Label
    ctx.fillStyle = marker.color;
    ctx.setLineDash([]);
    ctx.fillText(marker.label, plot.left + 4, y - 2);
  }

  // Cruise altitude
  if (data.cruise_altitude_ft !== null) {
    const cruiseP = altitudeToPressure(data.cruise_altitude_ft);
    if (cruiseP !== null && transform.isPressureVisible(cruiseP)) {
      const y = transform.pressureToY(cruiseP);
      ctx.strokeStyle = '#666';
      ctx.lineWidth = 1;
      ctx.setLineDash([8, 4]);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.fillStyle = '#666';
      ctx.setLineDash([]);
      ctx.fillText('Cruise', plot.left + 4, y - 2);
    }
  }

  ctx.restore();
}

/** Render thermodynamic indices as a text panel in the top-right corner. */
export function renderIndicesPanel(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  data: SoundingProfileData,
): void {
  const indices = data.indices;
  if (!indices) return;

  const dark = isDarkTheme();
  const plot = transform.plotArea;

  const lines: string[] = [];
  const cape = indices.cape_surface_jkg as number | null;
  const cin = indices.cin_surface_jkg as number | null;
  const li = indices.lifted_index as number | null;
  const pw = indices.precipitable_water_mm as number | null;
  const freezing = indices.freezing_level_ft as number | null;

  if (cape !== null && cape !== undefined) lines.push(`CAPE: ${Math.round(cape)} J/kg`);
  if (cin !== null && cin !== undefined) lines.push(`CIN: ${Math.round(cin)} J/kg`);
  if (li !== null && li !== undefined) lines.push(`LI: ${li.toFixed(1)}`);
  if (pw !== null && pw !== undefined) lines.push(`PW: ${pw.toFixed(1)} mm`);
  if (freezing !== null && freezing !== undefined) lines.push(`0°C: FL${Math.round(freezing / 100).toString().padStart(3, '0')}`);

  if (lines.length === 0) return;

  const lineHeight = 14;
  const padding = 6;
  const panelWidth = 120;
  const panelHeight = lines.length * lineHeight + padding * 2;
  const x = plot.right - panelWidth - 4;
  const y = plot.top + 4;

  // Background
  ctx.fillStyle = dark ? 'rgba(30, 30, 30, 0.85)' : 'rgba(255, 255, 255, 0.85)';
  ctx.fillRect(x, y, panelWidth, panelHeight);
  ctx.strokeStyle = dark ? '#555' : '#ccc';
  ctx.lineWidth = 0.5;
  ctx.strokeRect(x, y, panelWidth, panelHeight);

  // Text
  ctx.font = '11px monospace';
  ctx.fillStyle = dark ? '#ddd' : '#333';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  for (let i = 0; i < lines.length; i++) {
    ctx.fillText(lines[i], x + padding, y + padding + i * lineHeight);
  }
}

/** Approximate standard atmosphere: altitude (ft) → pressure (hPa). */
function altitudeToPressure(altFt: number): number | null {
  if (altFt < 0) return null;
  // Barometric formula (troposphere, T0=288.15K, L=0.0065K/m)
  const altM = altFt / 3.28084;
  return 1013.25 * Math.pow(1 - 0.0065 * altM / 288.15, 5.2561);
}

/** Approximate: freezing level altitude (ft) → pressure (hPa). */
function pressureFromAltitude(altFt: number | null): number | null {
  if (altFt === null || altFt === undefined) return null;
  return altitudeToPressure(altFt);
}
