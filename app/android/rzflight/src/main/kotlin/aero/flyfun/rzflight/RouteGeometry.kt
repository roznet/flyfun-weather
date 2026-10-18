package aero.flyfun.rzflight

import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Spherical route geometry utilities mirrored from `RZFlight.RouteGeometry`.
 */
object RouteGeometry {
    /** Mean spherical Earth radius in nautical miles. */
    const val EARTH_RADIUS_NM = 3440.065

    /**
     * Great-circle distance between [from] and [to] in nautical miles using the Haversine formula.
     */
    fun directDistanceNm(from: Coord, to: Coord): Double {
        val lat1 = Math.toRadians(from.latitude)
        val lat2 = Math.toRadians(to.latitude)
        val dLat = Math.toRadians(to.latitude - from.latitude)
        val dLon = Math.toRadians(to.longitude - from.longitude)

        val sinHalfLat = sin(dLat / 2.0)
        val sinHalfLon = sin(dLon / 2.0)
        val a = (sinHalfLat.pow(2) + cos(lat1) * cos(lat2) * sinHalfLon.pow(2)).coerceIn(0.0, 1.0)
        val c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))
        return EARTH_RADIUS_NM * c
    }

    /**
     * Projects [from] onto the segment `[toSegmentStart, segmentEnd]`.
     *
     * @return Pair of `(crossTrackDistanceNm, clampedSegmentRatio)` where `clampedSegmentRatio`
     *   is in `[0.0, 1.0]` (`0.0` at [toSegmentStart], `1.0` at [segmentEnd]).
     *   Handles zero-length segments gracefully by returning `(directDistanceNm(from, toSegmentStart), 0.0)`.
     */
    fun perpendicularDistanceAndRatio(
        from: Coord,
        toSegmentStart: Coord,
        segmentEnd: Coord,
    ): Pair<Double, Double> {
        val meanLatRad = Math.toRadians((toSegmentStart.latitude + segmentEnd.latitude) / 2.0)
        val cosLat = cos(meanLatRad)

        // Local tangent-plane coordinates in nautical miles (1 deg lat = 60 NM)
        val dx = (segmentEnd.longitude - toSegmentStart.longitude) * 60.0 * cosLat
        val dy = (segmentEnd.latitude - toSegmentStart.latitude) * 60.0
        val segLenSq = dx * dx + dy * dy

        if (segLenSq <= 1e-12) {
            return Pair(directDistanceNm(from, toSegmentStart), 0.0)
        }

        val px = (from.longitude - toSegmentStart.longitude) * 60.0 * cosLat
        val py = (from.latitude - toSegmentStart.latitude) * 60.0

        val rawRatio = (px * dx + py * dy) / segLenSq
        val clampedRatio = rawRatio.coerceIn(0.0, 1.0)

        val projectedCoord = Coord(
            latitude = toSegmentStart.latitude + clampedRatio * (segmentEnd.latitude - toSegmentStart.latitude),
            longitude = toSegmentStart.longitude + clampedRatio * (segmentEnd.longitude - toSegmentStart.longitude),
        )
        val crossTrackNm = directDistanceNm(from, projectedCoord)
        return Pair(crossTrackNm, clampedRatio)
    }

    /**
     * Initial great-circle bearing from [from] to [to] in degrees true `[0.0, 360.0)`.
     */
    fun initialBearingDeg(from: Coord, to: Coord): Double {
        val lat1 = Math.toRadians(from.latitude)
        val lat2 = Math.toRadians(to.latitude)
        val dLon = Math.toRadians(to.longitude - from.longitude)

        val y = sin(dLon) * cos(lat2)
        val x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
        if (abs(x) < 1e-15 && abs(y) < 1e-15) {
            return 0.0
        }
        val deg = Math.toDegrees(atan2(y, x))
        return (deg + 360.0) % 360.0
    }
}
