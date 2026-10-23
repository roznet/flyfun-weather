package aero.flyfun.weather.model.rules

import aero.flyfun.rzflight.Coord
import aero.flyfun.rzflight.RouteGeometry
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.LivePositionFix
import kotlin.math.abs

/**
 * Aircraft GPS fix projected onto a planned flight route.
 */
data class ProjectedRoutePosition(
    val lat: Double,
    val lon: Double,
    val altitudeFt: Double,
    val groundSpeedKt: Double,
    val trackDeg: Double,
    val distanceAlongRouteNm: Double,
    val crossTrackDistanceNm: Double,
    /** Normalized progress along the route in `[0.0, 1.0]`. */
    val routeFraction: Double,
    val nearestSegmentIndex: Int,
    /** True when `crossTrackDistanceNm <= maxCorridorNm`. */
    val isOnRoute: Boolean,
) {
    val distanceAlongRouteKm: Double
        get() = distanceAlongRouteNm * 1.852
}

/**
 * Pure geometry and throttling rules for in-flight GPS route projection and live position uploads.
 * Mirrors iOS `FlightTrackingService.swift` and `LivePositionService.swift`.
 */
object LivePositionRules {

    /** Default corridor half-width (nautical miles) within which an aircraft is considered on-route. */
    const val DEFAULT_MAX_CORRIDOR_NM: Double = 25.0

    /** Default minimum interval (seconds) between automatic position fix reports. */
    const val DEFAULT_MIN_INTERVAL_SEC: Long = 180L

    /** Default minimum distance (nautical miles) triggering an early position fix report. */
    const val DEFAULT_MIN_DISTANCE_NM: Double = 8.0

    /** Default minimum altitude change (feet) triggering an early position fix report. */
    const val DEFAULT_MIN_ALTITUDE_DELTA_FT: Double = 800.0

    /** Maximum number of fixes retained in the offline upload ring buffer. */
    const val MAX_BUFFERED_FIXES: Int = 200

    /**
     * Maximum fixes sent in a single `POST /api/flights/{id}/live/position` batch: the server
     * refuses more than 5 (`LivePositionRequest.fixes`, `max_length=5`) with a 422.
     */
    const val MAX_BATCH_SIZE: Int = 5

    /** Landing auto-stop (#874): ground speed under this, within [LANDING_MAX_DISTANCE_NM] of the destination. */
    const val LANDING_MAX_SPEED_KT: Double = 30.0

    /** Landing auto-stop distance from the destination. Mirrors iOS `FlightTrackingService.projectLocation`. */
    const val LANDING_MAX_DISTANCE_NM: Double = 5.0

    enum class FailureAction {
        DROP_BATCH,
        RETAIN_BATCH,
    }

    /**
     * Project an aircraft GPS position `(lat, lon)` onto a polyline of `(lat, lon)` route waypoints.
     *
     * Returns `null` if `waypoints` has fewer than 2 points.
     */
    fun projectOntoRoute(
        lat: Double,
        lon: Double,
        altitudeFt: Double,
        groundSpeedKt: Double,
        trackDeg: Double,
        waypoints: List<Pair<Double, Double>>,
        maxCorridorNm: Double = DEFAULT_MAX_CORRIDOR_NM,
    ): ProjectedRoutePosition? {
        if (waypoints.size < 2) return null

        val coords = waypoints.map { (wLat, wLon) -> Coord(latitude = wLat, longitude = wLon) }
        val point = Coord(latitude = lat, longitude = lon)

        val legLengthsNm = DoubleArray(coords.size - 1)
        var totalRouteNm = 0.0
        for (i in 0 until coords.size - 1) {
            val legNm = RouteGeometry.directDistanceNm(coords[i], coords[i + 1])
            legLengthsNm[i] = legNm
            totalRouteNm += legNm
        }

        var bestSegmentIndex = 0
        var bestCrossTrackNm = Double.POSITIVE_INFINITY
        var bestSegmentRatio = 0.0

        for (i in 0 until coords.size - 1) {
            val (perpNm, ratio) = RouteGeometry.perpendicularDistanceAndRatio(
                from = point,
                toSegmentStart = coords[i],
                segmentEnd = coords[i + 1],
            )
            if (perpNm < bestCrossTrackNm) {
                bestCrossTrackNm = perpNm
                bestSegmentIndex = i
                bestSegmentRatio = ratio.coerceIn(0.0, 1.0)
            }
        }

        var distanceBeforeSegmentNm = 0.0
        for (i in 0 until bestSegmentIndex) {
            distanceBeforeSegmentNm += legLengthsNm[i]
        }
        val rawAlongNm = distanceBeforeSegmentNm + bestSegmentRatio * legLengthsNm[bestSegmentIndex]
        val distanceAlongNm = if (totalRouteNm > 0.0) {
            rawAlongNm.coerceIn(0.0, totalRouteNm)
        } else {
            0.0
        }
        val routeFraction = if (totalRouteNm > 0.0) {
            (distanceAlongNm / totalRouteNm).coerceIn(0.0, 1.0)
        } else {
            0.0
        }

        return ProjectedRoutePosition(
            lat = lat,
            lon = lon,
            altitudeFt = altitudeFt,
            groundSpeedKt = groundSpeedKt,
            trackDeg = trackDeg,
            distanceAlongRouteNm = distanceAlongNm,
            crossTrackDistanceNm = bestCrossTrackNm,
            routeFraction = routeFraction,
            nearestSegmentIndex = bestSegmentIndex,
            isOnRoute = bestCrossTrackNm <= maxCorridorNm,
        )
    }

    /**
     * Decide whether `candidate` should be queued/sent given the previously sent `lastFix`.
     *
     * Returns `true` when:
     * - `lastFix == null` (first fix of the session), OR
     * - time elapsed between `lastFix.recordedAt` and `candidate.recordedAt` is `>= minIntervalSec`, OR
     * - great-circle distance moved is `>= minDistanceNm`, OR
     * - absolute altitude change is `>= minAltitudeDeltaFt`.
     */
    fun shouldSendPositionFix(
        lastFix: LivePositionFix?,
        candidate: LivePositionFix,
        minIntervalSec: Long = DEFAULT_MIN_INTERVAL_SEC,
        minDistanceNm: Double = DEFAULT_MIN_DISTANCE_NM,
        minAltitudeDeltaFt: Double = DEFAULT_MIN_ALTITUDE_DELTA_FT,
    ): Boolean {
        if (lastFix == null) return true

        val lastMs = IsoTime.parseEpochMs(lastFix.recordedAt)
        val candMs = IsoTime.parseEpochMs(candidate.recordedAt)
        if (lastMs != null && candMs != null) {
            val elapsedSec = abs(candMs - lastMs) / 1000L
            if (elapsedSec >= minIntervalSec) return true
        }

        val distanceNm = RouteGeometry.directDistanceNm(
            from = Coord(latitude = lastFix.lat, longitude = lastFix.lon),
            to = Coord(latitude = candidate.lat, longitude = candidate.lon),
        )
        if (distanceNm >= minDistanceNm) return true

        val lastAlt = lastFix.altitudeFt
        val candAlt = candidate.altitudeFt
        if (lastAlt != null && candAlt != null) {
            if (abs(candAlt - lastAlt) >= minAltitudeDeltaFt) return true
        }

        return false
    }

    /**
     * Whether a fix reads as the landing (#874): a valid ground speed under [LANDING_MAX_SPEED_KT]
     * within [LANDING_MAX_DISTANCE_NM] of the destination (the route's last waypoint). A fix with
     * no speed never does. Same rule as the iOS landing auto-stop.
     */
    fun isLanding(
        lat: Double,
        lon: Double,
        groundSpeedKt: Double?,
        waypoints: List<Pair<Double, Double>>,
    ): Boolean {
        val (destLat, destLon) = waypoints.lastOrNull() ?: return false
        if (groundSpeedKt == null || groundSpeedKt < 0.0 || groundSpeedKt >= LANDING_MAX_SPEED_KT) return false
        val distanceNm = RouteGeometry.directDistanceNm(
            from = Coord(latitude = lat, longitude = lon),
            to = Coord(latitude = destLat, longitude = destLon),
        )
        return distanceNm < LANDING_MAX_DISTANCE_NM
    }

    /**
     * Find the nearest named waypoint `(icao, distanceNm)` to `(lat, lon)`.
     */
    fun nearestNamedWaypoint(
        lat: Double,
        lon: Double,
        waypoints: List<Triple<String, Double, Double>>,
    ): Pair<String, Double>? {
        if (waypoints.isEmpty()) return null
        val here = Coord(latitude = lat, longitude = lon)
        var bestIcao = waypoints.first().first
        var bestDist = Double.POSITIVE_INFINITY
        for ((icao, wLat, wLon) in waypoints) {
            val dist = RouteGeometry.directDistanceNm(here, Coord(latitude = wLat, longitude = wLon))
            if (dist < bestDist) {
                bestDist = dist
                bestIcao = icao
            }
        }
        return bestIcao to bestDist
    }

    /**
     * Classify an HTTP failure status code for live position batch uploads.
     * Permanent client errors (`401`, `403`, `404`, `422`) drop the batch; transient/server errors retain it.
     */
    fun failureAction(statusCode: Int?): FailureAction = when (statusCode) {
        401, 403, 404, 422 -> FailureAction.DROP_BATCH
        else -> FailureAction.RETAIN_BATCH
    }

    /**
     * Enforce the ring-buffer cap on pending fixes, dropping oldest fixes when over `maxBufferedFixes`.
     */
    fun trimFixBuffer(
        fixes: List<LivePositionFix>,
        maxBufferedFixes: Int = MAX_BUFFERED_FIXES,
    ): List<LivePositionFix> {
        if (fixes.size <= maxBufferedFixes) return fixes
        return fixes.takeLast(maxBufferedFixes)
    }
}
