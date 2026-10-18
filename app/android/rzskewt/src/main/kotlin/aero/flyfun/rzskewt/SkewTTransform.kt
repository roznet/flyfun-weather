package aero.flyfun.rzskewt

import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.min
import kotlin.math.tan

/**
 * Configuration parameters for a Skew-T log-P diagram coordinate space.
 *
 * @property pBottom Bottom (surface) pressure in hPa (default 1050).
 * @property pTop Top pressure in hPa (default 250).
 * @property tMin Minimum surface temperature in °C (default -60).
 * @property tMax Maximum surface temperature in °C (default +40).
 * @property skewAngleDeg Isotherm tilt angle in degrees (default 45).
 * @property maxSkewFactor Upper bound on the aspect-adjusted skew factor (default 1.0)
 *   so narrow portrait phone screens do not over-skew upper-air temperatures off-chart.
 */
data class SkewTConfig(
    val pBottom: Double = 1050.0,
    val pTop: Double = 250.0,
    val tMin: Double = -60.0,
    val tMax: Double = 40.0,
    val skewAngleDeg: Double = 45.0,
    val maxSkewFactor: Double = 1.0,
)

/**
 * Maps between atmospheric `(temperature °C, pressure hPa)` coordinates and 2D canvas pixel coordinates
 * using a logarithmic pressure vertical axis and a skewed temperature horizontal axis.
 *
 * Ported from `SkewTTransform.swift` and `web/ts/visualization/skewt/skewt-transform.ts`.
 */
class SkewTTransform(
    val left: Float,
    val top: Float,
    val width: Float,
    val height: Float,
    val config: SkewTConfig = SkewTConfig(),
) {
    val right: Float
        get() = left + width

    val bottom: Float
        get() = top + height

    private val lnBottom: Double = ln(config.pBottom)
    private val lnTop: Double = ln(config.pTop)
    private val lnRange: Double = lnBottom - lnTop
    private val tRange: Double = config.tMax - config.tMin

    /**
     * Aspect-adjusted skew factor, clamped by [SkewTConfig.maxSkewFactor] so tall/narrow mobile plots
     * keep upper-air soundings legible.
     */
    val skewFactor: Double = run {
        val aspect = if (width > 0f) height.toDouble() / width.toDouble() else 1.0
        val raw = tan(Math.toRadians(config.skewAngleDeg)) * aspect
        min(config.maxSkewFactor, raw)
    }

    /**
     * Maps pressure (hPa) to vertical Y pixel coordinate.
     * Higher pressure is at [bottom], lower pressure is at [top].
     */
    fun pressureToY(pHpa: Double): Float {
        val logFrac = (lnBottom - ln(pHpa.coerceAtLeast(1.0))) / lnRange
        return (bottom - logFrac * height).toFloat()
    }

    /**
     * Inverse of [pressureToY]: maps vertical Y pixel coordinate to pressure (hPa).
     */
    fun yToPressure(y: Float): Double {
        val logFrac = (bottom - y).toDouble() / height.toDouble()
        return exp(lnBottom - logFrac * lnRange)
    }

    /**
     * Maps `(temperature °C, pressure hPa)` to horizontal X pixel coordinate, including the isotherm skew offset.
     */
    fun tempPressureToX(tempC: Double, pHpa: Double): Float {
        val logFrac = (lnBottom - ln(pHpa.coerceAtLeast(1.0))) / lnRange
        val skewOffset = logFrac * skewFactor
        val normalizedT = (tempC - config.tMin) / tRange
        return (left + (normalizedT + skewOffset) * width).toFloat()
    }

    /**
     * Inverse of [tempPressureToX]: maps horizontal X pixel coordinate and pressure (hPa) to temperature (°C).
     */
    fun xPressureToTemp(x: Float, pHpa: Double): Double {
        val logFrac = (lnBottom - ln(pHpa.coerceAtLeast(1.0))) / lnRange
        val skewOffset = logFrac * skewFactor
        val normalizedT = (x - left).toDouble() / width.toDouble() - skewOffset
        return normalizedT * tRange + config.tMin
    }

    /**
     * Maps standard-atmosphere altitude (ft) to vertical Y pixel coordinate.
     */
    fun altitudeFtToY(altitudeFt: Double): Float {
        return pressureToY(Thermodynamics.altitudeToPressureHpa(altitudeFt))
    }

    /**
     * Inverse of [altitudeFtToY]: maps vertical Y pixel coordinate to standard-atmosphere altitude (ft).
     */
    fun yToAltitudeFt(y: Float): Double {
        return Thermodynamics.pressureToAltitudeFt(yToPressure(y))
    }

    /**
     * Returns true if [pHpa] lies within `[config.pTop, config.pBottom]`.
     */
    fun isPressureVisible(pHpa: Double): Boolean {
        return pHpa in config.pTop..config.pBottom
    }
}
