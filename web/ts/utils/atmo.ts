/**
 * ISA standard-atmosphere conversion utilities.
 *
 * Barometric formula (troposphere, T0=288.15K, L=0.0065K/m). General-purpose —
 * consumed by the Skew-T modules and by the flights duration estimate.
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

/** Indicated airspeed (kt) → true airspeed (kt) under the ISA standard atmosphere at altFt.
 *  TAS = IAS / √(ρ/ρ₀), where ρ/ρ₀ = (1 − L·altM/T0)^4.2561 in the ISA troposphere. */
export function iasToTasISA(iasKt: number, altFt: number): number {
  const altM = Math.max(0, altFt) / 3.28084;
  const densityRatio = Math.pow(1 - 0.0065 * altM / 288.15, 4.2561);
  return iasKt / Math.sqrt(densityRatio);
}
