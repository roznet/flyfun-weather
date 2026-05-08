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
/** Surface DD below which the secondary trigger emits LIFR rather than
 *  IFR. Below 0.5 °C the air is essentially saturated; pair that with
 *  ≥ 80 % low cloud cover and the model is forecasting fog, not just
 *  near-saturated stratus. */
const SECONDARY_LIFR_DD_THRESHOLD_C = 0.5;
const TOP_DD_DRIES_OUT_C = 4;
const FLOOR_THICKNESS_FT = 500;
const CAP_THICKNESS_FT = 1500;

/**
 * Compute surface obscuration band from the surface forecast.
 * Returns `null` when no trigger fires.
 *
 * - Primary trigger: `visibility_m < 5000` → severity by 1/3/5 km tiers.
 * - Secondary trigger: low cloud ≥ 80% AND surface DD < 2°C, only when
 *   visibility is unavailable (ECMWF / Météo-France / GEM). Severity
 *   grades by surface DD: < 0.5°C → LIFR, else IFR.
 *
 * Band geometry:
 *   bottom = terrainFt
 *   top    = min(first level with DD > 4°C, terrainFt + 1500),
 *            floored at terrainFt + 500.
 *   Lowest-level altitude is used as a conservative top fallback only
 *   when no level reports DD (otherwise saturated levels would clamp
 *   the band into the fog itself).
 *
 * @param pressureLevels Must be **sorted ascending by `altitudeFt`** —
 *   `computeTop` finds the first matching level via `Array.find`, so an
 *   unsorted input would silently pick the wrong level.
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
    // Graduate severity by surface DD so dense fog on visibility-less
    // models (e.g. ECMWF TCC=100% / DD=0.1°C) gets the LIFR purple it
    // deserves rather than IFR red.
    severity = (temperature2mC - dewpoint2mC) < SECONDARY_LIFR_DD_THRESHOLD_C
      ? 'lifr'
      : 'ifr';
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

  const aboveTerrain = levels.filter((l) => l.altitudeFt > terrainFt);

  // First level above terrain where DD exceeds the dry-out threshold —
  // this is the natural fog top when DD info is available.
  const dryLevel = aboveTerrain.find(
    (l) => l.ddC !== null && l.ddC > TOP_DD_DRIES_OUT_C,
  );
  if (dryLevel) {
    top = Math.min(top, dryLevel.altitudeFt);
  } else if (aboveTerrain.length > 0 && aboveTerrain.every((l) => l.ddC === null)) {
    // Conservative fallback when no level reports DD: clamp to the
    // lowest level above terrain so we don't extrapolate fog into
    // territory we have no data for. Skipped when DD info IS available
    // (i.e. dryLevel just didn't fire) — in that case the levels are
    // saturated and ARE inside the fog, not above it; the cap should
    // win. This matters most for sea-level airports where 1000 hPa
    // sits ~330 ft AGL: clamping there on saturated levels would
    // produce a 500-ft-thick band even when fog extends much higher.
    top = Math.min(top, aboveTerrain[0].altitudeFt);
  }

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
