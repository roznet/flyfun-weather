/** Surface obscuration heuristic — pure helper.
 *
 * Cross-section pressure-level cloud detectors paint nothing for fog
 * that lives below the lowest pressure level (e.g. 1000 hPa is ~88 ft
 * AGL at a 364 ft field). This helper synthesises a surface band from
 * the surface forecast (visibility, T/Td, low cloud cover) so the
 * cross-section can show a fog/LIFR signal where the map already does.
 *
 * The heuristic deliberately favours simple thresholds (1/3/5 km) over
 * full ICAO LVPs — easier to tune from pilot feedback after the layer
 * is in users' hands. See issue #125 for the spec.
 */

import type { VizSurfaceObscuration, VizCloudLayer } from './types';

export interface ObscurationSurface {
  visibilityM: number | null;
  temperature2mC: number | null;
  dewpoint2mC: number | null;
  /** Open-Meteo low-cloud cover, 0–100. */
  cloudCoverLowPct: number | null;
}

/** Minimal pressure-level info the helper needs to find the layer top. */
export interface ObscurationLevel {
  altitudeFt: number;
  /** Dewpoint depression at this level (T − Td, °C). */
  ddC: number | null;
}

const PRIMARY_VIS_THRESHOLD_M = 5000;
const PRIMARY_AMBER_VIS_M = 3000;
const PRIMARY_RED_VIS_M = 1000;
const SECONDARY_LOW_CC_THRESHOLD_PCT = 80;
const SECONDARY_DD_THRESHOLD_C = 2;
const TOP_DD_DRIES_OUT_C = 4;
const FLOOR_THICKNESS_FT = 500;
const CAP_THICKNESS_FT = 1500;

/**
 * Compute surface obscuration band from the surface forecast.
 * Returns `null` when no trigger fires.
 *
 * - Primary trigger: `visibility_m < 5000` → severity by 1/3/5 km tiers.
 * - Secondary trigger: low cloud ≥ 80% AND surface DD < 2°C → amber.
 *
 * Band geometry:
 *   bottom = terrainFt
 *   top    = min(first level with DD > 4°C, lowest level altitude,
 *               terrainFt + 1500), floored at terrainFt + 500.
 */
export function computeSurfaceObscuration(
  surface: ObscurationSurface,
  pressureLevels: ObscurationLevel[],
  terrainFt: number,
): VizSurfaceObscuration | null {
  const { visibilityM, temperature2mC, dewpoint2mC, cloudCoverLowPct } = surface;

  let severity: 'lifr' | 'ifr' | 'mvfr' | null = null;
  let reason: 'visibility' | 'low_cloud_dd' | null = null;

  if (visibilityM !== null && visibilityM < PRIMARY_VIS_THRESHOLD_M) {
    if (visibilityM < PRIMARY_RED_VIS_M) severity = 'lifr';
    else if (visibilityM < PRIMARY_AMBER_VIS_M) severity = 'ifr';
    else severity = 'mvfr';
    reason = 'visibility';
  } else if (
    // Secondary fires only when the model has no visibility forecast
    // (ECMWF / Météo-France / GEM). For models that report visibility
    // (GFS / ICON / UKMO), trust the primary path: a 100% low-cloud +
    // near-zero DD forecast paired with vis=8 km is "low stratus, not
    // fog" and shouldn't paint a band.
    visibilityM === null
    && cloudCoverLowPct !== null
    && cloudCoverLowPct >= SECONDARY_LOW_CC_THRESHOLD_PCT
    && temperature2mC !== null
    && dewpoint2mC !== null
    && temperature2mC - dewpoint2mC < SECONDARY_DD_THRESHOLD_C
  ) {
    severity = 'ifr';
    reason = 'low_cloud_dd';
  }

  if (severity === null || reason === null) return null;

  const surfaceRhPct = computeRh(temperature2mC, dewpoint2mC);
  const topFt = computeTop(pressureLevels, terrainFt);

  return {
    baseFt: terrainFt,
    topFt,
    severity,
    visM: visibilityM,
    surfaceTC: temperature2mC,
    surfaceTdC: dewpoint2mC,
    surfaceRhPct,
    reason,
  };
}

/**
 * Variant that derives `ObscurationLevel`s from the cross-section's
 * existing `VizCloudLayer` array. Less accurate than per-level data
 * (cloud_layers are synthesised bands, not raw levels), but it's what's
 * available on the briefing-side `VizPoint` today. Levels are used only
 * to find the band top, so the loss of precision is bounded by the
 * `terrainFt + 1500` cap.
 */
export function computeSurfaceObscurationFromCloudLayers(
  surface: ObscurationSurface,
  cloudLayers: VizCloudLayer[],
  terrainFt: number,
): VizSurfaceObscuration | null {
  const levels: ObscurationLevel[] = cloudLayers
    .filter((cl) => cl.baseFt > terrainFt)
    .map((cl) => ({
      altitudeFt: cl.baseFt,
      ddC: cl.meanDewpointDepressionC ?? null,
    }))
    .sort((a, b) => a.altitudeFt - b.altitudeFt);
  return computeSurfaceObscuration(surface, levels, terrainFt);
}

function computeTop(levels: ObscurationLevel[], terrainFt: number): number {
  const cap = terrainFt + CAP_THICKNESS_FT;
  const floor = terrainFt + FLOOR_THICKNESS_FT;

  let top = cap;

  // First level above terrain where DD exceeds the dry-out threshold.
  const dryLevel = levels.find(
    (l) => l.altitudeFt > terrainFt && l.ddC !== null && l.ddC > TOP_DD_DRIES_OUT_C,
  );
  if (dryLevel) top = Math.min(top, dryLevel.altitudeFt);

  // Lowest pressure-level altitude — without exposed level altitudes the
  // caller passes `[]` and this branch is a no-op (cap remains).
  const lowest = levels.find((l) => l.altitudeFt > terrainFt);
  if (lowest) top = Math.min(top, lowest.altitudeFt);

  return Math.max(top, floor);
}

function computeRh(tC: number | null, tdC: number | null): number | null {
  if (tC === null || tdC === null) return null;
  // Magnus formula — good enough for tooltip context.
  const a = 17.625;
  const b = 243.04;
  const eT = Math.exp((a * tC) / (b + tC));
  const eTd = Math.exp((a * tdC) / (b + tdC));
  return Math.max(0, Math.min(100, (eTd / eT) * 100));
}
