package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.ObservedConditions
import java.util.Locale
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.roundToInt

/** A lat/lon rectangle, the box the route map asks the observed endpoints for. */
data class LatLonBox(
    val south: Double,
    val west: Double,
    val north: Double,
    val east: Double,
)

/**
 * One drawn observed layer's age in the map's summary line ("Cells 12 min"), orange when the
 * layer is stale or failed, so an empty map never reads as "nothing there".
 */
data class ObservedSummaryChip(
    val name: String,
    val status: String,
    val isWarning: Boolean,
) {
    val text: String get() = "$name $status"
}

/**
 * Shared pieces of the route map's observed layers (cells and lightning, #907): the corridor
 * box, the chip age and the badge's "HH:MMZ · N min old".
 *
 * SYNC: iOS `ObservedMapImagery.corridorBox` / `corridorRadius` / `SummaryChip.ageText` and
 * `ObservedBadge.ageText`.
 */
object ObservedMapRules {

    /** Route bounding box padded by [radiusNm] (web `corridorBox`); null without points. */
    fun corridorBox(points: List<Pair<Double, Double>>, radiusNm: Double): LatLonBox? {
        if (points.isEmpty()) return null
        val minLat = points.minOf { it.first }
        val maxLat = points.maxOf { it.first }
        val minLon = points.minOf { it.second }
        val maxLon = points.maxOf { it.second }
        val padLat = (radiusNm * 1.852) / 111.0
        // Longitude degrees shrink with latitude; pad with the widest latitude on the route
        // so the box never clips the corridor at its poleward end.
        val cosLat = max(0.2, cos(max(abs(minLat), abs(maxLat)) * Math.PI / 180.0))
        val padLon = padLat / cosLat
        return LatLonBox(
            south = minLat - padLat,
            west = minLon - padLon,
            north = maxLat + padLat,
            east = maxLon + padLon,
        )
    }

    /**
     * The corridor width the sampled numbers describe: [picked] when it is a radius the server
     * sampled, else the widest. Null when the briefing sampled none.
     */
    fun corridorRadius(observed: ObservedConditions?, picked: Double? = null): Double? {
        val radii = observed?.radiiNm.orEmpty()
        if (radii.isEmpty()) return null
        if (picked != null && picked in radii) return picked
        return radii.max()
    }

    /** Only a briefing that collected observed conditions offers the observed layers (iOS `showsObserved`). */
    fun showsObserved(observed: ObservedConditions?): Boolean = observed?.hasAnyField == true

    /** "now" under a minute, else "12 min". */
    fun chipAge(minutes: Double): String = if (minutes < 1) "now" else "${minutes.roundToInt()} min"

    /** "Lightning 14:05Z · 6 min old" (or "just now"); "--:--Z" for an unreadable time. */
    fun ageText(validTime: String?, ageMinutes: Double, label: String = ""): String {
        val hhmm = AirportCardRules.hhmmZ(validTime).ifEmpty { "--:--Z" }
        val age = if (ageMinutes < 1) "just now" else "${ageMinutes.roundToInt()} min old"
        val stamp = "$hhmm · $age"
        return if (label.isEmpty()) stamp else "$label $stamp"
    }

    /** "%.Nf" in a fixed locale: a query string must never carry a decimal comma. */
    fun fixed(value: Double, decimals: Int): String = String.format(Locale.US, "%.${decimals}f", value)
}
