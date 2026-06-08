/**
 * Skew-T interaction: hover tooltip and linked cursor.
 *
 * On hover: draws a horizontal crosshair at the pressure level
 * and shows a tooltip with all values at that level.
 *
 * Exposes callbacks for linked cursor sync with cross-section.
 */

import type { SkewTTransform } from './skewt-transform';
import type { SoundingProfileData, SoundingProfileLevel } from './types';
import type { CompareModelDataset } from './compare-renderer';
import { isDarkTheme } from '../interaction-utils';
import { altitudeToPressure, pressureToAltitudeFt } from '../../utils/atmo';

export interface SkewTInteractionCallbacks {
  /** Called with altitude in ft when hovering, undefined when leaving. */
  onHoverAltitude?: (altFt: number | undefined) => void;
}

export interface SkewTInteractionHandle {
  /** Set the external hover altitude (from cross-section). */
  setExternalHoverAlt(altFt: number | null): void;
  /** Update data after re-fetch. */
  update(data: SoundingProfileData | null): void;
  /** Clean up. */
  destroy(): void;
}

/**
 * Attach hover interaction to the Skew-T overlay canvas.
 */
export function attachSkewTInteraction(
  overlayCanvas: HTMLCanvasElement,
  container: HTMLElement,
  getTransform: () => SkewTTransform | null,
  getData: () => SoundingProfileData | null,
  callbacks: SkewTInteractionCallbacks,
): SkewTInteractionHandle {
  overlayCanvas.style.pointerEvents = 'auto';
  overlayCanvas.style.cursor = 'crosshair';

  let tooltip: HTMLElement | null = null;
  let currentData = getData();
  let externalAltFt: number | null = null;

  function handleMouseMove(e: MouseEvent): void {
    const transform = getTransform();
    if (!transform || !currentData) return;

    const rect = overlayCanvas.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const plot = transform.plotArea;

    if (y < plot.top || y > plot.bottom) {
      clearOverlay(transform);
      hideTooltip();
      callbacks.onHoverAltitude?.(undefined);
      return;
    }

    const pressureHPa = transform.yToPressure(y);
    const level = findClosestLevel(currentData.levels, pressureHPa);

    renderHoverOverlay(transform, y);
    if (level) {
      showLevelTooltip(e, level, currentData);
      // Convert pressure to approximate altitude for cross-section sync
      const altFt = level.altitude_ft ?? pressureToAltitudeFt(pressureHPa);
      callbacks.onHoverAltitude?.(altFt);
    }
  }

  function handleMouseLeave(): void {
    const transform = getTransform();
    if (transform) clearOverlay(transform);
    hideTooltip();
    callbacks.onHoverAltitude?.(undefined);
  }

  function renderHoverOverlay(transform: SkewTTransform, hoverY: number): void {
    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    overlayCanvas.width = rect.width * dpr;
    overlayCanvas.height = rect.height * dpr;
    const ctx = overlayCanvas.getContext('2d')!;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const plot = transform.plotArea;
    const dark = isDarkTheme();

    // Horizontal crosshair line
    ctx.strokeStyle = dark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.4)';
    ctx.lineWidth = 0.8;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(plot.left, hoverY);
    ctx.lineTo(plot.right, hoverY);
    ctx.stroke();
    ctx.setLineDash([]);

    // External hover from cross-section
    if (externalAltFt !== null) {
      const extP = altitudeToPressure(externalAltFt);
      if (extP !== null && transform.isPressureVisible(extP)) {
        const extY = transform.pressureToY(extP);
        ctx.strokeStyle = dark ? 'rgba(100,180,255,0.6)' : 'rgba(30,100,200,0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(plot.left, extY);
        ctx.lineTo(plot.right, extY);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  function clearOverlay(transform: SkewTTransform): void {
    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    overlayCanvas.width = rect.width * dpr;
    overlayCanvas.height = rect.height * dpr;
    const ctx = overlayCanvas.getContext('2d')!;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    // Still draw external hover line if present
    if (externalAltFt !== null) {
      const plot = transform.plotArea;
      const dark = isDarkTheme();
      const extP = altitudeToPressure(externalAltFt);
      if (extP !== null && transform.isPressureVisible(extP)) {
        const extY = transform.pressureToY(extP);
        ctx.strokeStyle = dark ? 'rgba(100,180,255,0.6)' : 'rgba(30,100,200,0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(plot.left, extY);
        ctx.lineTo(plot.right, extY);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  function showLevelTooltip(e: MouseEvent, level: SoundingProfileLevel, data: SoundingProfileData): void {
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'skewt-tooltip';
      document.body.appendChild(tooltip);
    }

    const altFt = level.altitude_ft ?? pressureToAltitudeFt(level.pressure_hpa);
    const fl = Math.round(altFt / 100).toString().padStart(3, '0');

    let html = `<div class="skewt-tooltip-header">${level.pressure_hpa} hPa · FL${fl}</div>`;
    html += '<div class="skewt-tooltip-body">';

    html += row('T', level.temperature_c, '°C');
    html += row('Td', level.dewpoint_c, '°C');
    html += row('DD', level.dewpoint_depression_c, '°C');
    html += row('RH', level.relative_humidity_pct, '%');
    html += row('CC', level.cloud_area_fraction_pct, '%');
    if (level.wind_speed_kt != null && level.wind_direction_deg != null) {
      html += `<div>${Math.round(level.wind_direction_deg)}°/${Math.round(level.wind_speed_kt)}kt</div>`;
      if (data.track_deg != null) {
        const rel = (level.wind_direction_deg - data.track_deg) * Math.PI / 180;
        const hw = level.wind_speed_kt * Math.cos(rel);
        const xw = level.wind_speed_kt * Math.sin(rel);
        const hwLabel = hw > 0 ? 'HW' : 'TW';
        html += `<div>${hwLabel} ${Math.abs(Math.round(hw))}kt · XW ${Math.abs(Math.round(xw))}kt</div>`;
      }
    }
    html += row('θe', level.theta_e_k, 'K');
    html += row('Γ', level.lapse_rate_c_per_km, '°C/km');
    html += row('Icing', level.icing_index, '');
    html += row('SFIP', level.sfip_100, '');
    html += row('w', level.w_fpm, 'ft/min');

    html += '</div>';
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';

    // Position near cursor
    const tx = e.clientX + 12;
    const ty = e.clientY - 10;
    const maxX = window.innerWidth - tooltip.offsetWidth - 8;
    tooltip.style.left = `${Math.min(tx, maxX)}px`;
    tooltip.style.top = `${ty}px`;
  }

  function hideTooltip(): void {
    if (tooltip) tooltip.style.display = 'none';
  }

  overlayCanvas.addEventListener('mousemove', handleMouseMove);
  overlayCanvas.addEventListener('mouseleave', handleMouseLeave);

  return {
    setExternalHoverAlt(altFt: number | null): void {
      externalAltFt = altFt;
      const transform = getTransform();
      if (transform) clearOverlay(transform);
    },
    update(data: SoundingProfileData | null): void {
      currentData = data;
    },
    destroy(): void {
      overlayCanvas.removeEventListener('mousemove', handleMouseMove);
      overlayCanvas.removeEventListener('mouseleave', handleMouseLeave);
      if (tooltip) { tooltip.remove(); tooltip = null; }
    },
  };
}

function findClosestLevel(levels: SoundingProfileLevel[], pressureHPa: number): SoundingProfileLevel | null {
  let best: SoundingProfileLevel | null = null;
  let bestDist = Infinity;
  for (const lv of levels) {
    const dist = Math.abs(lv.pressure_hpa - pressureHPa);
    if (dist < bestDist) { bestDist = dist; best = lv; }
  }
  return best;
}

function row(label: string, value: number | null | undefined, unit: string): string {
  if (value == null) return '';
  const fmt = Math.abs(value) >= 10 ? Math.round(value).toString() : value.toFixed(1);
  return `<div>${label}: ${fmt}${unit ? ' ' + unit : ''}</div>`;
}

// --- Compare mode interaction ---

export interface SkewTCompareInteractionHandle {
  setExternalHoverAlt(altFt: number | null): void;
  update(datasets: CompareModelDataset[]): void;
  destroy(): void;
}

/**
 * Attach hover interaction for Skew-T compare mode.
 * Tooltip shows per-model values at the hovered pressure level.
 */
export function attachSkewTCompareInteraction(
  overlayCanvas: HTMLCanvasElement,
  container: HTMLElement,
  getTransform: () => SkewTTransform | null,
  getDatasets: () => CompareModelDataset[],
  callbacks: SkewTInteractionCallbacks,
): SkewTCompareInteractionHandle {
  overlayCanvas.style.pointerEvents = 'auto';
  overlayCanvas.style.cursor = 'crosshair';

  let tooltip: HTMLElement | null = null;
  let currentDatasets = getDatasets();
  let externalAltFt: number | null = null;

  function handleMouseMove(e: MouseEvent): void {
    const transform = getTransform();
    if (!transform || currentDatasets.length === 0) return;

    const rect = overlayCanvas.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const plot = transform.plotArea;

    if (y < plot.top || y > plot.bottom) {
      clearCompareOverlay(transform);
      hideTooltip();
      callbacks.onHoverAltitude?.(undefined);
      return;
    }

    const pressureHPa = transform.yToPressure(y);
    renderCompareHoverOverlay(transform, y);
    showCompareTooltip(e, pressureHPa, currentDatasets);

    // Use primary model's level for altitude sync
    const primary = currentDatasets.find(d => d.isPrimary) ?? currentDatasets[0];
    const level = findClosestLevel(primary.data.levels, pressureHPa);
    if (level) {
      const altFt = level.altitude_ft ?? pressureToAltitudeFt(pressureHPa);
      callbacks.onHoverAltitude?.(altFt);
    }
  }

  function handleMouseLeave(): void {
    const transform = getTransform();
    if (transform) clearCompareOverlay(transform);
    hideTooltip();
    callbacks.onHoverAltitude?.(undefined);
  }

  function renderCompareHoverOverlay(transform: SkewTTransform, hoverY: number): void {
    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    overlayCanvas.width = rect.width * dpr;
    overlayCanvas.height = rect.height * dpr;
    const ctx = overlayCanvas.getContext('2d')!;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const plot = transform.plotArea;
    const dark = isDarkTheme();

    ctx.strokeStyle = dark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.4)';
    ctx.lineWidth = 0.8;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(plot.left, hoverY);
    ctx.lineTo(plot.right, hoverY);
    ctx.stroke();
    ctx.setLineDash([]);

    if (externalAltFt !== null) {
      const extP = altitudeToPressure(externalAltFt);
      if (extP !== null && transform.isPressureVisible(extP)) {
        const extY = transform.pressureToY(extP);
        ctx.strokeStyle = dark ? 'rgba(100,180,255,0.6)' : 'rgba(30,100,200,0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(plot.left, extY);
        ctx.lineTo(plot.right, extY);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  function clearCompareOverlay(transform: SkewTTransform): void {
    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    overlayCanvas.width = rect.width * dpr;
    overlayCanvas.height = rect.height * dpr;
    const ctx = overlayCanvas.getContext('2d')!;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    if (externalAltFt !== null) {
      const plot = transform.plotArea;
      const dark = isDarkTheme();
      const extP = altitudeToPressure(externalAltFt);
      if (extP !== null && transform.isPressureVisible(extP)) {
        const extY = transform.pressureToY(extP);
        ctx.strokeStyle = dark ? 'rgba(100,180,255,0.6)' : 'rgba(30,100,200,0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(plot.left, extY);
        ctx.lineTo(plot.right, extY);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  function showCompareTooltip(e: MouseEvent, pressureHPa: number, datasets: CompareModelDataset[]): void {
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'skewt-tooltip';
      document.body.appendChild(tooltip);
    }

    // Use primary model for altitude/FL
    const primary = datasets.find(d => d.isPrimary) ?? datasets[0];
    const refLevel = findClosestLevel(primary.data.levels, pressureHPa);
    if (!refLevel) { hideTooltip(); return; }

    const altFt = refLevel.altitude_ft ?? pressureToAltitudeFt(refLevel.pressure_hpa);
    const fl = Math.round(altFt / 100).toString().padStart(3, '0');

    let html = `<div class="skewt-tooltip-header">${refLevel.pressure_hpa} hPa \u00b7 FL${fl}</div>`;
    html += '<table class="skewt-tooltip-compare">';
    html += '<tr><th></th><th>T</th><th>Td</th><th>Wind</th>';
    if (primary.data.track_deg != null) html += '<th>HW</th>';
    html += '</tr>';

    for (const ds of datasets) {
      const level = findClosestLevel(ds.data.levels, pressureHPa);
      if (!level) continue;

      const bold = ds.isPrimary ? ' style="font-weight:600"' : '';
      html += `<tr${bold}>`;
      html += `<td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${ds.color};margin-right:4px"></span>${ds.model.toUpperCase()}</td>`;
      html += `<td>${fmtV(level.temperature_c, '°C')}</td>`;
      html += `<td>${fmtV(level.dewpoint_c, '°C')}</td>`;
      if (level.wind_speed_kt != null && level.wind_direction_deg != null) {
        html += `<td>${Math.round(level.wind_direction_deg)}°/${Math.round(level.wind_speed_kt)}kt</td>`;
        if (ds.data.track_deg != null) {
          const rel = (level.wind_direction_deg - ds.data.track_deg) * Math.PI / 180;
          const hw = level.wind_speed_kt * Math.cos(rel);
          const hwLabel = hw > 0 ? 'HW' : 'TW';
          html += `<td>${hwLabel}${Math.abs(Math.round(hw))}</td>`;
        }
      } else {
        html += '<td>-</td>';
        if (ds.data.track_deg != null) html += '<td>-</td>';
      }
      html += '</tr>';
    }
    html += '</table>';

    tooltip.innerHTML = html;
    tooltip.style.display = 'block';

    const tx = e.clientX + 12;
    const ty = e.clientY - 10;
    const maxX = window.innerWidth - tooltip.offsetWidth - 8;
    tooltip.style.left = `${Math.min(tx, maxX)}px`;
    tooltip.style.top = `${ty}px`;
  }

  function hideTooltip(): void {
    if (tooltip) tooltip.style.display = 'none';
  }

  overlayCanvas.addEventListener('mousemove', handleMouseMove);
  overlayCanvas.addEventListener('mouseleave', handleMouseLeave);

  return {
    setExternalHoverAlt(altFt: number | null): void {
      externalAltFt = altFt;
      const transform = getTransform();
      if (transform) clearCompareOverlay(transform);
    },
    update(datasets: CompareModelDataset[]): void {
      currentDatasets = datasets;
    },
    destroy(): void {
      overlayCanvas.removeEventListener('mousemove', handleMouseMove);
      overlayCanvas.removeEventListener('mouseleave', handleMouseLeave);
      if (tooltip) { tooltip.remove(); tooltip = null; }
    },
  };
}

function fmtV(value: number | null | undefined, unit: string): string {
  if (value == null) return '-';
  const fmt = Math.abs(value) >= 10 ? Math.round(value).toString() : value.toFixed(1);
  return `${fmt}${unit}`;
}

