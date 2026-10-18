package aero.flyfun.weather.model.viz

import kotlin.math.max
import kotlin.math.min

class CoordTransform(
    val width: Float,
    val height: Float,
    val totalDistanceNm: Double,
    val maxAltitudeFt: Double,
    val marginLeft: Float = 52f,
    val marginRight: Float = 44f,
    val marginTop: Float = 18f,
    val marginBottom: Float = 36f,
) {
    val plotLeft: Float = marginLeft
    val plotRight: Float = max(marginLeft + 1f, width - marginRight)
    val plotTop: Float = marginTop
    val plotBottom: Float = max(marginTop + 1f, height - marginBottom)
    val plotWidth: Float = plotRight - plotLeft
    val plotHeight: Float = plotBottom - plotTop

    fun distanceToX(distanceNm: Double): Float {
        if (totalDistanceNm <= 0.0) return plotLeft
        val ratio = (distanceNm / totalDistanceNm).coerceIn(0.0, 1.0)
        return plotLeft + (ratio * plotWidth).toFloat()
    }

    fun xToDistance(x: Float): Double {
        if (plotWidth <= 0f || totalDistanceNm <= 0.0) return 0.0
        val ratio = ((x - plotLeft) / plotWidth).toDouble().coerceIn(0.0, 1.0)
        return ratio * totalDistanceNm
    }

    fun altitudeToY(altitudeFt: Double): Float {
        if (maxAltitudeFt <= 0.0) return plotBottom
        val ratio = (altitudeFt / maxAltitudeFt).coerceIn(0.0, 1.0)
        return plotBottom - (ratio * plotHeight).toFloat()
    }

    fun yToAltitude(y: Float): Double {
        if (plotHeight <= 0f || maxAltitudeFt <= 0.0) return 0.0
        val ratio = ((plotBottom - y) / plotHeight).toDouble().coerceIn(0.0, 1.0)
        return min(maxAltitudeFt, max(0.0, ratio * maxAltitudeFt))
    }
}
