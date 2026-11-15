package aero.flyfun.weather.model

import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

/**
 * The flight's basics in words for the Advisory tab's summary strip (#996): where from and to,
 * when, how long, how high, in what.
 *
 * SYNC: mirrors iOS `FlightSummary` (Views/Briefing/FlightSummaryStrip.swift), same lines and
 * order. iOS also swaps in live take-off / ETA / landing times; the Android live layer doesn't
 * decode the progress block yet, so this shows the plan.
 */
object FlightSummary {
    data class Lines(
        /** "EGTF 10:00Z → LFAT 11:25Z" */
        val main: String,
        /** "Sat 21 Nov · 1h25 · FL065 · DA42" */
        val detail: String,
        /** The route between the ends, null for a direct flight. */
        val route: String?,
    )

    fun lines(flight: FlightResponse): Lines {
        val from = flight.waypoints.firstOrNull()?.uppercase(Locale.US).orEmpty()
        val to = flight.waypoints.lastOrNull()?.uppercase(Locale.US).orEmpty()
        val departure = flight.departureEpochMs?.let(Instant::ofEpochMilli)
        val arrival = departure?.plusSeconds((flight.flightDurationHours * 3600).roundToInt().toLong())

        val main = buildString {
            append(from)
            departure?.let { append(" ").append(hhmm(it)) }
            append(" → ").append(to)
            arrival?.let { append(" ").append(hhmm(it)) }
        }
        val detail = buildList {
            departure?.let { add(DAY.format(it)) }
            if (flight.flightDurationHours > 0) add(durationLabel(flight.flightDurationHours))
            if (flight.cruiseAltitudeFt > 0) add(String.format(Locale.US, "FL%03d", flight.cruiseAltitudeFt / 100))
            flight.aircraft?.let { add(aircraftName(it)) }
        }.joinToString(" · ")
        return Lines(main = main, detail = detail, route = route(flight))
    }

    /** The pilot's own route text when captured, else the waypoints between the ends. */
    fun route(flight: FlightResponse): String? {
        flight.rawRoute?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        if (flight.waypoints.size <= 2) return null
        return flight.waypoints.drop(1).dropLast(1).joinToString(" ") { it.uppercase(Locale.US) }
    }

    /** "1h25", "0h45", "2h00": exact minutes, not the form's quarter-hour steps. */
    fun durationLabel(hours: Double): String {
        val minutes = (hours * 60).roundToInt()
        return String.format(Locale.US, "%dh%02d", minutes / 60, minutes % 60)
    }

    /** Same order as iOS `AircraftInfo.displayName`: nickname, tail number, type. */
    private fun aircraftName(aircraft: AircraftInfo): String = when {
        !aircraft.nickname.isNullOrEmpty() -> aircraft.nickname
        !aircraft.tailNumber.isNullOrEmpty() -> aircraft.tailNumber
        else -> aircraft.typeName
    }

    private fun hhmm(instant: Instant): String = TIME.format(instant) + "Z"

    private val TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.US).withZone(ZoneOffset.UTC)
    private val DAY: DateTimeFormatter = DateTimeFormatter.ofPattern("EEE d MMM", Locale.US).withZone(ZoneOffset.UTC)
}
