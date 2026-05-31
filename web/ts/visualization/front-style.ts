/** Shared styling for the experimental front overlays (#196): route-map symbols
 *  and cross-section markers use the same kind→color / intensity→weight mapping
 *  so the two surfaces read consistently.
 *
 *  Colors follow surface-analysis convention (cold=blue, warm=red,
 *  quasi-stationary=purple). These fronts are free-atmosphere air-mass
 *  boundaries from Hewson θe diagnostics — advisory-only, not drawn SIGWX. */

import type { FrontCrossing, FrontIntensity, FrontKind } from '../types/fronts';

export const FRONT_KIND_COLOR: Record<FrontKind, string> = {
  cold: '#2563eb',                // blue
  warm: '#dc2626',                // red
  'quasi-stationary': '#7c3aed',  // purple
};

/** Line weight (px) by intensity — sharper fronts draw heavier. */
export const FRONT_INTENSITY_WEIGHT: Record<FrontIntensity, number> = {
  significant: 2,
  classical: 3,
  sharp: 4.5,
};

/** Dash pattern by intensity; sharp is solid, weaker boundaries dashed. */
export const FRONT_INTENSITY_DASH: Record<FrontIntensity, number[]> = {
  significant: [4, 4],
  classical: [8, 4],
  sharp: [],
};

export function frontColor(kind: FrontKind): string {
  return FRONT_KIND_COLOR[kind] ?? FRONT_KIND_COLOR['quasi-stationary'];
}

export function frontKindLabel(kind: FrontKind): string {
  if (kind === 'cold') return 'Cold front';
  if (kind === 'warm') return 'Warm front';
  return 'Quasi-stationary front';
}

/** One-line pilot-facing tooltip for a crossing: kind, intensity, Δθe, advection. */
export function frontTooltip(c: FrontCrossing): string {
  const advWord =
    c.advection > 1 ? ' · deteriorating' : c.advection < -1 ? ' · improving' : '';
  return (
    `${c.intensity} ${frontKindLabel(c).toLowerCase()}` +
    ` · Δθe ${c.delta_theta_e.toFixed(1)} K` +
    ` · ${c.advection >= 0 ? '+' : ''}${c.advection.toFixed(1)} K/h${advWord}`
  );
}
