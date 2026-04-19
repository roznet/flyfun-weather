/**
 * Standard atmosphere conversion utilities shared across Skew-T modules.
 *
 * Barometric formula (troposphere, T0=288.15K, L=0.0065K/m).
 */

/** Altitude (ft) → pressure (hPa). Returns null for negative altitudes. */
export function altitudeToPressure(altFt: number): number | null {
  if (altFt < 0) return null;
  const altM = altFt / 3.28084;
  return 1013.25 * Math.pow(1 - 0.0065 * altM / 288.15, 5.2561);
}

/** Pressure (hPa) → altitude (ft). */
export function pressureToAltitudeFt(pressureHPa: number): number {
  const altM = 288.15 / 0.0065 * (1 - Math.pow(pressureHPa / 1013.25, 1 / 5.2561));
  return altM * 3.28084;
}
