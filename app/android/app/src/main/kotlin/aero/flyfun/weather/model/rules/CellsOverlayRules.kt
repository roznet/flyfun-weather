package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.CellFrame
import aero.flyfun.weather.model.CellFramesResponse
import aero.flyfun.weather.model.CellMotion
import aero.flyfun.weather.model.CellTrend
import aero.flyfun.weather.model.DisplayCell
import aero.flyfun.weather.model.IsoTime
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * The experimental radar-cell overlay on the route map (#907): outlines per tier, a marker per
 * core coloured by **trend** (evolution, not safety), and a magenta arrow to where a core would
 * be in 30 min, only for measured motion. Everything here is pure so the rules (which overlay to
 * draw, when it is too old, what the badge and the cell card say) are unit-testable; the map
 * only paints the result.
 *
 * Nothing is computed on the phone: the arrow end is the feed's own `arrow` point, the trend and
 * motion words are the feed's own fields put into words.
 *
 * SYNC: port of iOS `Views/Map/CellsOverlay.swift` (itself a port of the web's
 * `cells-overlay-core.ts`). A wording, colour or threshold change there lands here too
 * (pinned by `CellsOverlayRulesTest`, the iOS `CellsOverlayTests` fixture).
 */
object CellsOverlayRules {

    /** Cells are fetched for the corridor box widened by this much (iOS `CellsOverlay.marginNm`). */
    const val MARGIN_NM: Double = 50.0

    /** The corridor used when the briefing sampled none (iOS `BriefingViewModel.cellsBox`). */
    const val DEFAULT_CORRIDOR_NM: Double = 20.0

    /** Magenta motion arrow (web `ARROW_COLOUR`). */
    const val ARROW_ARGB: Long = 0xFFB000B5L

    /** Marker rim (iOS `UIColor(white: 0.13)`). */
    const val MARKER_RIM_ARGB: Long = 0xFF212121L

    /** Legend order. */
    val TREND_ORDER: List<String> = listOf("developing", "steady", "decaying", "mixed", "new")

    /** Trend colour; same table as the storm cards ([LiveStormRules.TREND_HEX] = iOS `trendHex`). */
    fun trendColorArgb(state: String?): Long = LiveStormRules.trendColorArgb(state)

    /** Outline stroke per tier: ARGB and width in dp. `rain20` is never drawn on the route map. */
    fun outlineStyle(tier: String): Pair<Long, Float> = when (tier) {
        "core41" -> 0xFF000000L to 1.5f
        "core35" -> 0xFFFFFFFFL to 1.5f
        else -> 0xFF8A8A8AL to 1f
    }

    /** The tiers drawn as outlines, bottom first (core35 under core41). */
    val OUTLINE_TIERS: List<String> = listOf("core35", "core41")

    fun tierLabel(tier: String): String = when (tier) {
        "rain20" -> "Rain area ≥20 dBZ"
        "core35" -> "Core ≥35 dBZ"
        "core41" -> "Core ≥41 dBZ"
        else -> tier
    }

    /** Marker radius in dp (web circleMarker radius). */
    fun markerRadiusDp(tier: String): Float = when (tier) {
        "core41" -> 7f
        "core35" -> 5f
        else -> 6f
    }

    /**
     * The arrow's end, `(lat, lon)`, only for measured motion on a core: never for withheld or
     * unsupported motion even if an arrow point were present, never for a rain area.
     */
    fun arrowEnd(cell: DisplayCell): Pair<Double, Double>? {
        if (!cell.isCore || cell.motion?.status != "available") return null
        val arrow = cell.arrow ?: return null
        if (arrow.size != 2) return null
        return arrow[0] to arrow[1]
    }

    /** Polylines of one tier as `(lat, lon)` lists, dropping malformed points and lines under 2 points. */
    fun outlines(display: CellDisplay, tier: String): List<List<Pair<Double, Double>>> =
        display.outlines?.get(tier).orEmpty()
            .map { line -> line.mapNotNull { p -> if (p.size == 2) p[0] to p[1] else null } }
            .filter { it.size >= 2 }

    /** The cells the route map draws: cores only (web parity). */
    fun drawnCells(display: CellDisplay): List<DisplayCell> = display.cells.filter { it.isCore }

    // ---- Which overlay to draw ----

    sealed interface Match {
        data class Ok(val frame: CellFrame) : Match
        data object Disabled : Match
        data class Unavailable(val since: String?) : Match
    }

    /**
     * Which overlay to draw. The overlay for the same stamp as [radarStamp] when there is one,
     * else the newest at or before it; null [radarStamp] means the newest. Then the overlay's
     * own age decides: past `staleAfterMinutes` nothing is drawn and the map says "unavailable
     * since" that overlay's time. Never a stale picture.
     */
    fun match(info: CellFramesResponse?, radarStamp: String? = null, nowMs: Long): Match {
        if (info == null) return Match.Unavailable(null)
        if (!info.enabled) return Match.Disabled
        val frames = info.frames
        val pick = if (radarStamp != null) {
            frames.firstOrNull { it.stamp == radarStamp } ?: frames.firstOrNull { it.stamp <= radarStamp }
        } else {
            frames.firstOrNull()
        }
        if (pick == null) return Match.Unavailable(frames.firstOrNull()?.validTime ?: info.unavailableSince)
        val valid = IsoTime.parseEpochMs(pick.validTime) ?: return Match.Unavailable(pick.validTime)
        if ((nowMs - valid) / 60_000.0 > info.staleAfterMinutes) return Match.Unavailable(pick.validTime)
        return Match.Ok(pick)
    }

    /** The cells box: the corridor plus [MARGIN_NM] (iOS `BriefingViewModel.cellsBox`). */
    fun cellsBox(points: List<Pair<Double, Double>>, corridorNm: Double?): LatLonBox? =
        ObservedMapRules.corridorBox(points, (corridorNm ?: DEFAULT_CORRIDOR_NM) + MARGIN_NM)

    /** The display file's box query, two decimals (iOS `displayPath`); empty without a box. */
    fun boxQuery(box: LatLonBox?): List<Pair<String, String>> {
        box ?: return emptyList()
        return listOf(
            "south" to ObservedMapRules.fixed(box.south, 2),
            "west" to ObservedMapRules.fixed(box.west, 2),
            "north" to ObservedMapRules.fixed(box.north, 2),
            "east" to ObservedMapRules.fixed(box.east, 2),
        )
    }

    // ---- Words ----

    fun hhmmZ(iso: String?): String = AirportCardRules.hhmmZ(iso).ifEmpty { "--:--Z" }

    private fun ageMinutes(validTime: String, nowMs: Long): Double? =
        IsoTime.parseEpochMs(validTime)?.let { max(0.0, (nowMs - it) / 60_000.0) }

    /** The overlay's badge line: its own time, never the radar's. */
    fun badge(match: Match, display: CellDisplay?, nowMs: Long): String = when (match) {
        Match.Disabled -> "Cell analysis: not available on this server"
        is Match.Unavailable ->
            if (match.since != null) {
                "Cell analysis unavailable since ${hhmmZ(match.since)}"
            } else {
                "Cell analysis unavailable (nothing received yet)"
            }
        is Match.Ok -> buildString {
            val age = ageMinutes(match.frame.validTime, nowMs)?.roundToInt() ?: 0
            append("Cells ${hhmmZ(match.frame.validTime)} · $age min old")
            if (display != null) append(" · ${display.cells.size} cells")
            // Never "no lightning" (reads as "none in these cells"): pending (on its way, #666)
            // and unavailable are said as such.
            display?.pending?.takeIf { it.isNotEmpty() }?.let { append(" · " + it.joinToString(", ") + " pending") }
            display?.unavailable?.takeIf { it.isNotEmpty() }?.let {
                append(" · " + it.joinToString(", ") { u -> u.what } + " unavailable")
            }
            append(" · experimental")
        }
    }

    /** The overlay's chip in the map's summary line: its own age, never the radar's. */
    fun chip(match: Match, nowMs: Long): ObservedSummaryChip = when (match) {
        Match.Disabled, is Match.Unavailable -> ObservedSummaryChip("Cells", "n/a", isWarning = true)
        is Match.Ok -> ObservedSummaryChip(
            "Cells",
            ObservedMapRules.chipAge(ageMinutes(match.frame.validTime, nowMs) ?: 0.0),
            isWarning = false,
        )
    }

    private val COMPASS_POINTS = listOf(
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )

    fun compass(deg: Double?): String {
        if (deg == null || !deg.isFinite()) return "–"
        val normalised = ((deg % 360.0) + 360.0) % 360.0
        return COMPASS_POINTS[(normalised / 22.5).roundToInt() % 16]
    }

    /** "moving NE at 9 kt", or why there is no motion: in words, not codes. */
    fun motionText(m: CellMotion?): String {
        val status = m?.status ?: return "motion unknown"
        return when (status) {
            "available" -> {
                val toward = m.towardDeg
                val speed = m.speedKt ?: 0.0
                if (toward == null || speed < 1) "nearly stationary" else "moving ${compass(toward)} at ${speed.roundToInt()} kt"
            }
            // A split or merge this frame: the matcher could not pair it yet (#689).
            "withheld" -> "motion not yet measured"
            "unsupported" -> "motion withheld: too little of the cell in matched tiles"
            "no_pair" -> "no motion yet: no earlier radar frame to compare"
            else -> if (!m.reason.isNullOrEmpty()) "motion $status: ${m.reason}" else "motion $status"
        }
    }

    fun trendText(t: CellTrend?): String {
        t ?: return "trend unknown"
        val window = t.windowMin
        if (t.state == "new" || window == null) return "new (less than 15 min of history)"
        val parts = buildList {
            t.dPeakDb?.let { add("peak ${signed(it)} dB") }
            t.areaRatio?.let { add("area ×${number(it)}") }
            t.dFlashes?.let { add("flashes ${signed(it)}") }
        }
        val head = "${t.state ?: "unknown"} over ${window.roundToInt()} min"
        return if (parts.isEmpty()) head else "$head (${parts.joinToString(", ")})"
    }

    /** The cell's measurements, one clause per line: descriptive only (web `cellPopupHtml`). */
    fun detailLines(c: DisplayCell): List<String> = buildList {
        add("peak ${dbzText(c.peakDbz)} · area ${value(c.areaKm2, " km²")}")
        add(
            "rain rate peak ${value(c.ratePeakMmH, " mm/h")}${rateAsOfText(c)} · lightning ${lightningText(c)}" +
                (c.topFl?.let { " · cloud top FL${it.roundToInt()}" } ?: ""),
        )
        add("age ${value(c.ageMin, " min")} (${c.event ?: ""}) · ${trendText(c.trend)}")
        add(motionText(c.motion))
        if (c.truncated == true) add("partly outside radar coverage")
    }

    /** The caveat every cell surface carries. */
    const val CAVEAT: String = "Provisional thresholds: cells often flicker in as \"new\", rain rate can show " +
        "artefact peaks and lag the radar by ~10 min. Describes evolution, not safety."

    /** "pending" while the frame's lightning is on its way (#666), else the count or "–". */
    fun lightningText(c: DisplayCell): String =
        if (c.flashes == null && c.flashesPending == true) "pending" else value(c.flashes)

    /** " (as of HH:MMZ)" when the rain rate is older than the radar (#666), else "". */
    fun rateAsOfText(c: DisplayCell): String {
        if (c.ratePeakMmH == null) return ""
        val asOf = c.rateAsOf ?: return ""
        return " (as of ${hhmmZ(asOf)})"
    }

    /** Whole-number dBZ ("76 dBZ", not "75.5 dBZ", #689). */
    fun dbzText(v: Double?): String = v?.let { "${it.roundToInt()} dBZ" } ?: "–"

    /** "1 flash", "3 flashes". */
    fun flashesText(n: Double): String {
        val count = n.roundToInt()
        return if (count == 1) "1 flash" else "$count flashes"
    }

    fun value(v: Double?, unit: String = ""): String = v?.let { number(it) + unit } ?: "–"

    fun number(v: Double): String =
        if (v == Math.rint(v)) v.toLong().toString() else ObservedMapRules.fixed(v, 1)

    private fun signed(v: Double): String = (if (v > 0) "+" else "") + number(v)
}
