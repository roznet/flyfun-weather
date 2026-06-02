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

const FRONT_KIND_I18N: Record<FrontKind, string> = {
  cold: 'fronts.kind.cold',
  warm: 'fronts.kind.warm',
  'quasi-stationary': 'fronts.kind.quasi',
};

export function frontColor(kind: FrontKind): string {
  return FRONT_KIND_COLOR[kind] ?? FRONT_KIND_COLOR['quasi-stationary'];
}

/** Marker opacity from persistence: a front that holds across the time window
 *  draws solid; a flickering one (likely an orographic / grid artifact) draws
 *  faint, so the eye trusts it less. Unknown persistence → near-solid. */
export function frontAlpha(c: FrontCrossing): number {
  const p = c.persistence;
  if (p == null) return 0.9;
  return Math.max(0.35, Math.min(1, 0.35 + 0.65 * p));
}

/** Dash from co-location: a boundary carrying real weather (wet / convective)
 *  draws solid; a dry or partly one — a wind shift more than a weather event —
 *  draws dashed and de-emphasized. Mirrors the advisory's wet/dry gate. */
export function frontDash(c: FrontCrossing): number[] {
  return c.co_location === 'wet' || c.co_location === 'convective' ? [] : [6, 4];
}

/** Convective boundaries get an extra glyph — towers can punch through / above
 *  an overflown front, so the cross-section flags them even when the line reads
 *  benign at the crossing level. */
export function frontIsConvective(c: FrontCrossing): boolean {
  return c.co_location === 'convective';
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
