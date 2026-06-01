/** Shared styling for the experimental front overlays (#196): route-map symbols
 *  and cross-section markers use the same kind→color / intensity→weight mapping
 *  so the two surfaces read consistently.
 *
 *  Colors follow surface-analysis convention (cold=blue, warm=red,
 *  quasi-stationary=purple). These fronts are free-atmosphere air-mass
 *  boundaries from Hewson θe diagnostics — advisory-only, not drawn SIGWX. */

import type { FrontCrossing, FrontIntensity, FrontKind } from '../types/fronts';
import { t } from '../i18n/i18n';

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

const FRONT_KIND_I18N: Record<FrontKind, string> = {
  cold: 'fronts.kind.cold',
  warm: 'fronts.kind.warm',
  'quasi-stationary': 'fronts.kind.quasi',
};

export function frontColor(kind: FrontKind): string {
  return FRONT_KIND_COLOR[kind] ?? FRONT_KIND_COLOR['quasi-stationary'];
}

export function frontKindLabel(kind: FrontKind): string {
  return t(FRONT_KIND_I18N[kind] ?? FRONT_KIND_I18N['quasi-stationary']);
}

/** One-line pilot-facing tooltip for a crossing: kind, intensity, Δθe,
 *  advection. Intensity is kept as the raw technical token (sharp / classical /
 *  significant) — Δθe / K/h are universal units. */
export function frontTooltip(c: FrontCrossing): string {
  const tail =
    c.advection > 1 ? ` · ${t('fronts.trend.deteriorating')}`
    : c.advection < -1 ? ` · ${t('fronts.trend.improving')}`
    : '';
  return t('fronts.tooltip.crossing', {
    kind: frontKindLabel(c.kind),
    intensity: c.intensity,
    dtheta: c.delta_theta_e.toFixed(1),
    adv: `${c.advection >= 0 ? '+' : ''}${c.advection.toFixed(1)}`,
    tail,
  });
}

/** Tooltip for the nearest off-track front closing on the route. */
export function frontOfftrackTooltip(
  distanceKm: number,
  closingKmPerH: number | null | undefined,
): string {
  const rate = closingKmPerH != null ? ` ${closingKmPerH.toFixed(0)} km/h` : '';
  return t('fronts.tooltip.offtrack', { dist: distanceKm.toFixed(0), rate });
}
