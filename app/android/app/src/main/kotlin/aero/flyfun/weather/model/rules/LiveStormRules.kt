package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.LiveStorm
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Words and colours for the server's `/live` storms (#688, #886): the Observed tab's
 * storm cards and the route map's storm dots. Nothing is measured or classified here:
 * trend, intensity, side, place, relative motion, closing speed and the abeam time are
 * the server's. Mirrors iOS `LiveStormListRules`, `RouteRibbonRules.stormPositionText`
 * / `stormMotionText` and the `CellsOverlay.trendHex` table.
 *
 * SYNC: hand-copied from those iOS functions; a wording, threshold or colour change
 * there lands here too (pinned by `LiveStormRulesTest`, the iOS `LiveStormListTests` fixture).
 */
object LiveStormRules {

    /** The map's trend colours, same table as iOS `CellsOverlay.trendHex` and the web. */
    val TREND_HEX: Map<String, Long> = mapOf(
        "developing" to 0xFFD7263DL,
        "decaying" to 0xFF1B6CA8L,
        "steady" to 0xFF7A7A7AL,
        "mixed" to 0xFFF18F01L,
        "new" to 0xFFFFFFFFL,
    )

    /** Colour for a trend the table does not know (iOS `unknownTrendHex`). */
    const val UNKNOWN_TREND_HEX: Long = 0xFFCCCCCCL

    /** ARGB colour for the server's trend word; [UNKNOWN_TREND_HEX] for a word or null it has no colour for. */
    fun trendColorArgb(trend: String?): Long =
        trend?.trim()?.lowercase(Locale.US)?.let { TREND_HEX[it] } ?: UNKNOWN_TREND_HEX

    /** "Developing": the trend pill's text, or null when the server sent no trend (no pill). */
    fun trendLabel(storm: LiveStorm): String? =
        storm.trend?.trim()?.takeIf { it.isNotEmpty() }?.replaceFirstChar { it.uppercase() }

    /** "Heavy cell" / "Cell": the server's VIP intensity, as the iOS list words it. */
    fun title(storm: LiveStorm): String =
        storm.intensity?.takeIf { it.isNotBlank() }?.let { "${it.replaceFirstChar { c -> c.uppercase() }} cell" } ?: "Cell"

    /**
     * Where the storm is: "6 NM right of track, near ZZAA", "8 NM NE of ZZAR" past the
     * route's ends, or "6 NM right of track at 40 NM" without a place (iOS `stormPositionText`).
     */
    fun positionText(storm: LiveStorm): String {
        val offNm = storm.offtrackNm ?: storm.distanceNm
        val off = "${offNm.roundToInt()} NM"
        val place = storm.place
        val words = place?.text?.takeIf { it.isNotEmpty() }
        if (place != null && words != null) {
            if (place.relation == "off_departure" || place.relation == "past_destination") return words
            val side = storm.side
            if (side == null || offNm < 1.0) return "on track, $words"
            return "$off $side of track, $words"
        }
        if (storm.end != null) {
            val anchor = storm.endIcao ?: storm.end
            return listOfNotNull(off, storm.endBearing, "of", anchor).filter { it.isNotEmpty() }.joinToString(" ")
        }
        val along = (storm.alongNm ?: storm.enrouteDistanceNm).roundToInt()
        val side = storm.side ?: return "on track at $along NM"
        return "$off $side of track at $along NM"
    }

    /** The server's motion against the track, in its words (iOS `stormMotionText`). */
    fun motionText(storm: LiveStorm): String = when (storm.relativeMotion) {
        "closing" -> storm.closingKt?.let { "closing ${it.roundToInt()} kt" } ?: "closing"
        "moving_away" -> storm.closingKt?.let { "moving away ${(-it).roundToInt()} kt" } ?: "moving away"
        "parallel" -> "moving along the track"
        "stationary" -> "nearly stationary"
        else -> "motion not yet measured"
    }

    /**
     * "abeam in ~25 min (~08:52Z)": the server's minutes to abeam (to the nearest 5 like
     * its `when_text`) and clock time. Null past the route's ends, behind, or without a time.
     */
    fun abeamText(storm: LiveStorm): String? {
        if (storm.end != null || storm.ahead == false) return null
        val clock = AirportCardRules.hhmmZ(storm.abeamEta)
        val minutes = storm.minutesToAbeam ?: return if (clock.isEmpty()) null else "abeam ~$clock"
        val inText = when {
            minutes <= 2.0 -> "abeam now"
            minutes < 10.0 -> "abeam in ~${minutes.roundToInt()} min"
            else -> "abeam in ~${5 * (minutes / 5.0).roundToInt()} min"
        }
        return if (clock.isEmpty()) inText else "$inText (~$clock)"
    }

    /** "lightning pending" / "partly outside radar coverage": the server's caveats on this storm. */
    fun caveats(storm: LiveStorm): List<String> = buildList {
        if (storm.flashes == null && storm.flashesPending) add("lightning pending")
        if (storm.truncated) add("partly outside radar coverage")
    }
}
