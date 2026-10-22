package aero.flyfun.weather.model

import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.abs

/**
 * Absolute date `fc.date=YYYY-MM-DD` (UTC) of a forecast-map link (#840, #860).
 *
 * `fc.day` is relative to the opener's UTC today, so on its own a link drifts a day per day
 * unopened; `fc.date` names the calendar date and wins over `fc.day`. Same rules as the web's
 * `resolveLinkedDate` (`web/ts/visualization/forecast-url.ts`) and iOS
 * `ForecastMapViewModel.resolveLinkedDate`. Spec: `designs/forecast-page.md`.
 */
object ForecastLinkedDate {

    /** Why a linked date could not be shown as is. */
    enum class Fallback { PAST, BEYOND, UNAVAILABLE }

    /** [day] is the relative day to show; [fallback] is set when it is not the linked date. */
    data class Resolved(val day: Int, val fallback: Fallback?)

    private val SHAPE = Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}")
    private val LABEL = DateTimeFormatter.ofPattern("EEE d MMM", Locale.US)

    /**
     * A strict `YYYY-MM-DD` (ASCII digits, real calendar date), or null: a sign such as `+026`,
     * a short field (`2026-10-2`) or a date the calendar would roll forward (`2026-02-30`).
     */
    fun parse(s: String?): LocalDate? {
        if (s == null || !SHAPE.matches(s)) return null
        // ISO_LOCAL_DATE resolves STRICT, so 2026-02-30 throws instead of becoming 2026-03-02
        return runCatching { LocalDate.parse(s, DateTimeFormatter.ISO_LOCAL_DATE) }.getOrNull()
    }

    /**
     * Resolve a linked date to a relative day against the [offered] (available) days. Not offered
     * → nearest offered day, never silently: a past date shows the first offered day (today), one
     * beyond the horizon the last, one inside it but not fetched yet the nearest (ties to the
     * earlier). Null for a malformed date or an empty grid (`fc.day` applies).
     */
    fun resolve(date: String, offered: List<Int>, today: LocalDate): Resolved? {
        val linked = parse(date) ?: return null
        if (offered.isEmpty()) return null
        val day = ChronoUnit.DAYS.between(today, linked)
        val sorted = offered.distinct().sorted()
        if (sorted.any { it.toLong() == day }) return Resolved(day.toInt(), null)
        if (day < sorted.first()) return Resolved(sorted.first(), Fallback.PAST)
        if (day > sorted.last()) return Resolved(sorted.last(), Fallback.BEYOND)
        val nearest = sorted.reduce { a, b -> if (abs(b - day) < abs(a - day)) b else a }
        return Resolved(nearest, Fallback.UNAVAILABLE)
    }

    /**
     * The notice for [resolved], or null when the link is shown as is. Against the fallback grid
     * (the days request failed, [gridIsFallback]) only "has passed" is a true claim: "beyond" or
     * "not forecast yet" would be invented by the guessed D+0…D+3.
     */
    fun noticeFor(linked: String, resolved: Resolved, gridIsFallback: Boolean, today: LocalDate): String? {
        val fallback = resolved.fallback ?: return null
        if (gridIsFallback && fallback != Fallback.PAST) return null
        return notice(linked, resolved.day, fallback, today)
    }

    /** "This link was for Sat 25 Oct, which has passed: showing Tue 28 Oct instead." */
    fun notice(linked: String, shownDay: Int, fallback: Fallback, today: LocalDate): String {
        val linkedLabel = parse(linked)?.format(LABEL) ?: linked
        val shownLabel = today.plusDays(shownDay.toLong()).format(LABEL)
        return when (fallback) {
            Fallback.PAST -> "This link was for $linkedLabel, which has passed: showing $shownLabel instead."
            Fallback.BEYOND -> "This link was for $linkedLabel, beyond the forecast range: showing $shownLabel, the last forecast day."
            Fallback.UNAVAILABLE -> "This link was for $linkedLabel, not forecast yet: showing $shownLabel instead."
        }
    }
}
