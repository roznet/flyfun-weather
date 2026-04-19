/**
 * Atmospheric thermodynamic computations for Skew-T background lines.
 * Ported from rzskewt Thermodynamics.swift.
 *
 * Constants and formulas follow standard meteorological conventions.
 */

// Physical constants
const Rd = 287.05;    // Gas constant for dry air (J/(kg·K))
const cp = 1004.0;    // Specific heat at constant pressure (J/(kg·K))
const Lv = 2.501e6;   // Latent heat of vaporization (J/kg)
const eps = 0.622;    // Ratio of molecular weight of water to dry air
const kappa = Rd / cp; // ~0.286

/** Saturation vapor pressure (hPa) via Magnus formula. */
function saturationVaporPressure(tempC: number): number {
  return 6.112 * Math.exp((17.67 * tempC) / (tempC + 243.5));
}

/** Saturation mixing ratio (kg/kg) at given T and p. */
function saturationMixingRatio(tempC: number, pressureHPa: number): number {
  const es = saturationVaporPressure(tempC);
  return eps * es / (pressureHPa - es);
}

/** Dewpoint (°C) from mixing ratio (kg/kg) and pressure (hPa). */
function dewpointFromMixingRatio(w: number, pressureHPa: number): number {
  const e = (w * pressureHPa) / (eps + w);
  return (243.5 * Math.log(e / 6.112)) / (17.67 - Math.log(e / 6.112));
}

/** Moist adiabatic lapse rate dT/dp (°C per hPa). */
function moistLapseRate(tempC: number, pressureHPa: number): number {
  const T = tempC + 273.15;
  const ws = saturationMixingRatio(tempC, pressureHPa);
  const numerator = (Rd * T + Lv * ws) / pressureHPa;
  const denominator = cp + (Lv * Lv * ws * eps) / (Rd * T * T);
  return numerator / denominator;
}

export interface AtmosphericPoint {
  tempC: number;
  pressureHPa: number;
}

/**
 * Generate isotherm lines (constant temperature).
 * Returns arrays of points for each isotherm.
 */
export function generateIsotherms(
  pBottom: number, pTop: number,
  tMin: number, tMax: number, step: number = 10,
): AtmosphericPoint[][] {
  const lines: AtmosphericPoint[][] = [];
  for (let t = Math.ceil(tMin / step) * step; t <= tMax; t += step) {
    lines.push([
      { tempC: t, pressureHPa: pBottom },
      { tempC: t, pressureHPa: pTop },
    ]);
  }
  return lines;
}

/**
 * Generate isobar levels (standard pressure levels).
 */
export function generateIsobars(pBottom: number, pTop: number): number[] {
  const standard = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100];
  return standard.filter(p => p <= pBottom && p >= pTop);
}

/**
 * Generate dry adiabat lines (constant potential temperature θ).
 * T = θ × (p/1000)^κ - 273.15
 */
export function generateDryAdiabats(
  pBottom: number, pTop: number, step: number = 20,
): AtmosphericPoint[][] {
  const lines: AtmosphericPoint[][] = [];
  // θ range: generate enough adiabats to cover the visible area
  for (let theta = 200; theta <= 500; theta += step) {
    const points: AtmosphericPoint[] = [];
    for (let p = pBottom; p >= pTop; p -= 25) {
      const tempC = theta * Math.pow(p / 1000, kappa) - 273.15;
      points.push({ tempC, pressureHPa: p });
    }
    lines.push(points);
  }
  return lines;
}

/**
 * Generate moist (saturated) adiabat lines.
 * Integrated using RK2 (midpoint method) from surface pressure upward.
 */
export function generateMoistAdiabats(
  pBottom: number, pTop: number, step: number = 5,
): AtmosphericPoint[][] {
  const lines: AtmosphericPoint[][] = [];
  const dpStep = 10; // hPa per integration step

  for (let startT = -30; startT <= 40; startT += step) {
    const points: AtmosphericPoint[] = [];
    let t = startT;
    for (let p = pBottom; p >= pTop; p -= dpStep) {
      points.push({ tempC: t, pressureHPa: p });
      // RK2 midpoint integration
      const k1 = moistLapseRate(t, p) * (-dpStep);
      const tMid = t + k1 / 2;
      const pMid = p - dpStep / 2;
      const k2 = moistLapseRate(tMid, pMid) * (-dpStep);
      t += k2;
    }
    lines.push(points);
  }
  return lines;
}

/**
 * Generate mixing ratio lines (constant humidity mixing ratio).
 * Each line is (dewpoint at that mixing ratio, pressure).
 */
export function generateMixingRatioLines(
  pBottom: number, pTop: number,
): { w_gkg: number; points: AtmosphericPoint[] }[] {
  const wValues = [0.4, 1, 2, 4, 7, 10, 16, 24]; // g/kg
  return wValues.map(w_gkg => {
    const w = w_gkg / 1000; // convert to kg/kg
    const points: AtmosphericPoint[] = [];
    for (let p = pBottom; p >= pTop; p -= 25) {
      const td = dewpointFromMixingRatio(w, p);
      if (isFinite(td)) {
        points.push({ tempC: td, pressureHPa: p });
      }
    }
    return { w_gkg, points };
  });
}
