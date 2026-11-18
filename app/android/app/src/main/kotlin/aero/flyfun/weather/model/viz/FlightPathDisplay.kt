package aero.flyfun.weather.model.viz

import aero.flyfun.weather.model.RouteAnalysesResponse
import kotlin.math.abs

/** One point of the climb / cruise / descent line on the cross-section. */
data class VizFlightPathPoint(val distanceNm: Double, val altitudeFt: Double)

/**
 * When the cross-section follows the flight path (#1005), matching the web's `data-extract.ts`
 * and `reference-lines.ts`.
 */
object FlightPathDisplay {
    private const val LIVE_MODE = "live"
    private const val SAME_ALTITUDE_FT = 1.0
    private const val MIN_POINTS = 2

    /**
     * The path to draw, or null for the flat cruise line. It is drawn only where the cards graded
     * on it (a live pack) and only at the altitude it was built for: at another cruise it would
     * mislead.
     */
    fun pathToDraw(analyses: RouteAnalysesResponse, effectiveCruiseFt: Double): List<VizFlightPathPoint>? {
        val path = analyses.flightPath ?: return null
        if (analyses.flightPathMode != LIVE_MODE) return null
        if (abs(path.cruiseAltitudeFt - effectiveCruiseFt) >= SAME_ALTITUDE_FT) return null
        val points = path.profile
            .filter { it.size >= MIN_POINTS }
            .map { VizFlightPathPoint(distanceNm = it[0], altitudeFt = it[1]) }
        return points.takeIf { it.size >= MIN_POINTS }
    }

    /** Where the label sits: the top of climb, or the start when the path never reaches cruise. */
    fun labelPoint(path: List<VizFlightPathPoint>, cruiseFt: Double): VizFlightPathPoint =
        path.firstOrNull { it.altitudeFt >= cruiseFt - SAME_ALTITUDE_FT } ?: path.first()
}
