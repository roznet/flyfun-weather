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
    @SerialName("trip_id")
    val tripId: String = "",
    val title: String = "",
    val role: String? = null,
    @SerialName("leg_index")
    val legIndex: Int = 1,
    @SerialName("leg_count")
    val legCount: Int = 1,
    @SerialName("continuity_warning")
    val continuityWarning: String? = null,
) {
    val badgeText: String
        get() = "$title · Leg $legIndex/$legCount"
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
    @SerialName("briefing_status")
    val briefingStatus: BriefingStatusInfo? = null,
    @SerialName("advisory_summary")
    val advisorySummary: AdvisorySummary? = null,
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    @SerialName("alt_briefing_status")
    val altBriefingStatus: BriefingStatusInfo? = null,
    @SerialName("alt_advisory_summary")
    val altAdvisorySummary: AdvisorySummary? = null,
    @SerialName("flexibility_mode")
    val flexibilityMode: FlexibilityMode = FlexibilityMode.FIXED,
    @SerialName("flexibility_days")
    val flexibilityDays: Int = 1,
    @SerialName("time_window_from_utc")
    val timeWindowFromUtc: Int = 6,
    @SerialName("time_window_to_utc")
    val timeWindowToUtc: Int = 18,
    @SerialName("time_step_hours")
    val timeStepHours: Int = 3,
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
        get() = trip?.tripId

    val tripName: String?
        get() = trip?.title

    val departureWindowEnd: String?
        get() = altDepartureTime

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
     */
    fun hasLiveObservationWindowEnded(nowEpochMs: Long = System.currentTimeMillis()): Boolean {
        val dep = departureEpochMs ?: return false
        val windowEndMs = dep + ((flightDurationHours + 1.0) * 3_600_000.0).toLong()
        return nowEpochMs > windowEndMs
    }

    /**
     * True when [nowEpochMs] is within the live observation window:
     * `departure - 3h <= now <= departure + duration + 1h`.
     */
    fun isInLiveObservationWindow(nowEpochMs: Long = System.currentTimeMillis()): Boolean {
        val dep = departureEpochMs ?: return false
        val windowStartMs = dep - 3L * 3_600_000L
        return nowEpochMs >= windowStartMs && !hasLiveObservationWindowEnded(nowEpochMs)
    }

    /**
     * True when [nowEpochMs] is within the tracking / PIREP window:
     * `departure - 2h <= now <= departure + duration + 2h`.
     * Mirrors `PIREP_FLIGHT_WINDOW_HOURS` in `api/pireps.py`.
     */
    fun isInTrackingWindow(nowEpochMs: Long = System.currentTimeMillis()): Boolean {
        val dep = departureEpochMs ?: return false
        val twoHoursMs = 2L * 3_600_000L
        val durationMs = (flightDurationHours * 3_600_000.0).toLong()
        val windowStartMs = dep - twoHoursMs
        val windowEndMs = dep + durationMs + twoHoursMs
        return nowEpochMs in windowStartMs..windowEndMs
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

@Serializable
data class CreateFlightRequest(
    val waypoints: List<String>,
    @SerialName("route_name")
    val routeName: String? = null,
    @SerialName("departure_time")
    val departureTime: String,
    @SerialName("departure_window_end")
    val departureWindowEnd: String? = null,
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
    @SerialName("trip_id")
    val tripId: String? = null,
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    @SerialName("flexibility_mode")
    val flexibilityMode: FlexibilityMode? = null,
    @SerialName("flexibility_days")
    val flexibilityDays: Int? = null,
    @SerialName("time_window_from_utc")
    val timeWindowFromUtc: Int? = null,
    @SerialName("time_window_to_utc")
    val timeWindowToUtc: Int? = null,
    @SerialName("time_step_hours")
    val timeStepHours: Int? = null,
)

@Serializable
data class UpdateFlightRequest(
    val waypoints: List<String>? = null,
    @SerialName("departure_time")
    val departureTime: String? = null,
    @SerialName("departure_window_end")
    val departureWindowEnd: String? = null,
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
    @SerialName("trip_id")
    val tripId: String? = null,
    val private: Boolean? = null,
    @SerialName("auto_refresh")
    val autoRefresh: Boolean? = null,
    @SerialName("auto_refresh_hour")
    val autoRefreshHour: Int? = null,
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    @SerialName("flexibility_mode")
    val flexibilityMode: FlexibilityMode? = null,
    @SerialName("flexibility_days")
    val flexibilityDays: Int? = null,
    @SerialName("time_window_from_utc")
    val timeWindowFromUtc: Int? = null,
    @SerialName("time_window_to_utc")
    val timeWindowToUtc: Int? = null,
    @SerialName("time_step_hours")
    val timeStepHours: Int? = null,
    @SerialName("notify_override")
    val notifyOverride: FlightNotifyOverride? = null,
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

@Serializable
data class MoveFlightRequest(
    @SerialName("departure_time")
    val departureTime: String = "",
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    @SerialName("trip_id")
    val tripId: String? = null,
)

@Serializable
data class BulkDeleteRequest(
    @SerialName("flight_ids")
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

@Serializable
data class ParseFplResponse(
    val waypoints: List<String> = emptyList(),
    @SerialName("raw_route_tokens")
    val rawRouteTokens: List<String> = emptyList(),
    @SerialName("skipped_tokens")
    val skippedTokens: List<String> = emptyList(),
    @SerialName("altitude_ft")
    val altitudeFt: Int? = null,
    @SerialName("speed_kt")
    val speedKt: Int? = null,
    @SerialName("flight_rules")
    val flightRules: String? = null,
    @SerialName("departure_time_utc")
    val departureTimeUtc: String? = null,
    @SerialName("duration_hours")
    val durationHours: Double? = null,
    @SerialName("aircraft_type")
    val aircraftType: String? = null,
    @SerialName("tail_number")
    val tailNumber: String? = null,
    @SerialName("departure_date")
    val departureDate: String? = null,
) {
    val departureTime: String?
        get() = departureTimeUtc
}

@Serializable
data class RouteWaypointInfo(
    val code: String = "",
    val name: String = "",
    @SerialName("kind_label")
    val kindLabel: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
)

@Serializable
data class InterpretRouteRequest(
    @SerialName("raw_text")
    val rawText: String,
    @SerialName("cruise_speed_kt")
    val cruiseSpeedKt: Double? = null,
)

@Serializable
data class InterpretRouteResponse(
    val waypoints: List<RouteWaypointInfo> = emptyList(),
    @SerialName("skipped_tokens")
    val skippedTokens: List<String> = emptyList(),
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double? = null,
    @SerialName("estimated_duration_hours")
    val estimatedDurationHours: Double? = null,
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
