package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.ZoneOffset

@Serializable
enum class TripRole(val wireValue: String) {
    @SerialName("owner")
    OWNER("owner"),

    @SerialName("subscriber")
    SUBSCRIBER("subscriber"),

    @SerialName("shared")
    SHARED("shared"),
}

@Serializable
enum class TripBindingBasis(val wireValue: String) {
    @SerialName("all_models")
    ALL_MODELS("all_models"),

    @SerialName("longest_range_only")
    LONGEST_RANGE_ONLY("longest_range_only"),

    @SerialName("out_of_range")
    OUT_OF_RANGE("out_of_range"),
}

@Serializable
enum class TripGradeKind(val wireValue: String) {
    @SerialName("none")
    NONE("none"),

    @SerialName("single")
    SINGLE("single"),

    @SerialName("split")
    SPLIT("split"),
}

@Serializable
enum class TripLegState(val wireValue: String) {
    @SerialName("complete")
    COMPLETE("complete"),

    @SerialName("ecmwf_only")
    ECMWF_ONLY("ecmwf_only"),

    @SerialName("gfs_only")
    GFS_ONLY("gfs_only"),

    @SerialName("beyond_horizon")
    BEYOND_HORIZON("beyond_horizon"),

    @SerialName("missing")
    MISSING("missing");

    val shortBadgeLabel: String?
        get() = when (this) {
            COMPLETE -> null
            ECMWF_ONLY -> "ECMWF only"
            GFS_ONLY -> "GFS only"
            BEYOND_HORIZON -> "Too far out"
            MISSING -> "No briefing"
        }

    companion object {
        fun fromString(s: String?): TripLegState {
            if (s.isNullOrBlank()) return MISSING
            val lower = s.trim().lowercase()
            return entries.firstOrNull { it.wireValue == lower } ?: MISSING
        }
    }
}

@Serializable
data class TripSummaryResponse(
    val id: String,
    val title: String = "",
    @SerialName("name")
    val rawName: String = "",
    @SerialName("display_name")
    val displayName: String? = null,
    @SerialName("flight_ids")
    val flightIds: List<String> = emptyList(),
    @SerialName("leg_count")
    val legCount: Int = 0,
    @SerialName("first_departure_time")
    val firstDepartureTime: String? = null,
    @SerialName("last_departure_time")
    val lastDepartureTime: String? = null,
    @SerialName("origin_icao")
    val originIcao: String? = null,
    @SerialName("destination_icao")
    val destinationIcao: String? = null,
    @SerialName("overall_assessment")
    val overallAssessment: String? = null,
    @SerialName("ecmwf_assessment")
    val ecmwfAssessment: String? = null,
    @SerialName("gfs_assessment")
    val gfsAssessment: String? = null,
    @SerialName("grade_kind")
    val gradeKind: TripGradeKind? = null,
    @SerialName("complete_count")
    val completeCount: Int? = null,
    @SerialName("ecmwf_only_count")
    val ecmwfOnlyCount: Int? = null,
    @SerialName("gfs_only_count")
    val gfsOnlyCount: Int? = null,
    @SerialName("beyond_horizon_count")
    val beyondHorizonCount: Int? = null,
    @SerialName("missing_count")
    val missingCount: Int? = null,
    @SerialName("stale_leg_count")
    val staleLegCount: Int? = null,
    @SerialName("missing_leg_count")
    val missingLegCount: Int? = null,
    @SerialName("continuity_warning_count")
    val continuityWarningCount: Int? = null,
    val role: TripRole? = null,
    @SerialName("owner_display_name")
    val ownerDisplayName: String? = null,
    @SerialName("subscriber_count")
    val subscriberCount: Int? = null,
    @SerialName("share_code")
    val shareCode: String? = null,
    @SerialName("created_at")
    val createdAt: String? = null,
) {
    val resolvedAssessment: Assessment
        get() = Assessment.fromString(overallAssessment)

    val resolvedEcmwfAssessment: Assessment
        get() = Assessment.fromString(ecmwfAssessment)

    val resolvedGfsAssessment: Assessment
        get() = Assessment.fromString(gfsAssessment)

    val isEditable: Boolean
        get() = role != TripRole.SUBSCRIBER

    val routeLabel: String?
        get() {
            val orig = originIcao?.takeIf { it.isNotEmpty() } ?: return null
            val dest = destinationIcao?.takeIf { it.isNotEmpty() } ?: return null
            return "${orig.uppercase()} → ${dest.uppercase()}"
        }

    val hasBriefingIssues: Boolean
        get() = (staleLegCount ?: 0) > 0 || (missingLegCount ?: 0) > 0 || (missingCount ?: 0) > 0

    val name: String
        get() = title.ifBlank {
            displayName?.takeIf { it.isNotBlank() } ?: rawName.ifBlank { routeLabel ?: "Trip" }
        }

    val flightCount: Int
        get() = if (legCount > 0) legCount else flightIds.size
}

typealias TripSummary = TripSummaryResponse

@Serializable
data class ContinuityWarning(
    @SerialName("after_leg_index")
    val afterLegIndex: Int = 0,
    val kind: String = "",
    val detail: String = "",
) {
    val id: String
        get() = "$afterLegIndex-$kind"
}

@Serializable
data class TripLegResponse(
    @SerialName("leg_index")
    val legIndex: Int = 1,
    val flight: FlightResponse,
    @SerialName("pack_timestamp")
    val packTimestamp: String? = null,
    val assessment: String? = null,
    @SerialName("assessment_reason")
    val assessmentReason: String? = null,
    @SerialName("is_stale")
    val isStale: Boolean = false,
    @SerialName("has_pack")
    val hasPack: Boolean = false,
    @SerialName("leg_state")
    val legState: String? = null,
    @SerialName("ecmwf_assessment")
    val ecmwfAssessment: String? = null,
    @SerialName("gfs_assessment")
    val gfsAssessment: String? = null,
    @SerialName("models_fetched")
    val modelsFetched: List<String>? = null,
    @SerialName("advisory_chips")
    val advisoryChips: List<AdvisoryChip>? = null,
    @SerialName("airport_conditions")
    val airportConditions: AirportConditions? = null,
    @SerialName("watch_items")
    val watchItems: List<String>? = null,
    @SerialName("continuity_warning")
    val continuityWarning: String? = null,
) {
    val id: String
        get() = flight.id

    val shortTitle: String
        get() = flight.shortTitle

    val resolvedAssessment: Assessment
        get() = Assessment.fromString(assessment ?: flight.briefingStatus?.assessment)

    val resolvedEcmwfAssessment: Assessment
        get() = Assessment.fromString(ecmwfAssessment)

    val resolvedGfsAssessment: Assessment
        get() = Assessment.fromString(gfsAssessment)

    val resolvedLegState: TripLegState
        get() {
            if (!legState.isNullOrBlank()) {
                return TripLegState.fromString(legState)
            }
            return if (hasPack) TripLegState.COMPLETE else TripLegState.MISSING
        }
}

typealias TripLeg = TripLegResponse

@Serializable
data class ServerTripLeg(
    @SerialName("flight_id")
    val flightId: String = "",
    val label: String = "",
    val origin: String? = null,
    val destination: String? = null,
    @SerialName("departure_time")
    val departureTime: String = "",
    @SerialName("duration_hours")
    val durationHours: Double = 0.0,
    val state: String? = null,
    val assessment: String? = null,
    @SerialName("assessment_reason")
    val assessmentReason: String? = null,
    @SerialName("advisory_summary")
    val advisorySummary: AdvisorySummary? = null,
) {
    fun toFlightResponse(): FlightResponse {
        val wps = buildList {
            if (!origin.isNullOrBlank()) add(origin)
            if (!destination.isNullOrBlank()) add(destination)
        }.ifEmpty {
            label.split("→").map { it.trim() }.filter { it.isNotEmpty() }
        }
        return FlightResponse(
            id = flightId,
            routeName = label,
            waypoints = wps,
            departureTime = departureTime,
            flightDurationHours = durationHours,
            briefingStatus = BriefingStatusInfo(assessment = assessment),
            advisorySummary = advisorySummary,
        )
    }
}

@Serializable
data class ServerTripSummary(
    @SerialName("trip_id")
    val tripId: String = "",
    val name: String = "",
    val legs: List<ServerTripLeg> = emptyList(),
    @SerialName("total_legs")
    val totalLegs: Int = 0,
    @SerialName("chain_status")
    val chainStatus: String? = null,
    @SerialName("chain_label")
    val chainLabel: String = "",
    val headline: String = "",
)

@Serializable
data class TripResponse(
    val id: String,
    @SerialName("user_id")
    val userId: String? = null,
    val title: String = "",
    @SerialName("name")
    val rawName: String = "",
    @SerialName("display_name")
    val displayName: String? = null,
    @SerialName("flight_ids")
    val flightIds: List<String> = emptyList(),
    val summary: ServerTripSummary? = null,
    @SerialName("leg_count")
    val legCount: Int = 0,
    @SerialName("first_departure_time")
    val firstDepartureTime: String? = null,
    @SerialName("last_departure_time")
    val lastDepartureTime: String? = null,
    @SerialName("origin_icao")
    val originIcao: String? = null,
    @SerialName("destination_icao")
    val destinationIcao: String? = null,
    @SerialName("overall_assessment")
    val overallAssessment: String? = null,
    @SerialName("binding_leg_index")
    val bindingLegIndex: Int? = null,
    @SerialName("binding_basis")
    val bindingBasis: TripBindingBasis? = null,
    @SerialName("ecmwf_assessment")
    val ecmwfAssessment: String? = null,
    @SerialName("gfs_assessment")
    val gfsAssessment: String? = null,
    @SerialName("grade_kind")
    val gradeKind: TripGradeKind? = null,
    @SerialName("complete_count")
    val completeCount: Int? = null,
    @SerialName("ecmwf_only_count")
    val ecmwfOnlyCount: Int? = null,
    @SerialName("gfs_only_count")
    val gfsOnlyCount: Int? = null,
    @SerialName("beyond_horizon_count")
    val beyondHorizonCount: Int? = null,
    @SerialName("missing_count")
    val missingCount: Int? = null,
    @SerialName("stale_leg_count")
    val staleLegCount: Int = 0,
    @SerialName("missing_leg_count")
    val missingLegCount: Int = 0,
    @SerialName("continuity_warning_count")
    val continuityWarningCount: Int = 0,
    @SerialName("continuity_warnings")
    val continuityWarnings: List<ContinuityWarning> = emptyList(),
    val legs: List<TripLegResponse> = emptyList(),
    val role: String? = null,
    @SerialName("owner_display_name")
    val ownerDisplayName: String? = null,
    @SerialName("subscriber_count")
    val subscriberCount: Int? = null,
    @SerialName("share_code")
    val shareCode: String? = null,
    @SerialName("ai_summary")
    val aiSummary: String? = null,
    @SerialName("ai_summary_generated_at")
    val aiSummaryGeneratedAt: String? = null,
    @SerialName("ai_summary_at")
    val aiSummaryAt: String? = null,
    @SerialName("ai_summary_stale")
    val aiSummaryStale: Boolean? = null,
    @SerialName("created_at")
    val createdAt: String? = null,
) {
    val name: String
        get() = title.ifBlank {
            displayName?.takeIf { it.isNotBlank() }
                ?: summary?.name?.takeIf { it.isNotBlank() }
                ?: rawName.ifBlank { summary?.chainLabel?.takeIf { it.isNotBlank() } ?: routeLabel ?: "Trip" }
        }

    val flights: List<FlightResponse>
        get() = if (legs.isNotEmpty()) {
            legs.map { it.flight }
        } else {
            summary?.legs?.map { it.toFlightResponse() }.orEmpty()
        }

    val resolvedAssessment: Assessment
        get() = Assessment.fromString(overallAssessment ?: summary?.chainStatus)

    val resolvedEcmwfAssessment: Assessment
        get() = Assessment.fromString(ecmwfAssessment)

    val resolvedGfsAssessment: Assessment
        get() = Assessment.fromString(gfsAssessment)

    val isEditable: Boolean
        get() = role != "subscriber" && role != "viewer"

    val routeLabel: String?
        get() {
            val orig = originIcao?.takeIf { it.isNotEmpty() } ?: return null
            val dest = destinationIcao?.takeIf { it.isNotEmpty() } ?: return null
            return "${orig.uppercase()} → ${dest.uppercase()}"
        }
}

@Serializable
data class CreateTripRequest(
    val name: String = "",
    val title: String = "",
    @SerialName("flight_ids")
    val flightIds: List<String> = emptyList(),
    val legs: List<CreateFlightRequest>? = null,
) {
    constructor(
        name: String,
        flightIds: List<String> = emptyList(),
    ) : this(name = name, title = name, flightIds = flightIds, legs = null)
}

@Serializable
data class AddTripLegsRequest(
    @SerialName("flight_ids")
    val flightIds: List<String> = emptyList(),
    val legs: List<CreateFlightRequest>? = null,
)

@Serializable
data class UpdateTripRequest(
    val name: String? = null,
    val title: String? = null,
    @SerialName("flight_ids")
    val flightIds: List<String>? = null,
) {
    constructor(
        name: String?,
    ) : this(name = name, title = name, flightIds = null)
}

@Serializable
data class TripRefreshStatus(
    @SerialName("trip_id")
    val tripId: String = "",
    val active: Boolean = false,
    val queued: List<String> = emptyList(),
    @SerialName("already_active")
    val alreadyActive: List<String> = emptyList(),
    val skipped: List<String> = emptyList(),
    @SerialName("active_flight_ids")
    val activeFlightIds: List<String> = emptyList(),
) {
    val status: String
        get() = if (active) "running" else "completed"

    val completedCount: Int
        get() = skipped.size

    val totalCount: Int
        get() = (queued.size + alreadyActive.size + activeFlightIds.size + skipped.size).coerceAtLeast(1)
}

@Serializable
data class TripAiSummaryResponse(
    @SerialName("trip_id")
    val tripId: String = "",
    val summary: String? = null,
    @SerialName("generated_at")
    val generatedAt: String? = null,
    val stale: Boolean = false,
)

@Serializable
data class TripSubscribeResponse(
    val subscribed: Boolean = false,
    @SerialName("trip_id")
    val tripId: String = "",
    val title: String = "",
    @SerialName("leg_count")
    val legCount: Int = 0,
    @SerialName("owner_display_name")
    val ownerDisplayName: String? = null,
)

@Serializable
data class TripRefreshConflict(
    val flightId: String = "",
    val routeName: String = "",
    val reason: String = "",
)

/**
 * Wrapper around the cached trip list in `BriefingCacheStore` (`trips.json`).
 * Includes `cachedAt` so the client can detect when a calendar-day rollover has
 * shifted `D-N` relative day offsets.
 */
@Serializable
data class TripListCache(
    @SerialName("cached_at")
    val cachedAt: String,
    val trips: List<TripSummaryResponse>,
) {
    /**
     * True when [cachedAt] falls on a different UTC calendar day than [nowEpochMs].
     */
    fun isCalendarStale(nowEpochMs: Long = System.currentTimeMillis()): Boolean {
        val cachedMs = IsoTime.parseEpochMs(cachedAt) ?: return true
        val cachedDay = Instant.ofEpochMilli(cachedMs).atZone(ZoneOffset.UTC).toLocalDate()
        val nowDay = Instant.ofEpochMilli(nowEpochMs).atZone(ZoneOffset.UTC).toLocalDate()
        return cachedDay != nowDay
    }
}
