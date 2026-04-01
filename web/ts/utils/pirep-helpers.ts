/** Shared PIREP severity/hazard helpers used by both list UI and map. */

import type { PirepResponse } from '../adapters/pirep-adapter';

export const SEVERITY_COLORS: Record<string, string> = {
  none: '#4caf50',
  trace: '#fbc02d',
  light: '#fbc02d',
  moderate: '#ff9800',
  severe: '#f44336',
};

const SEVERITY_ORDER = ['none', 'trace', 'light', 'moderate', 'severe'];

export function maxSeverity(pirep: PirepResponse): string {
  let max = 0;
  if (pirep.icing_intensity) max = Math.max(max, SEVERITY_ORDER.indexOf(pirep.icing_intensity));
  if (pirep.turbulence_intensity) max = Math.max(max, SEVERITY_ORDER.indexOf(pirep.turbulence_intensity));
  return SEVERITY_ORDER[max] || 'none';
}

/** HTML entity string for the primary hazard icon(s) — no wrapper spans. */
export function hazardIconsRaw(pirep: PirepResponse): string {
  const parts: string[] = [];
  if (pirep.icing_intensity && pirep.icing_intensity !== 'none') parts.push('&#10052;');
  if (pirep.turbulence_intensity && pirep.turbulence_intensity !== 'none') parts.push('&#9084;');
  if (pirep.in_cloud || pirep.ceiling_msl_ft != null || pirep.tops_msl_ft != null) parts.push('&#9729;');
  if (parts.length === 0) parts.push('&#10003;');
  return parts.join('');
}
