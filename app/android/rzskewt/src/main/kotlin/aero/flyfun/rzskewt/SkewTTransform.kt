package aero.flyfun.rzskewt

import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
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
 *
 * The visible window (`tMin..tMax`) of a narrow plot comes from [fitSkewT], which keeps the
 * isotherm angle and skew shift fixed instead of clamping the skew factor (#962).
 */
data class SkewTConfig(
    val pBottom: Double = 1050.0,
    val pTop: Double = 250.0,
    val tMin: Double = -60.0,
    val tMax: Double = 40.0,
    val skewAngleDeg: Double = 45.0,
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
     * Aspect-adjusted skew factor so isotherms tilt at [SkewTConfig.skewAngleDeg] on screen.
     * Use [fitSkewT] to pick a box + window that keeps the diagram's proportions.
     */
    val skewFactor: Double = run {
        val aspect = if (width > 0f) height.toDouble() / width.toDouble() else 1.0
        tan(Math.toRadians(config.skewAngleDeg)) * aspect
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

// ── Fitted layout (#962) ─────────────────────────────────────────────────────
//
// Same rule as the web (`web/ts/visualization/skewt/skewt-transform.ts`) and iOS (RZSkewT):
// isotherms at 45° and a fixed skew shift of [SKEW_SHIFT_DEG] across pBottom→pTop. The visible
// window then follows from the box, window = shift · w / h: a wide box shows the full config
// range (box narrowed, centred), a narrow one a window centred on the sounding, with the height
// shrunk when the sounding is wider than the window. See designs/skewt-canvas.md.

/** Skew shift (°C) across pBottom→pTop: the web's classic −60…+40 at plot aspect 1.8. */
const val SKEW_SHIFT_DEG: Double = 100.0 / 1.8

/** A fitted plot box (px, ≤ the available size) and its temperature window (°C at pBottom). */
data class SkewTFit(val width: Float, val height: Float, val tMin: Double, val tMax: Double)

private fun logFraction(pHpa: Double, config: SkewTConfig): Double {
    val lb = ln(config.pBottom)
    return (lb - ln(pHpa)) / (lb - ln(config.pTop))
}

/**
 * Horizontal extent of `(T °C, p hPa)` points in skewed coordinates — the temperature each
 * lands on when read at the bottom axis (X = T + shift · logFrac). Points with a null T or
 * outside `[pTop, pBottom]` are ignored; null when none are left.
 */
fun skewedExtent(
    points: Iterable<Pair<Double?, Double>>,
    config: SkewTConfig = SkewTConfig(),
    shiftDeg: Double = SKEW_SHIFT_DEG,
): ClosedFloatingPointRange<Double>? {
    var lo = Double.POSITIVE_INFINITY
    var hi = Double.NEGATIVE_INFINITY
    for ((t, p) in points) {
        if (t == null || !t.isFinite() || p < config.pTop || p > config.pBottom) continue
        val x = t + shiftDeg * logFraction(p, config)
        lo = min(lo, x)
        hi = max(hi, x)
    }
    return if (lo <= hi) lo..hi else null
}

/**
 * Fit the plot box and temperature window to the available space and the sounding's skewed
 * [extent], keeping 45° isotherms and a fixed skew shift. `config.tMin..tMax` is the widest
 * window ever shown.
 */
fun fitSkewT(
    availW: Float,
    availH: Float,
    extent: ClosedFloatingPointRange<Double>?,
    config: SkewTConfig = SkewTConfig(),
    shiftDeg: Double = SKEW_SHIFT_DEG,
    paddingDeg: Double = 5.0,
    minHeight: Float = 220f,
): SkewTFit {
    val full = config.tMax - config.tMin
    if (availW <= 0f || availH <= 0f) {
        return SkewTFit(max(0f, availW), max(0f, availH), config.tMin, config.tMax)
    }
    val minH = min(minHeight, availH).toDouble()
    val w = availW.toDouble()

    var h = availH.toDouble()
    val need = extent?.let { min(full, it.endInclusive - it.start + 2 * paddingDeg) } ?: 0.0
    if (shiftDeg * w / h < need) {
        h = max(minH, shiftDeg * w / need)
    }

    val window = shiftDeg * w / h
    if (window >= full - 1e-9) {
        return SkewTFit((full * h / shiftDeg).toFloat(), h.toFloat(), config.tMin, config.tMax)
    }
    val centre = extent?.let { (it.start + it.endInclusive) / 2 } ?: ((config.tMin + config.tMax) / 2)
    val tMin = min(max(centre - window / 2, config.tMin), config.tMax - window)
    return SkewTFit(availW, h.toFloat(), tMin, tMin + window)
}
