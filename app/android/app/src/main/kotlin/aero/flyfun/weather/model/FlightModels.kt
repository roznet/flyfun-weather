package aero.flyfun.weather.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.buildClassSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.math.min

@Serializable
enum class FlightRole(val wireValue: String) {
    @SerialName("owner")
    OWNER("owner"),

    @SerialName("subscriber")
    SUBSCRIBER("subscriber"),

    @SerialName("shared")
    SHARED("shared"),
}

@Serializable
enum class FlightSection(val wireValue: String, val title: String) {
    @SerialName("future")
    FUTURE("future", "Future"),

    @SerialName("recent")
    RECENT("recent", "Recent"),

    @SerialName("past")
    PAST("past", "Past");

    val startsCollapsed: Boolean
        get() = this == PAST

    companion object {
        fun fromString(s: String?): FlightSection? {
            if (s.isNullOrBlank()) return null
            val lower = s.trim().lowercase()
            return entries.firstOrNull { it.wireValue == lower }
        }
    }
}

@Serializable
enum class FlightNotifyOverride(val wireValue: String) {
    @SerialName("inherit")
    INHERIT("inherit"),

    @SerialName("on")
    ON("on"),

    @SerialName("off")
    OFF("off"),
}

@Serializable
enum class FlexibilityMode(val wireValue: String, val label: String) {
    @SerialName("fixed")
    FIXED("fixed", "Fixed"),

    @SerialName("day")
    DAY("day", "Same day"),

    @SerialName("multi_day")
    MULTI_DAY("multi_day", "Multi-day");

    val title: String
        get() = label

    fun daysLabel(days: Int): String = when (this) {
        FIXED -> "Fixed"
        DAY -> "Same day"
        MULTI_DAY -> "$days days"
    }
}

/**
 * The flight's timing-scenario mode, as the server stores it (`FlightResponse.flexibility`).
 * Mirrors iOS `FlexibilityMode`. Unknown values decode to [NONE] (see the serializer) so a
 * mode added server-side never fails the whole flight-list decode.
 */
@Serializable(with = FlightFlexibilitySerializer::class)
enum class FlightFlexibility(val wireValue: String, val label: String) {
    NONE("none", "None"),
    ALTERNATE("alternate", "Alternate time"),
    SAME_DAY("same_day", "Same day"),
    PREV_DAY("prev_day", "Previous day"),
    NEXT_DAY("next_day", "Next day"),
    ;

    companion object {
        fun fromWire(value: String?): FlightFlexibility =
            entries.firstOrNull { it.wireValue == value?.trim()?.lowercase() } ?: NONE
    }
}

object FlightFlexibilitySerializer : KSerializer<FlightFlexibility> {
    override val descriptor: SerialDescriptor =
        kotlinx.serialization.descriptors.PrimitiveSerialDescriptor(
            "aero.flyfun.weather.model.FlightFlexibility",
            kotlinx.serialization.descriptors.PrimitiveKind.STRING,
        )

    override fun deserialize(decoder: Decoder): FlightFlexibility =
        FlightFlexibility.fromWire(decoder.decodeString())

    override fun serialize(encoder: Encoder, value: FlightFlexibility) {
        encoder.encodeString(value.wireValue)
    }
}

@Serializable
data class CoveragePending(
    val model: String = "",
    val label: String = "",
    @SerialName("in_range_at")
    val inRangeAt: String = "",
)

@Serializable
data class AircraftInfo(
    val id: Int = 0,
    @SerialName("icao_type")
    val icaoType: String = "",
    @SerialName("type_name")
    val typeName: String = "",
    @SerialName("tail_number")
    val tailNumber: String? = null,
    val nickname: String? = null,
    @SerialName("cruise_speed_kt")
    val cruiseSpeedKt: Int? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
    @SerialName("is_ifr")
    val isIfr: Boolean = false,
    @SerialName("is_fiki")
    val isFiki: Boolean = false,
) {
    val displayLabel: String
        get() = when {
            !nickname.isNullOrEmpty() -> nickname
            !tailNumber.isNullOrEmpty() -> "$tailNumber ($typeName)"
            else -> typeName
        }
}

@Serializable
data class BriefingStatusInfo(
    @SerialName("last_updated")
    val lastUpdated: String? = null,
    val assessment: String? = null,
    @SerialName("days_out")
    val daysOut: Int? = null,
    @SerialName("has_digest")
    val hasDigest: Boolean = false,
)

@Serializable
data class AdvisorySummary(
    val green: Int = 0,
    val amber: Int = 0,
    val red: Int = 0,
    val chips: List<AdvisoryChip>? = null,
)

@Serializable
data class AdvisoryChip(
    val id: String = "",
    val name: String = "",
    val status: String = "",
)

@Serializable
data class TripLegRef(
    val id: String = "",
    @SerialName("trip_id")
    val legacyTripId: String = "",
    val name: String = "",
    val title: String = "",
    val role: String? = null,
    val position: Int = 0,
    @SerialName("leg_index")
    val legacyLegIndex: Int = 1,
    val total: Int = 0,
    @SerialName("leg_count")
    val legacyLegCount: Int = 1,
    @SerialName("auto_refresh")
    val autoRefresh: Boolean = false,
    @SerialName("continuity_warning")
    val continuityWarning: String? = null,
) {
    val tripId: String
        get() = id.ifBlank { legacyTripId }

    val resolvedTitle: String
        get() = name.ifBlank { title }

    val legIndex: Int
        get() = if (position > 0) position else legacyLegIndex

    val legCount: Int
        get() = if (total > 0) total else legacyLegCount

    val badgeText: String
        get() = if (resolvedTitle.isNotBlank()) {
            "$resolvedTitle · Leg $legIndex/$legCount"
        } else {
            "Leg $legIndex/$legCount"
        }
}

@Serializable
data class FlightResponse(
    val id: String,
    @SerialName("user_id")
    val userId: String = "",
    @SerialName("profile_id")
    val profileId: Int? = null,
    @SerialName("aircraft_id")
    val aircraftId: Int? = null,
    val aircraft: AircraftInfo? = null,
    @SerialName("route_name")
    val routeName: String = "",
    val waypoints: List<String> = emptyList(),
    @SerialName("departure_time")
    val departureTime: String = "",
    @SerialName("target_date")
    val targetDate: String = "",
    @SerialName("target_time_utc")
    val targetTimeUtc: Int = 0,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int = 8000,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int = 18000,
    @SerialName("flight_duration_hours")
    val flightDurationHours: Double = 2.0,
    val private: Boolean = false,
    @SerialName("auto_refresh")
    val autoRefresh: Boolean = true,
    @SerialName("auto_refresh_hour")
    val autoRefreshHour: Int? = null,
    @SerialName("effective_auto_refresh_hour")
    val effectiveAutoRefreshHour: Int? = null,
    @SerialName("created_at")
    val createdAt: String = "",
    /** The server's `latest_briefing` (older Android fixtures call it `briefing_status`). */
    @SerialName("latest_briefing")
    @kotlinx.serialization.json.JsonNames("briefing_status")
    val briefingStatus: BriefingStatusInfo? = null,
    @SerialName("advisory_summary")
    val advisorySummary: AdvisorySummary? = null,
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    @SerialName("alt_briefing_status")
    val altBriefingStatus: BriefingStatusInfo? = null,
    @SerialName("alt_advisory_summary")
    val altAdvisorySummary: AdvisorySummary? = null,
    val flexibility: FlightFlexibility = FlightFlexibility.NONE,
    /** Pilot's free-text purpose. Owner-only: a subscriber gets null. */
    val description: String? = null,
    /** The Field-15 text the pilot typed, when captured; the edit form is pre-filled from it. */
    @SerialName("raw_route")
    val rawRoute: String? = null,
    @SerialName("time_scan_status")
    val timeScanStatus: TimeScanStatusDTO? = null,
    @SerialName("time_confirmation")
    val timeConfirmation: TimeConfirmationDTO? = null,
    @SerialName("has_pireps")
    val hasPireps: Boolean? = null,
    val section: String? = null,
    @SerialName("debrief_eligible")
    val debriefEligible: Boolean? = null,
    @SerialName("has_debrief")
    val hasDebrief: Boolean? = null,
    val role: FlightRole? = null,
    @SerialName("owner_display_name")
    val ownerDisplayName: String? = null,
    @SerialName("subscriber_count")
    val subscriberCount: Int? = null,
    @SerialName("share_code")
    val shareCode: String? = null,
    @SerialName("notify_override")
    val notifyOverride: FlightNotifyOverride? = null,
    val trip: TripLegRef? = null,
    @SerialName("coverage_pending")
    val coveragePending: List<CoveragePending>? = null,
) {
    /** Short display title: `"ORIGIN → DEST"`. */
    val shortTitle: String
        get() {
            val first = waypoints.firstOrNull()
            val last = waypoints.lastOrNull()
            return if (first != null && last != null) {
                "${first.uppercase()} → ${last.uppercase()}"
            } else {
                routeName
            }
        }

    /** True when the current user owns this flight (or when role is absent on older caches). */
    val isEditable: Boolean
        get() = role != FlightRole.SUBSCRIBER

    /** Parsed departure timestamp in epoch milliseconds, if valid. */
    val departureEpochMs: Long?
        get() = IsoTime.parseEpochMs(departureTime)

    /** Formatted flight duration (e.g. `"2h 30m"` or `"4h"`). */
    val durationLabel: String
        get() = FlightDuration.label(flightDurationHours)

    /** Typed assessment from briefing status. */
    val resolvedAssessment: Assessment
        get() = Assessment.fromString(briefingStatus?.assessment)

    val latestAssessment: String?
        get() = briefingStatus?.assessment

    val tripId: String?
        get() = trip?.tripId?.takeIf { it.isNotBlank() }

    val tripName: String?
        get() = trip?.resolvedTitle?.takeIf { it.isNotBlank() }

    /** Server-computed section, falling back to date-based bucketing at the current clock time. */
    val flightSection: FlightSection
        get() = resolvedSection()

    /**
     * True once `departure + duration` has passed (`now > departure + duration`).
     * Mirrors `_flight_end_utc` in `api/flights.py`: an in-progress flight has not ended yet,
     * and the exact landing instant is still not ended (`>` rather than `>=`).
     */
    fun hasEnded(nowEpochMs: Long = System.currentTimeMillis()): Boolean {
        val dep = departureEpochMs ?: return false
        val durationMs = (flightDurationHours * 3_600_000.0).toLong()
        return nowEpochMs > dep + durationMs
    }

    /** Whether the flight has already landed (`departure + duration < now`). */
    fun isPast(nowEpochMs: Long = System.currentTimeMillis()): Boolean = hasEnded(nowEpochMs)

    /**
     * Server-computed section (`future` / `recent` / `past`), falling back to a
     * 7-day date cutoff when absent (offline cache or older response).
     */
    fun resolvedSection(nowEpochMs: Long = System.currentTimeMillis()): FlightSection {
        FlightSection.fromString(section)?.let { return it }
        val dep = departureEpochMs ?: return FlightSection.FUTURE
        if (!hasEnded(nowEpochMs)) return FlightSection.FUTURE
        val sevenDaysMs = 7L * 24L * 3_600_000L
        return if (dep >= nowEpochMs - sevenDaysMs) {
            FlightSection.RECENT
        } else {
            FlightSection.PAST
        }
    }

    /**
     * True once the live observation window (`departure - 3h .. departure + duration + 1h`) has ended.
     * [started]: the flight is being tracked and has not landed (#874); the end then moves to the
     * latest take-off the server accepts ([MAX_TAKEOFF_DELAY_HOURS]) + duration + 1h, as the
     * server's own window moves with the take-off.
     */
    fun hasLiveObservationWindowEnded(
        nowEpochMs: Long = System.currentTimeMillis(),
        started: Boolean = false,
    ): Boolean {
        val dep = departureEpochMs ?: return false
        val delayHours = if (started) MAX_TAKEOFF_DELAY_HOURS else 0.0
        val windowEndMs = dep + ((delayHours + flightDurationHours + 1.0) * 3_600_000.0).toLong()
        return nowEpochMs > windowEndMs
    }

    /**
     * True when [nowEpochMs] is within the live observation window:
     * `departure - 3h <= now <= departure + duration + 1h` (later when [started], see
     * [hasLiveObservationWindowEnded]).
     */
    fun isInLiveObservationWindow(
        nowEpochMs: Long = System.currentTimeMillis(),
        started: Boolean = false,
    ): Boolean {
        val dep = departureEpochMs ?: return false
        val windowStartMs = dep - 3L * 3_600_000L
        return nowEpochMs >= windowStartMs && !hasLiveObservationWindowEnded(nowEpochMs, started)
    }

    /**
     * True when [nowEpochMs] is within the tracking / PIREP window:
     * `departure - 2h <= now <= departure + duration + 2h`.
     * Mirrors `PIREP_FLIGHT_WINDOW_HOURS` in `api/pireps.py`. [started] moves the end to
     * [trackingWindowEndEpochMs] with `started = true` (#874).
     */
    fun isInTrackingWindow(
        nowEpochMs: Long = System.currentTimeMillis(),
        started: Boolean = false,
    ): Boolean {
        val dep = departureEpochMs ?: return false
        val windowEndMs = trackingWindowEndEpochMs(started) ?: return false
        val windowStartMs = dep - 2L * 3_600_000L
        return nowEpochMs in windowStartMs..windowEndMs
    }

    /**
     * When the tracking window closes: `departure + duration + 2h`, or for a started flight the
     * latest take-off the server accepts ([MAX_TAKEOFF_DELAY_HOURS]) + duration + 2h (#874), so a
     * leg taking off after its planned arrival keeps tracking until it lands. The tracking
     * service's auto-stop. Mirrors iOS `FlightResponse.trackingWindowEnd(started:)`.
     */
    fun trackingWindowEndEpochMs(started: Boolean = false): Long? {
        val dep = departureEpochMs ?: return null
        val delayHours = if (started) MAX_TAKEOFF_DELAY_HOURS else 0.0
        return dep + ((delayHours + flightDurationHours + 2.0) * 3_600_000.0).toLong()
    }

    /**
     * True once the server would treat a refresh of this flight as historical
     * (`now > departure + min(duration + 3h, 12h)`).
     * Mirrors `_classify_refresh_time` in `api/packs.py` (`_INFLIGHT_GRACE = 3h`, `_MAX_INFLIGHT_WINDOW = 12h`).
     */
    fun isHistoricalForRefresh(nowEpochMs: Long = System.currentTimeMillis()): Boolean {
        val dep = departureEpochMs ?: return false
        val windowHours = min(flightDurationHours + 3.0, 12.0)
        val liveEndMs = dep + (windowHours * 3_600_000.0).toLong()
        return nowEpochMs > liveEndMs
    }

    data class FlightGroup(
        val section: FlightSection,
        val title: String,
        val flights: List<FlightResponse>,
    )

    companion object {
        /**
         * How long after the planned departure the server still accepts a take-off
         * (`MAX_TAKEOFF_DELAY_H` in `tasks/live_tick.py`, #760). Bounds how far a started
         * flight's windows stretch past the plan (#874). Keep in step with the server.
         */
        const val MAX_TAKEOFF_DELAY_HOURS: Double = 6.0

        /**
         * Buckets flights into `Future`, `Recent`, and `Past` sections (dropping empty groups),
         * honoring [order] for `Future` while keeping `Recent` and `Past` most-recent-first.
         * Mirrors `FlightListView.groupedFlights` on iOS.
         */
        fun groupFlights(
            flights: List<FlightResponse>,
            order: FlightOrder = FlightOrder.FURTHEST_FIRST,
            nowEpochMs: Long = System.currentTimeMillis(),
        ): List<FlightGroup> {
            val future = mutableListOf<FlightResponse>()
            val recent = mutableListOf<FlightResponse>()
            val past = mutableListOf<FlightResponse>()

            for (flight in flights) {
                when (flight.resolvedSection(nowEpochMs)) {
                    FlightSection.FUTURE -> future.add(flight)
                    FlightSection.RECENT -> recent.add(flight)
                    FlightSection.PAST -> past.add(flight)
                }
            }

            val futureComparator = Comparator<FlightResponse> { a, b ->
                val depA = a.departureEpochMs ?: Long.MIN_VALUE
                val depB = b.departureEpochMs ?: Long.MIN_VALUE
                when (order) {
                    FlightOrder.SOONEST_FIRST -> depA.compareTo(depB)
                    FlightOrder.FURTHEST_FIRST -> depB.compareTo(depA)
                }
            }
            val recentOrPastComparator = Comparator<FlightResponse> { a, b ->
                val depA = a.departureEpochMs ?: Long.MIN_VALUE
                val depB = b.departureEpochMs ?: Long.MIN_VALUE
                depB.compareTo(depA)
            }

            future.sortWith(futureComparator)
            recent.sortWith(recentOrPastComparator)
            past.sortWith(recentOrPastComparator)

            return buildList {
                if (future.isNotEmpty()) add(FlightGroup(FlightSection.FUTURE, FlightSection.FUTURE.title, future))
                if (recent.isNotEmpty()) add(FlightGroup(FlightSection.RECENT, FlightSection.RECENT.title, recent))
                if (past.isNotEmpty()) add(FlightGroup(FlightSection.PAST, FlightSection.PAST.title, past))
            }
        }
    }
}

typealias FlightGroup = FlightResponse.FlightGroup

fun groupFlights(
    flights: List<FlightResponse>,
    order: FlightOrder = FlightOrder.FURTHEST_FIRST,
    nowEpochMs: Long = System.currentTimeMillis(),
): List<FlightGroup> = FlightResponse.groupFlights(flights, order, nowEpochMs)

/**
 * Body of `POST /api/flights` (server `CreateFlightRequest`, `api/flights.py`).
 *
 * Every key here must be a server field: the server ignores unknown keys, so a
 * misnamed one is dropped without an error (that is how Flexibility used to be
 * lost on create). `tests/test_android_api_contract.py` checks this.
 */
@Serializable
data class CreateFlightRequest(
    val waypoints: List<String>,
    @SerialName("route_name")
    val routeName: String? = null,
    /** The Field-15 text the pilot typed, stored verbatim beside [waypoints]. */
    @SerialName("raw_route")
    val rawRoute: String? = null,
    val description: String? = null,
    @SerialName("departure_time")
    val departureTime: String,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int? = null,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int? = null,
    @SerialName("flight_duration_hours")
    val flightDurationHours: Double? = null,
    @SerialName("profile_id")
    val profileId: Int? = null,
    @SerialName("aircraft_id")
    val aircraftId: Int? = null,
    /** `ALTERNATE` is rejected on create: it needs an alt time, which only PATCH sets. */
    val flexibility: FlightFlexibility? = null,
    /** Opt-in trip membership. A duplicate does not inherit the source's trip. */
    @SerialName("trip_id")
    val tripId: String? = null,
)

/**
 * Body of `PATCH /api/flights/{id}` (server `UpdateFlightRequest`). A null field is
 * omitted on the wire (`explicitNulls = false`), which the server reads as "unchanged".
 *
 * The date and the first/last waypoint are part of the flight's identity: PATCH refuses
 * them with a 422, and the client offers Move or Duplicate instead ([FlightEditRules]).
 * Trip membership is not a PATCH field; it goes through `/api/trips/{id}/legs`.
 */
@Serializable
data class UpdateFlightRequest(
    val waypoints: List<String>? = null,
    /** Sent only when the pilot edited the route; omitting it on a route change clears it. */
    @SerialName("raw_route")
    val rawRoute: String? = null,
    val description: String? = null,
    @SerialName("departure_time")
    val departureTime: String? = null,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int? = null,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int? = null,
    @SerialName("flight_duration_hours")
    val flightDurationHours: Double? = null,
    @SerialName("profile_id")
    val profileId: Int? = null,
    @SerialName("aircraft_id")
    val aircraftId: Int? = null,
    /** ISO 8601, or `""` to clear a stored alternate. Must be on the departure's UTC day. */
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    val flexibility: FlightFlexibility? = null,
    @SerialName("notify_override")
    val notifyOverride: String? = null,
)

@Serializable
enum class FlightInvalidation(val wireValue: String) {
    @SerialName("none")
    NONE("none"),

    @SerialName("advisories_only")
    ADVISORIES_ONLY("advisories_only"),

    @SerialName("refetch_needed")
    REFETCH_NEEDED("refetch_needed");

    companion object {
        fun fromString(s: String?): FlightInvalidation = when (s?.trim()?.lowercase()) {
            "advisories_only" -> ADVISORIES_ONLY
            "refetch_needed" -> REFETCH_NEEDED
            else -> NONE
        }
    }
}

/**
 * Response from `PUT /api/flights/{id}`.
 * On the wire, the server returns all [FlightResponse] fields at the top level alongside `"invalidation"`.
 */
@Serializable(with = UpdateFlightResponseSerializer::class)
data class UpdateFlightResponse(
    val flight: FlightResponse,
    val invalidation: FlightInvalidation = FlightInvalidation.NONE,
)

object UpdateFlightResponseSerializer : KSerializer<UpdateFlightResponse> {
    override val descriptor: SerialDescriptor =
        buildClassSerialDescriptor("aero.flyfun.weather.model.UpdateFlightResponse")

    override fun deserialize(decoder: Decoder): UpdateFlightResponse {
        val jsonDecoder = decoder as? JsonDecoder
            ?: error("UpdateFlightResponseSerializer only supports JSON")
        val obj = jsonDecoder.decodeJsonElement().jsonObject
        val invalidationStr = obj["invalidation"]?.jsonPrimitive?.content
        val invalidation = FlightInvalidation.fromString(invalidationStr)
        val flightElement = obj["flight"] ?: obj
        val flight = jsonDecoder.json.decodeFromJsonElement(FlightResponse.serializer(), flightElement)
        return UpdateFlightResponse(flight = flight, invalidation = invalidation)
    }

    override fun serialize(encoder: Encoder, value: UpdateFlightResponse) {
        val jsonEncoder = encoder as? JsonEncoder
            ?: error("UpdateFlightResponseSerializer only supports JSON")
        val flightObj = jsonEncoder.json.encodeToJsonElement(FlightResponse.serializer(), value.flight).jsonObject
        val combined = LinkedHashMap<String, kotlinx.serialization.json.JsonElement>(flightObj)
        combined["invalidation"] = JsonPrimitive(value.invalidation.wireValue)
        jsonEncoder.encodeJsonElement(JsonObject(combined))
    }
}

/**
 * Body of `POST /api/flights/{id}/move` (server `MoveFlightRequest`): replaces the flight
 * with one built from these values and **discards all its briefing packs**. Null fields
 * inherit from the source flight. There is no aircraft, profile or Flexibility field, so
 * the client sends those as a follow-up PATCH on the moved flight.
 */
@Serializable
data class MoveFlightRequest(
    @SerialName("departure_time")
    val departureTime: String? = null,
    val waypoints: List<String>? = null,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int? = null,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int? = null,
    @SerialName("flight_duration_hours")
    val flightDurationHours: Double? = null,
    @SerialName("raw_route")
    val rawRoute: String? = null,
    val description: String? = null,
    /** Trip membership survives a move unless the pilot unticks it. */
    @SerialName("keep_in_trip")
    val keepInTrip: Boolean = true,
)

/** `POST /api/flights/bulk-delete` body: the server field is `ids` (at most 200). */
@Serializable
data class BulkDeleteRequest(
    @SerialName("ids")
    val flightIds: List<String>,
)

@Serializable
data class BulkDeletePartialFailure(
    @SerialName("flight_id")
    val flightId: String = "",
    val reason: String = "",
)

@Serializable
data class BulkDeleteResponse(
    val deleted: List<String> = emptyList(),
    @SerialName("not_found")
    val notFound: List<String> = emptyList(),
    @SerialName("not_owned")
    val notOwned: List<String> = emptyList(),
    val partial: List<BulkDeletePartialFailure> = emptyList(),
)

@Serializable
data class ParseFplRequest(
    @SerialName("fpl_text")
    val fplText: String,
)

/** `POST /api/flights/parse-fpl` (server `ParseFplResponse`). */
@Serializable
data class ParseFplResponse(
    val waypoints: List<String> = emptyList(),
    /** Departure date, `YYYY-MM-DD`. */
    val date: String? = null,
    /** Departure time, `HH:MM` UTC. */
    @SerialName("time_utc")
    val timeUtc: String? = null,
    @SerialName("altitude_ft")
    val altitudeFt: Int? = null,
    @SerialName("duration_hours")
    val durationHours: Double? = null,
    @SerialName("flight_rules")
    val flightRules: String? = null,
    @SerialName("aircraft_type")
    val aircraftType: String? = null,
    /** The plan's Field-15 route, kept as the flight's `raw_route`. */
    @SerialName("raw_route")
    val rawRoute: String? = null,
    val error: String? = null,
) {
    /** Departure as ISO 8601 UTC, when the plan carried both a date and a time. */
    val departureTime: String?
        get() {
            val d = date?.takeIf { it.isNotBlank() } ?: return null
            val t = timeUtc?.takeIf { it.isNotBlank() } ?: return null
            return "${d}T$t:00Z"
        }
}

/** `POST /api/flights/interpret-route` body (server `InterpretRouteRequest`). */
@Serializable
data class InterpretRouteRequest(
    @SerialName("raw_route")
    val rawRoute: String,
)

/**
 * `POST /api/flights/interpret-route` (server `InterpretRouteResponse`): the Field-15
 * text resolved into the waypoint list a flight stores.
 */
@Serializable
data class InterpretRouteResponse(
    @SerialName("original_tokens")
    val originalTokens: List<String> = emptyList(),
    /** The waypoints that resolved, in route order: what the flight stores. */
    val interpreted: List<String> = emptyList(),
    /** Tokens that could not be placed (typos, unknown idents). */
    val skipped: List<String> = emptyList(),
    /** Tokens recognised but dropped as too far off the direct leg. */
    @SerialName("off_route")
    val offRoute: List<String> = emptyList(),
    val waypoints: List<Waypoint> = emptyList(),
)

@Serializable
data class RouteDistanceRequest(
    val waypoints: List<String>,
)

@Serializable
data class RouteDistanceResponse(
    val waypoints: List<Waypoint> = emptyList(),
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double = 0.0,
)

@Serializable
data class AutorouterRoute(
    val fpl: String = "",
    val departure: String = "",
    val destination: String = "",
    val waypoints: List<String> = emptyList(),
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    @SerialName("fuel_penalty")
    val fuelPenalty: Double? = null,
    @SerialName("flight_time_minutes")
    val flightTimeMinutes: Int? = null,
    @SerialName("minimum_level")
    val minimumLevel: Int? = null,
    @SerialName("maximum_level")
    val maximumLevel: Int? = null,
    val summary: String? = null,
) {
    val routeText: String
        get() = fpl.ifBlank { summary.orEmpty() }

    val durationHours: Double?
        get() = flightTimeMinutes?.let { it / 60.0 }
}

typealias AutorouterRouteOption = AutorouterRoute

@Serializable
data class AutorouterRoutesResponse(
    val routes: List<AutorouterRoute> = emptyList(),
)

@Serializable
data class AutorouterLinkTicketRequest(
    @SerialName("code_challenge")
    val codeChallenge: String,
    val platform: String = "android",
)

@Serializable
data class AutorouterLinkCompleteRequest(
    val ticket: String,
    @SerialName("code_verifier")
    val codeVerifier: String,
)

@Serializable
data class AutorouterLinkTicketResponse(
    @SerialName("authorize_url")
    val authorizeUrl: String = "",
    val ticket: String = "",
)

@Serializable
data class SharedFlightPreview(
    val code: String = "",
    val flight: FlightResponse,
    @SerialName("shared_by")
    val sharedBy: String? = null,
)

@Serializable
data class SharedFlightCodeResponse(
    val code: String = "",
    @SerialName("share_url")
    val shareUrl: String = "",
)
