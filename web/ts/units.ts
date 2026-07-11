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

/** Concrete region used for formatting. */
export type UnitsRegion = 'europe' | 'us';
/** Stored user preference: a forced region, or 'auto' (resolve per flight). */
export type UnitsPreference = 'auto' | 'europe' | 'us';

const STORAGE_KEY = 'wb_units_region';
const M_PER_SM = 1609.34;
const HPA_PER_INHG = 33.8639;

function normalizePref(pref: string | null | undefined): UnitsPreference {
  return pref === 'us' || pref === 'europe' || pref === 'auto' ? pref : 'auto';
}

function resolve(pref: UnitsPreference, hint: UnitsRegion | null): UnitsRegion {
  if (pref === 'us' || pref === 'europe') return pref;
  return hint ?? 'europe'; // auto
}

function readStored(): UnitsPreference {
  try {
    return normalizePref(localStorage.getItem(STORAGE_KEY));
  } catch {
    return 'auto';
  }
}

let unitsPreference: UnitsPreference = readStored();
let activeRegion: UnitsRegion = resolve(unitsPreference, null);

export function getUnitsRegion(): UnitsRegion {
  return activeRegion;
}

export function getUnitsPreference(): UnitsPreference {
  return unitsPreference;
}

/** Set the stored preference (auto/europe/us) and persist it. Resets the active
 *  region to the preference's default; a later setFlightRegion() supplies the
 *  per-flight hint that 'auto' needs. */
export function setUnitsPreference(pref: string | null | undefined): void {
  unitsPreference = normalizePref(pref);
  activeRegion = resolve(unitsPreference, null);
  try {
    localStorage.setItem(STORAGE_KEY, unitsPreference);
  } catch {
    /* localStorage unavailable — keep in-memory value */
  }
}

/** Apply a detected flight region. Only affects the active region when the
 *  preference is 'auto'; a forced europe/us preference ignores the hint. */
export function setFlightRegion(hint: UnitsRegion | null): void {
  activeRegion = resolve(unitsPreference, hint);
}

/** Classify a set of waypoint ICAOs the same way the backend does
 *  (K/C/P prefix → US). Returns null for a mixed/unknown route. */
export function regionFromIcaos(icaos: Array<string | null | undefined>): UnitsRegion | null {
  const regions = new Set<UnitsRegion>();
  for (const icao of icaos) {
    const c = (icao ?? '').trim().toUpperCase()[0];
    if (!c) continue;
    regions.add(c === 'K' || c === 'C' || c === 'P' ? 'us' : 'europe');
  }
  return regions.size === 1 ? [...regions][0] : null;
}

/** Format a visibility in meters for the active (or given) region. */
export function formatVisibility(
  meters: number | null | undefined,
  region: UnitsRegion = activeRegion,
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

/** Format an altimeter setting / QNH in hPa. */
export function formatQNH(
  hpa: number | null | undefined,
  region: UnitsRegion = activeRegion,
): string {
  if (hpa == null) return '';
  if (region === 'us') return `${(hpa / HPA_PER_INHG).toFixed(2)} inHg`;
  return `${Math.round(hpa)} hPa`;
}

/** QNH display-unit label for the active (or given) region. */
export function qnhUnitLabel(region: UnitsRegion = activeRegion): string {
  return region === 'us' ? 'inHg' : 'hPa';
}

/** Convert a QNH in canonical hPa to the active (or given) region's display
 *  numeric value (hPa unchanged for Europe, inHg for the US). Use this when a
 *  numeric value is needed (e.g. axis scaling); use formatQNH for a string. */
export function qnhDisplayValue(
  hpa: number,
  region: UnitsRegion = activeRegion,
): number {
  return region === 'us' ? hpa / HPA_PER_INHG : hpa;
}

/** Zero-pad a compass heading/bearing to 3 digits. Rounds to nearest `step`° first
 *  (step=10 for wind chips/METAR/TAF, step=1 elsewhere). Does NOT append the ° symbol;
 *  call sites append °/°T/@ themselves. */
export function formatHeading(deg: number, step = 1): string {
  const rounded = Math.round(deg / step) * step;
  return String(rounded).padStart(3, '0');
}

/** Format a temperature in Celsius. (Not yet wired into surfaces.) */
export function formatTemperature(
  celsius: number | null | undefined,
  region: UnitsRegion = activeRegion,
  decimals = 0,
): string {
  if (celsius == null) return '';
  if (region === 'us') return `${(celsius * 9 / 5 + 32).toFixed(decimals)}°F`;
  return `${celsius.toFixed(decimals)}°C`;
}
