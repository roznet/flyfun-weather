package aero.flyfun.rzskewt

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Atmospheric thermodynamic and standard-atmosphere computations for Skew-T log-P diagrams.
 *
 * Formulas follow Bolton (1980) and the ICAO Standard Atmosphere (ISA).
 */
object Thermodynamics {

    // Physical constants
    private const val KELVIN_OFFSET = 273.15
    private const val ISA_SEA_LEVEL_PRESSURE_HPA = 1013.25
    private const val ISA_SEA_LEVEL_TEMP_K = 288.15
    private const val ISA_SEA_LEVEL_TEMP_C = 15.0
    private const val ISA_LAPSE_RATE_K_PER_M = 0.0065
    private const val ISA_TROPOPAUSE_FT = 36089.24
    private const val ISA_TROPOPAUSE_TEMP_C = -56.5
    private const val FEET_PER_METER = 3.28084
    private const val BAROMETRIC_EXPONENT = 5.2561

    private const val RD = 287.05       // Dry air gas constant J/(kg·K)
    private const val CP = 1004.0       // Specific heat of dry air at constant pressure J/(kg·K)
    private const val LV = 2.501e6      // Latent heat of vaporization J/kg
    private const val EPSILON = 0.622   // Ratio of molecular weight of water vapor to dry air
    private const val KAPPA = 0.2854    // Poisson constant R_d / c_p

    // ========================================================================
    // Standard Atmosphere Conversions
    // ========================================================================

    /**
     * Converts geometric altitude (ft) to standard atmosphere pressure (hPa)
     * using the ISA tropospheric barometric formula.
     */
    fun altitudeToPressureHpa(altitudeFt: Double): Double {
        val altM = altitudeFt / FEET_PER_METER
        val factor = (1.0 - ISA_LAPSE_RATE_K_PER_M * altM / ISA_SEA_LEVEL_TEMP_K).coerceAtLeast(0.01)
        return ISA_SEA_LEVEL_PRESSURE_HPA * factor.pow(BAROMETRIC_EXPONENT)
    }

    /**
     * Converts pressure (hPa) to standard atmosphere altitude (ft).
     */
    fun pressureToAltitudeFt(pressureHpa: Double): Double {
        val clampedP = pressureHpa.coerceAtLeast(1.0)
        val altM = (ISA_SEA_LEVEL_TEMP_K / ISA_LAPSE_RATE_K_PER_M) *
            (1.0 - (clampedP / ISA_SEA_LEVEL_PRESSURE_HPA).pow(1.0 / BAROMETRIC_EXPONENT))
        return altM * FEET_PER_METER
    }

    /**
     * Returns the nearest integer Flight Level (hundreds of feet in ISA) for a given pressure (hPa).
     */
    fun flightLevelFromPressure(pressureHpa: Double): Int {
        return (pressureToAltitudeFt(pressureHpa) / 100.0).roundToInt()
    }

    /**
     * Returns the ICAO Standard Atmosphere temperature (°C) at [altitudeFt].
     * Clamps at -56.5°C above the tropopause (~36,089 ft).
     */
    fun standardAtmosphereTemperatureC(altitudeFt: Double): Double {
        if (altitudeFt >= ISA_TROPOPAUSE_FT) return ISA_TROPOPAUSE_TEMP_C
        val altM = altitudeFt / FEET_PER_METER
        return ISA_SEA_LEVEL_TEMP_C - ISA_LAPSE_RATE_K_PER_M * altM
    }

    /**
     * Returns ISA temperature deviation (°C): actual temperature minus ISA standard temperature at [altitudeFt].
     */
    fun isaDeviationC(tempC: Double, altitudeFt: Double): Double {
        return tempC - standardAtmosphereTemperatureC(altitudeFt)
    }

    // ========================================================================
    // Temperature / Moisture / Parcel Physics
    // ========================================================================

    fun celsiusToKelvin(tempC: Double): Double = tempC + KELVIN_OFFSET

    fun kelvinToCelsius(tempK: Double): Double = tempK - KELVIN_OFFSET

    /**
     * Saturation vapor pressure (hPa) over liquid water via Bolton (1980):
     * `e_s(T) = 6.112 * exp(17.67 * T / (T + 243.5))`
     */
    fun saturationVaporPressureHpa(tempC: Double): Double {
        return 6.112 * exp((17.67 * tempC) / (tempC + 243.5))
    }

    /**
     * Relative humidity (%) in [0, 100] from temperature (°C) and dewpoint (°C).
     */
    fun relativeHumidityPct(tempC: Double, dewpointC: Double): Double {
        val es = saturationVaporPressureHpa(tempC)
        val e = saturationVaporPressureHpa(dewpointC)
        if (es <= 0.0) return 0.0
        return ((e / es) * 100.0).coerceIn(0.0, 100.0)
    }

    /**
     * Dewpoint temperature (°C) from temperature (°C) and relative humidity (%).
     */
    fun dewpointFromRhC(tempC: Double, rhPct: Double): Double {
        val clampedRh = rhPct.coerceIn(0.1, 100.0)
        val e = (clampedRh / 100.0) * saturationVaporPressureHpa(tempC)
        val lnRatio = ln(e / 6.112)
        return (243.5 * lnRatio) / (17.67 - lnRatio)
    }

    /**
     * Saturation mixing ratio (g/kg) at given temperature (°C) and pressure (hPa).
     */
    fun mixingRatioGPerKg(tempC: Double, pressureHpa: Double): Double {
        val es = saturationVaporPressureHpa(tempC).coerceAtMost(pressureHpa * 0.99)
        return 1000.0 * EPSILON * es / (pressureHpa - es)
    }

    /**
     * Temperature (°C) at which the saturation mixing ratio equals [wGPerKg] (g/kg) at [pressureHpa] (isohume).
     */
    fun temperatureForMixingRatioC(wGPerKg: Double, pressureHpa: Double): Double {
        val w = (wGPerKg.coerceAtLeast(1e-4)) / 1000.0 // kg/kg
        val e = (w * pressureHpa) / (EPSILON + w)
        val lnRatio = ln((e / 6.112).coerceAtLeast(1e-6))
        return (243.5 * lnRatio) / (17.67 - lnRatio)
    }

    /**
     * Potential temperature θ (K): `T_K * (1000 / p)^0.2854`.
     */
    fun potentialTemperatureK(tempC: Double, pressureHpa: Double): Double {
        val tk = celsiusToKelvin(tempC)
        return tk * (1000.0 / pressureHpa.coerceAtLeast(1.0)).pow(KAPPA)
    }

    /**
     * Temperature (°C) along a dry adiabat defined by surface potential temperature [thetaC] (°C at 1000 hPa)
     * at pressure [pressureHpa].
     */
    fun dryAdiabatTemperatureC(thetaC: Double, pressureHpa: Double): Double {
        val thetaK = celsiusToKelvin(thetaC)
        val tk = thetaK * (pressureHpa.coerceAtLeast(1.0) / 1000.0).pow(KAPPA)
        return kelvinToCelsius(tk)
    }

    /**
     * Equivalent potential temperature θ_e (K) using Bolton (1980) Eq. 43.
     */
    fun equivalentPotentialTemperatureK(tempC: Double, dewpointC: Double, pressureHpa: Double): Double {
        val tk = celsiusToKelvin(tempC)
        val tdk = celsiusToKelvin(dewpointC.coerceAtMost(tempC))
        val e = saturationVaporPressureHpa(dewpointC.coerceAtMost(tempC)).coerceAtMost(pressureHpa * 0.99)
        val r = EPSILON * e / (pressureHpa - e) // mixing ratio in kg/kg
        val tLclK = if (tdk >= tk) {
            tk
        } else {
            1.0 / (1.0 / (tdk - 56.0) + ln(tk / tdk) / 800.0) + 56.0
        }
        val thetaDl = tk * (1000.0 / (pressureHpa - e)).pow(KAPPA) * (tk / tLclK).pow(0.28 * r)
        return thetaDl * exp((3036.0 / tLclK - 1.78) * r * (1.0 + 0.448 * r))
    }

    /**
     * Lifting Condensation Level (LCL) temperature (°C) and pressure (hPa) for a parcel lifted from
     * [surfacePressureHpa] with [tempC] and [dewpointC] using Bolton (1980) Eq. 15.
     *
     * @return `Pair(lclTempC, lclPressureHpa)`
     */
    fun lclTemperatureAndPressure(
        tempC: Double,
        dewpointC: Double,
        surfacePressureHpa: Double,
    ): Pair<Double, Double> {
        if (dewpointC >= tempC) {
            return Pair(tempC, surfacePressureHpa)
        }
        val tk = celsiusToKelvin(tempC)
        val tdk = celsiusToKelvin(dewpointC)
        val tLclK = 1.0 / (1.0 / (tdk - 56.0) + ln(tk / tdk) / 800.0) + 56.0
        val lclTempC = kelvinToCelsius(tLclK)
        val lclPressureHpa = surfacePressureHpa * (tLclK / tk).pow(1.0 / KAPPA)
        return Pair(lclTempC, lclPressureHpa)
    }

    /**
     * Pseudo-adiabatic moist lapse rate `dT/dp` (°C per hPa) at [tempC] and [pressureHpa].
     * Positive value means temperature decreases as pressure decreases.
     */
    fun moistAdiabaticLapseRateCPerHpa(tempC: Double, pressureHpa: Double): Double {
        val tk = celsiusToKelvin(tempC)
        val p = pressureHpa.coerceAtLeast(10.0)
        val es = saturationVaporPressureHpa(tempC).coerceAtMost(p * 0.99)
        val ws = EPSILON * es / (p - es)
        val numerator = (RD * tk + LV * ws) / p
        val denominator = CP + (LV * LV * ws * EPSILON) / (RD * tk * tk)
        return numerator / denominator
    }

    /**
     * Integrates `dT/dp` upward from [startPressureHpa] to [endPressureHpa] using 2nd-order Runge-Kutta (midpoint),
     * returning `(tempC, pressureHpa)` points along the saturated adiabat.
     */
    fun moistAdiabatCurve(
        startTempC: Double,
        startPressureHpa: Double = 1000.0,
        endPressureHpa: Double = 200.0,
        stepHpa: Double = 25.0,
    ): List<Pair<Double, Double>> {
        val dp = abs(stepHpa).coerceAtLeast(1.0)
        val points = mutableListOf<Pair<Double, Double>>()
        var p = startPressureHpa
        var t = startTempC
        while (p >= endPressureHpa - 1e-6) {
            points.add(Pair(t, p))
            val nextP = (p - dp).coerceAtLeast(endPressureHpa)
            val actualDp = p - nextP
            if (actualDp <= 1e-6) break
            val k1 = moistAdiabaticLapseRateCPerHpa(t, p) * (-actualDp)
            val k2 = moistAdiabaticLapseRateCPerHpa(t + 0.5 * k1, p - 0.5 * actualDp) * (-actualDp)
            t += k2
            p = nextP
            if ( abs(p - endPressureHpa) < 1e-6) {
                points.add(Pair(t, p))
                break
            }
        }
        return points
    }

    /**
     * Generates `(tempC, pressureHpa)` points along a dry adiabat for [thetaC] (°C at 1000 hPa)
     * from [startPressureHpa] down to [endPressureHpa].
     */
    fun dryAdiabatCurve(
        thetaC: Double,
        startPressureHpa: Double = 1050.0,
        endPressureHpa: Double = 200.0,
        stepHpa: Double = 25.0,
    ): List<Pair<Double, Double>> {
        val dp = abs(stepHpa).coerceAtLeast(1.0)
        val points = mutableListOf<Pair<Double, Double>>()
        var p = startPressureHpa
        while (p >= endPressureHpa - 1e-6) {
            points.add(Pair(dryAdiabatTemperatureC(thetaC, p), p))
            if (abs(p - endPressureHpa) < 1e-6) break
            p = (p - dp).coerceAtLeast(endPressureHpa)
        }
        return points
    }

    /**
     * Generates `(tempC, pressureHpa)` points along a constant mixing-ratio line (isohume)
     * for [wGPerKg] (g/kg) from [startPressureHpa] down to [endPressureHpa].
     */
    fun isohumeCurve(
        wGPerKg: Double,
        startPressureHpa: Double = 1050.0,
        endPressureHpa: Double = 400.0,
        stepHpa: Double = 25.0,
    ): List<Pair<Double, Double>> {
        val dp = abs(stepHpa).coerceAtLeast(1.0)
        val points = mutableListOf<Pair<Double, Double>>()
        var p = startPressureHpa
        while (p >= endPressureHpa - 1e-6) {
            points.add(Pair(temperatureForMixingRatioC(wGPerKg, p), p))
            if (abs(p - endPressureHpa) < 1e-6) break
            p = (p - dp).coerceAtLeast(endPressureHpa)
        }
        return points
    }

    /**
     * Decomposes wind vector ([windSpeedKt], [windDirDeg] true) along aircraft [trackDeg] true.
     *
     * @return `Pair(headwindKt, crosswindKt)` where:
     *   - positive `headwindKt` = headwind, negative = tailwind
     *   - positive `crosswindKt` = wind from the right, negative = wind from the left
     */
    fun headwindCrosswindKt(
        windSpeedKt: Double,
        windDirDeg: Double,
        trackDeg: Double,
    ): Pair<Double, Double> {
        val relRad = Math.toRadians(windDirDeg - trackDeg)
        val headwind = windSpeedKt * cos(relRad)
        val crosswind = windSpeedKt * sin(relRad)
        return Pair(headwind, crosswind)
    }
}
