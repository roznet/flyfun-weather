package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.AirportObservation
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.LiveGlanceTerminal
import kotlin.math.roundToLong

/**
 * Layout choices for the Observed airport cards (#883, part of #737).
 *
 * Every value the card shows is the server's (#880: `glance.departure` /
 * `glance.destination` and their `airport` row): meter positions, sky and
 * visibility words, weather words, the short wind, the crosswind reading,
 * the trend, the TAF hours and the fold. This only picks which of them to
 * draw and in what order; a missing field is left out, never derived here.
 *
 * SYNC: web `airport-card-view.ts` (#881) and iOS (#882) draw the same card
 * from the same fields.
 */
object AirportCardRules {

    /** METAR age past which the provenance line turns amber (web `OBSERVED_STALE_MIN`). */
    const val STALE_AFTER_MIN: Long = 30

    enum class Role { DEPARTURE, ARRIVAL }

    /** A header box: filled = prevailing, dashed = a worse TEMPO/PROB group. */
    data class CategoryBox(val category: String, val caption: String, val dashed: Boolean = false)

    /** One slot of the cards area, in drawing order. */
    data class Slot(val role: Role, val block: LiveGlanceTerminal, val folded: Boolean)

    /**
     * Departure then arrival; once the server folds the departure, the arrival
     * comes first and the departure follows as its one-line fold.
     */
    fun slots(departure: LiveGlanceTerminal?, destination: LiveGlanceTerminal?): List<Slot> {
        val folded = departure?.folded == true
        val dep = departure?.let { Slot(Role.DEPARTURE, it, folded) }
        val arr = destination?.let { Slot(Role.ARRIVAL, it, false) }
        return if (folded) listOfNotNull(arr, dep) else listOfNotNull(dep, arr)
    }

    /** METAR now → TAF at ETD/ETA, plus the dashed TEMPO/PROB box when the server sets one. */
    fun headerBoxes(block: LiveGlanceTerminal, role: Role): List<CategoryBox> {
        val a = block.airport
        val now = a?.metarFlightCategory ?: block.metarCategory
        val at = a?.tafPrevailingCategoryAtEta
        val temp = a?.tafTemporaryCategoryAtEta
        return buildList {
            if (now != null) add(CategoryBox(now, "now"))
            if (at != null) add(CategoryBox(at, if (role == Role.DEPARTURE) "at ETD" else "at ETA"))
            if (at != null && temp != null) add(CategoryBox(temp, shortType(a?.tafTemporaryType), dashed = true))
        }
    }

    /** "PROB30 TEMPO" → "PROB30": the box has room for one word. */
    fun shortType(type: String?): String = (type ?: "TEMPO").trim().split(" ").first().ifEmpty { "TEMPO" }

    /** The server's 0..1 meter position, clamped; null draws no dot. */
    fun meterPosition(value: Double?): Float? =
        value?.takeIf { it.isFinite() }?.coerceIn(0.0, 1.0)?.toFloat()

    /** ◀ for a crosswind from the left, ▶ from the right, nothing down the runway. */
    fun crosswindGlyph(side: String?): String = when (side) {
        "left" -> "◀ "
        "right" -> "▶ "
        else -> ""
    }

    /** The crosswind's tone: the server's METAR wind advisory, amber or red only. Green is quiet. */
    fun crosswindTone(airport: AirportObservation): String? =
        airport.metarWindAdvisory?.lowercase()?.takeIf { it == "amber" || it == "red" }

    /** "↘ was VFR 09:00Z" / "↗ was MVFR 07:20Z", or null without a server trend. */
    fun trendText(airport: AirportObservation?): String? {
        val trend = airport?.metarTrend ?: return null
        val prev = airport.metarPreviousFlightCategory ?: return null
        val arrow = if (trend == "worse") "↘" else "↗"
        val time = hhmmZ(airport.metarPreviousTime)
        return listOf("$arrow was $prev", time).filter { it.isNotEmpty() }.joinToString(" ")
    }

    /** "09:30Z" from an ISO time, "" without one. */
    fun hhmmZ(iso: String?): String {
        if (iso.isNullOrBlank()) return ""
        val t = iso.indexOf('T')
        return if (t != -1 && iso.length >= t + 6) "${iso.substring(t + 1, t + 6)}Z" else ""
    }

    /** "10Z": a TAF strip hour label. */
    fun hourLabel(iso: String?): String = hhmmZ(iso).takeIf { it.length >= 2 }?.let { "${it.take(2)}Z" } ?: ""

    /** Whole minutes from [iso] to [nowMs], never negative; null without a time. */
    fun minutesAgo(iso: String?, nowMs: Long): Long? {
        val at = IsoTime.parseEpochMs(iso) ?: return null
        return ((nowMs - at) / 60_000.0).roundToLong().coerceAtLeast(0)
    }

    fun isStale(minutes: Long?): Boolean = minutes != null && minutes > STALE_AFTER_MIN

    /** "8 min", "1 h 05". */
    fun ageText(minutes: Long): String =
        if (minutes < 60) "$minutes min" else "${minutes / 60} h ${(minutes % 60).toString().padStart(2, '0')}"

    /** "ETD 10:00Z" / "ETA 11:35Z", or null when the server has no time. */
    fun timeText(block: LiveGlanceTerminal, role: Role): String? {
        val time = if (role == Role.DEPARTURE) block.etd else block.eta
        val hhmm = hhmmZ(time).ifEmpty { return null }
        return if (role == Role.DEPARTURE) "ETD $hhmm" else "ETA $hhmm"
    }

    /** No TAF category at the ETA: the server's own reason ("no TAF for ETA"). */
    fun tafReason(block: LiveGlanceTerminal): String? =
        block.tafText.takeIf { block.airport?.tafPrevailingCategoryAtEta == null && it.isNotBlank() }

    /** The folded line's category box: the block's METAR category. */
    fun foldedCategory(block: LiveGlanceTerminal): String? =
        block.metarCategory ?: block.airport?.metarFlightCategory

    /**
     * The offline banner (#883 comment): "Offline · showing data from 09:40Z, 12 min ago",
     * from the layer's own `live_updated_at`. Null when the view is online.
     */
    fun offlineBannerText(offline: Boolean, liveUpdatedAt: String?, nowMs: Long): String? {
        if (!offline) return null
        val hhmm = hhmmZ(liveUpdatedAt)
        if (hhmm.isEmpty()) return "Offline · no live data yet"
        val age = minutesAgo(liveUpdatedAt, nowMs)
        return if (age == null) "Offline · showing data from $hhmm" else "Offline · showing data from $hhmm, ${ageText(age)} ago"
    }
}
