package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.ObservedFlash
import aero.flyfun.weather.model.ObservedFlashesResponse
import kotlin.math.max

/**
 * Lightning on the route map (#907): every flash in the corridor box over the last hour, drawn
 * as a small bolt faded by its own age (a ten-minute accumulation drawn flat would suggest every
 * flash happened at once). Pure so the rules (which flashes are drawn, how faded, what the badge
 * says, when the feed is late) are unit-testable; the map only paints the result.
 *
 * SYNC: port of iOS `Views/Map/ObservedLightning.swift` (itself a port of the web's
 * `observed-overlay-geometry.ts` flash rules). Pinned by `ObservedLightningRulesTest`, the iOS
 * `ObservedLightningTests` fixture.
 */
object ObservedLightningRules {

    /** Lightning older than this is dropped rather than drawn nearly invisible. */
    const val TRAIL_MINUTES: Double = 60.0

    /**
     * MTG LI publishes every 10 min; past two missed cycles the feed is late and the badge says
     * newer flashes may be missing (cells use the same 25).
     */
    const val STALE_AFTER_MINUTES: Double = 25.0

    /** MTG LI lands every 10 min; a 2-minute poll keeps the fade and the age honest. */
    const val POLL_INTERVAL_MS: Long = 120_000L

    /**
     * A small lightning bolt, polygon vertices in a unit box ((0,0) top-left, y down). A bolt
     * rather than a dot so a flash reads as lightning without a legend and is not mistaken for
     * a radar echo or an airport marker.
     */
    val GLYPH: List<Pair<Double, Double>> = listOf(
        0.62 to 0.0,
        0.08 to 0.58,
        0.44 to 0.58,
        0.3 to 1.0,
        0.92 to 0.4,
        0.56 to 0.4,
        0.78 to 0.0,
    )

    /** On-screen size of the bolt, in dp. */
    const val GLYPH_WIDTH_DP: Float = 8f
    const val GLYPH_HEIGHT_DP: Float = 12f

    /** Web `FLASH_COLOR` (white) and `FLASH_RIM` (#6d28d9): the rim keeps it readable on bright cloud. */
    const val FILL_ARGB: Long = 0xFFFFFFFFL
    const val RIM_ARGB: Long = 0xFF6D28D9L

    /** Opacity for a flash of a given age: linear fade to nothing at the trail end. */
    fun opacity(ageMinutes: Double): Double = when {
        ageMinutes <= 0 -> 0.9
        ageMinutes >= TRAIL_MINUTES -> 0.0
        else -> 0.9 * (1 - ageMinutes / TRAIL_MINUTES)
    }

    /** A flash ready to draw: where it is and how faded. */
    data class DrawableFlash(val lat: Double, val lon: Double, val opacity: Double)

    /**
     * Flashes still inside the trail with their faded opacity, oldest first so the most recent
     * draw on top. Unparseable times are dropped.
     */
    fun drawable(flashes: List<ObservedFlash>, nowMs: Long): List<DrawableFlash> =
        flashes
            .mapNotNull { flash ->
                val time = IsoTime.parseEpochMs(flash.time) ?: return@mapNotNull null
                val age = (nowMs - time) / 60_000.0
                if (age < TRAIL_MINUTES) flash to age else null
            }
            .sortedByDescending { it.second }
            .map { (flash, age) -> DrawableFlash(flash.lat, flash.lon, opacity(age)) }
            .filter { it.opacity > 0 }

    /** The bolt's vertices centred on ([cx], [cy]), in the caller's units. */
    fun glyphPoints(cx: Float, cy: Float, width: Float, height: Float): List<Pair<Float, Float>> =
        GLYPH.map { (x, y) -> (cx + (x.toFloat() - 0.5f) * width) to (cy + (y.toFloat() - 0.5f) * height) }

    /**
     * `/api/observed/flashes` query for the corridor box, last [TRAIL_MINUTES] only (asking for
     * the trail keeps a phone's payload to what is drawn).
     */
    fun flashesQuery(box: LatLonBox): List<Pair<String, String>> = listOf(
        "south" to ObservedMapRules.fixed(box.south, 4),
        "west" to ObservedMapRules.fixed(box.west, 4),
        "north" to ObservedMapRules.fixed(box.north, 4),
        "east" to ObservedMapRules.fixed(box.east, 4),
        "minutes" to TRAIL_MINUTES.toInt().toString(),
    )

    /** What the map knows about lightning right now. */
    sealed interface State {
        /** Nothing asked yet / first request in flight: say nothing. */
        data object Loading : State

        /** The request failed (no server, offline, observed disabled). */
        data object Unavailable : State

        data class Loaded(val response: ObservedFlashesResponse) : State
    }

    /** Age of the newest lightning frame, null when none is held. */
    fun newestAgeMinutes(response: ObservedFlashesResponse, nowMs: Long): Double? {
        val valid = IsoTime.parseEpochMs(response.newestValidTime) ?: return null
        return max(0.0, (nowMs - valid) / 60_000.0)
    }

    /**
     * "Lightning 14:05Z · 6 min old · 12 flashes in the last 60 min · <attribution>".
     *
     * "Unavailable" and "no current frame" are said as such, never as "no lightning"; a late
     * feed says newer flashes may be missing, so an empty patch of map is not read as a storm
     * that has stopped.
     */
    fun badge(state: State, nowMs: Long): String? = when (state) {
        State.Loading -> null
        State.Unavailable -> "Lightning unavailable"
        is State.Loaded -> {
            val response = state.response
            val age = newestAgeMinutes(response, nowMs)
            if (response.newestValidTime == null || age == null) {
                "Lightning: no current frame"
            } else {
                buildString {
                    append(ObservedMapRules.ageText(response.newestValidTime, age, "Lightning"))
                    if (age > STALE_AFTER_MINUTES) append(" · feed late, newer flashes may be missing")
                    append(" · ${flashCountText(drawable(response.flashes, nowMs).size)}")
                    response.attribution?.text?.takeIf { it.isNotEmpty() }?.let { append(" · $it") }
                }
            }
        }
    }

    /** "1 flash" / "12 flashes" in the last hour. Flashes, not strikes: MTG LI sees in-cloud lightning too. */
    fun flashCountText(count: Int): String {
        val noun = if (count == 1) "flash" else "flashes"
        return "$count $noun in the last ${TRAIL_MINUTES.toInt()} min"
    }

    /** The layer's chip in the map's summary line: its own age, orange when unavailable or late. */
    fun chip(state: State, nowMs: Long): ObservedSummaryChip? = when (state) {
        State.Loading -> null
        State.Unavailable -> ObservedSummaryChip("Lightning", "n/a", isWarning = true)
        is State.Loaded -> {
            val age = newestAgeMinutes(state.response, nowMs)
            if (age == null) {
                ObservedSummaryChip("Lightning", "n/a", isWarning = true)
            } else {
                ObservedSummaryChip("Lightning", ObservedMapRules.chipAge(age), isWarning = age > STALE_AFTER_MINUTES)
            }
        }
    }

    /** The legend line (iOS details sheet). */
    val LEGEND: String = "one flash, fading over ${TRAIL_MINUTES.toInt()} min"
}
