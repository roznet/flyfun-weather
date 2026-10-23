package aero.flyfun.weather.model

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit

/**
 * Pure rules for editing a flight, ported from the web (`flight-main.ts`,
 * `detectStructuralChange`) and iOS (`AddFlightViewModel`, #552).
 *
 * The flight id is built from the route's endpoints and the departure's **UTC** date, so
 * those are the flight's identity: `PATCH /api/flights/{id}` refuses them (422) and the
 * client offers Move (replace, discarding the briefings) or Duplicate (keep both) instead.
 * Everything else is a PATCH, whose effect on the briefing is previewed by [impact] and
 * then decided by the server's `invalidation` hint.
 */
object FlightEditRules {

    /** What an edit will do to the flight's briefing, worst first. */
    enum class Impact {
        /** Date, origin or destination changed: a different flight (Move or Duplicate). */
        STRUCTURAL,

        /** Time, mid-route waypoints or duration: the briefing is regenerated. */
        REBRIEF,

        /** Altitude, ceiling or profile: the advisories are recomputed. */
        ADVISORIES,

        /** Aircraft, Flexibility, description: saved, nothing to recompute. */
        NONE,
    }

    /** The edited values [impact] compares with the flight. */
    data class EditedValues(
        val routeInput: String,
        val waypoints: List<String>,
        val departure: Instant,
        val cruiseAltitudeFt: Int,
        val flightCeilingFt: Int,
        val durationChanged: Boolean,
        val profileId: Int?,
    )

    /**
     * The text the route field is seeded with: the flight's Field-15 `raw_route` when it has
     * one, else its waypoints. Seeding from the waypoints would replace the pilot's
     * `EGKA N0180VFR DCT EGKB` with the parser's reading of it on every edit.
     */
    fun baselineRouteInput(flight: FlightResponse): String {
        val raw = flight.rawRoute?.trim().orEmpty()
        return raw.ifEmpty { flight.waypoints.joinToString(" ") }
    }

    /** Case- and spacing-insensitive form of a route input, for change detection. */
    fun normalizeRouteInput(text: String): String =
        text.uppercase().split(ROUTE_SEPARATORS).filter { it.isNotBlank() }.joinToString(" ")

    /** Separators between route tokens: whitespace, commas and `->` arrows. */
    val ROUTE_SEPARATORS = Regex("[\\s,\\->]+")

    /**
     * Whether the route *text* differs from what the form was seeded with. Compares text, not
     * parsed waypoints: a Field-15 baseline's naive token split is not a waypoint list.
     */
    fun routeInputChanged(text: String, flight: FlightResponse): Boolean =
        normalizeRouteInput(text) != normalizeRouteInput(baselineRouteInput(flight))

    /** The UTC calendar day of an instant: the unit the server derives `target_date` in. */
    fun utcDay(instant: Instant): LocalDate = instant.atZone(ZoneOffset.UTC).toLocalDate()

    /**
     * Whether [edited] falls on another **UTC** day than [original]. A pilot in Paris picking
     * 00:30 local is 22:30 UTC the day before: a date change even though the local day agrees.
     */
    fun departureDayChanged(edited: Instant, original: Instant): Boolean =
        utcDay(edited) != utcDay(original)

    /**
     * Whether the first or last waypoint changed. A mid-route insert or removal is not
     * structural: the flight id only encodes the endpoints.
     */
    fun endpointsChanged(edited: List<String>, original: List<String>): Boolean {
        val newFirst = edited.firstOrNull()?.uppercase() ?: return false
        val newLast = edited.lastOrNull()?.uppercase() ?: return false
        val oldFirst = original.firstOrNull()?.uppercase() ?: return false
        val oldLast = original.lastOrNull()?.uppercase() ?: return false
        return newFirst != oldFirst || newLast != oldLast
    }

    /** Whether the edit changes something the flight id is built from (PATCH would 422). */
    fun isStructural(values: EditedValues, flight: FlightResponse): Boolean {
        val original = flight.departureInstant ?: return false
        if (departureDayChanged(values.departure, original)) return true
        return routeInputChanged(values.routeInput, flight) &&
            endpointsChanged(values.waypoints, flight.waypoints)
    }

    /** The preview of what saving [values] does, mirroring the server's invalidation rules. */
    fun impact(values: EditedValues, flight: FlightResponse): Impact {
        if (isStructural(values, flight)) return Impact.STRUCTURAL
        val timeChanged = flight.departureInstant?.let { it != values.departure } ?: false
        if (timeChanged || values.durationChanged || routeInputChanged(values.routeInput, flight)) {
            return Impact.REBRIEF
        }
        if (values.cruiseAltitudeFt != flight.cruiseAltitudeFt ||
            values.flightCeilingFt != flight.flightCeilingFt ||
            values.profileId != flight.profileId
        ) {
            return Impact.ADVISORIES
        }
        return Impact.NONE
    }

    /** One line under the form saying what Save will do. */
    fun impactLine(impact: Impact): String = when (impact) {
        Impact.STRUCTURAL -> "This becomes a different flight: move it or keep both."
        Impact.REBRIEF -> "The briefing will be regenerated."
        Impact.ADVISORIES -> "Advisories will be recomputed."
        Impact.NONE -> "Saves instantly."
    }

    /**
     * Move [instant] onto [reference]'s UTC day, keeping its UTC time of day. The server
     * requires the alternate departure on the departure's UTC day.
     */
    fun alignUtcDay(instant: Instant, reference: Instant): Instant {
        val time = instant.atZone(ZoneOffset.UTC).toLocalTime()
        return utcDay(reference).atTime(time).toInstant(ZoneOffset.UTC)
    }

    /** What Move discards, phrased without a number while the pack count is unknown. */
    fun moveConsequence(packCount: Int?, isLive: Boolean): String {
        val briefings = when (packCount) {
            null -> "Its existing briefings are discarded."
            0 -> "It has no briefings yet."
            1 -> "Its 1 briefing is discarded."
            else -> "Its $packCount briefings are discarded."
        }
        val live = if (isLive) " The live layer and tracking for today are dropped." else ""
        return "Replaces this flight. $briefings Share link kept.$live"
    }
}

/**
 * Parsed departure, at the form's minute precision so a stored departure with seconds
 * doesn't read as a time change (and a needless re-brief); null when not valid ISO 8601.
 */
val FlightResponse.departureInstant: Instant?
    get() = departureEpochMs?.let { Instant.ofEpochMilli(it).truncatedTo(ChronoUnit.MINUTES) }
