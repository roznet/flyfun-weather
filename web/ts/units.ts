/**
 * Region-aware display-unit formatting (module-level singleton, like i18n/theme).
 *
 * Canonical values cross the wire raw (visibility in meters, pressure in hPa,
 * temperature in Celsius); these helpers convert + format at the display edge so
 * we never derive a precise unit from an already-rounded one.
 *
 * v1 wires in visibility only. formatQNH / formatTemperature exist so QNH
 * (hPa vs inHg) and temperature (C vs F) plug in at their call sites without
 * new plumbing once those fields are surfaced.
 */

export type UnitsRegion = 'europe' | 'us';

const STORAGE_KEY = 'wb_units_region';
const M_PER_SM = 1609.34;
const HPA_PER_INHG = 33.8639;

function readStored(): UnitsRegion {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'us' ? 'us' : 'europe';
  } catch {
    return 'europe';
  }
}

let unitsRegion: UnitsRegion = readStored();

export function getUnitsRegion(): UnitsRegion {
  return unitsRegion;
}

export function setUnitsRegion(region: string | null | undefined): void {
  unitsRegion = region === 'us' ? 'us' : 'europe';
  try {
    localStorage.setItem(STORAGE_KEY, unitsRegion);
  } catch {
    /* localStorage unavailable — keep in-memory value */
  }
}

/** Format a visibility in meters for the active (or given) region. */
export function formatVisibility(
  meters: number | null | undefined,
  region: UnitsRegion = unitsRegion,
): string {
  if (meters == null) return '';
  if (region === 'us') {
    const sm = meters / M_PER_SM;
    if (sm >= 10) return '>10 SM';
    return `${sm.toFixed(1)} SM`;
  }
  if (meters >= 10000) return '>10 km';
  if (meters >= 5000) return `${(meters / 1000).toFixed(0)} km`;
  return `${Math.round(meters)} m`;
}

/** Format an altimeter setting / QNH in hPa. (Not yet wired into surfaces.) */
export function formatQNH(
  hpa: number | null | undefined,
  region: UnitsRegion = unitsRegion,
): string {
  if (hpa == null) return '';
  if (region === 'us') return `${(hpa / HPA_PER_INHG).toFixed(2)} inHg`;
  return `${Math.round(hpa)} hPa`;
}

/** Format a temperature in Celsius. (Not yet wired into surfaces.) */
export function formatTemperature(
  celsius: number | null | undefined,
  region: UnitsRegion = unitsRegion,
  decimals = 0,
): string {
  if (celsius == null) return '';
  if (region === 'us') return `${(celsius * 9 / 5 + 32).toFixed(decimals)}°F`;
  return `${celsius.toFixed(decimals)}°C`;
}
