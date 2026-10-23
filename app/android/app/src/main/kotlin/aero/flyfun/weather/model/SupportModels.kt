package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// ============================================================================
// Aircraft
// ============================================================================

@Serializable
data class AircraftResponse(
    val id: Int = 0,
    @SerialName("icao_type")
    val icaoType: String = "",
    @SerialName("type_name")
    val typeName: String = "",
    @SerialName("manufacturer")
    val manufacturer: String? = null,
    @SerialName("model_name")
    val modelName: String? = null,
    val category: String? = null,
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
    @SerialName("is_default")
    val isDefault: Boolean = false,
    @SerialName("created_at")
    val createdAt: String? = null,
    /** The server's synthesised "My aircraft" (`ZZZZ`, #777); cleared by any PUT. */
    @SerialName("is_placeholder")
    val isPlaceholder: Boolean = false,
) {
    val displayLabel: String
        get() = when {
            !nickname.isNullOrEmpty() -> nickname
            !tailNumber.isNullOrEmpty() -> "$tailNumber ($typeName)"
            else -> typeName
        }
}

@Serializable
data class AircraftTypeResponse(
    val icao: String = "",
    val manufacturer: String = "",
    val model: String = "",
    val category: String? = null,
    @SerialName("cruise_speed_kt")
    val cruiseSpeedKt: Int? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
) {
    val id: String
        get() = icao

    val defaultSpeedKt: Int?
        get() = cruiseSpeedKt

    val defaultCeilingFt: Int?
        get() = ceilingFt
}

typealias AircraftTypeResult = AircraftTypeResponse

@Serializable
data class CreateAircraftRequest(
    @SerialName("icao_type")
    val icaoType: String,
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
    @SerialName("is_default")
    val isDefault: Boolean = false,
)

@Serializable
data class UpdateAircraftRequest(
    @SerialName("icao_type")
    val icaoType: String? = null,
    @SerialName("tail_number")
    val tailNumber: String? = null,
    val nickname: String? = null,
    @SerialName("cruise_speed_kt")
    val cruiseSpeedKt: Int? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
    @SerialName("is_ifr")
    val isIfr: Boolean? = null,
    @SerialName("is_fiki")
    val isFiki: Boolean? = null,
    @SerialName("is_default")
    val isDefault: Boolean? = null,
)

// ============================================================================
// Profiles & Usage
// ============================================================================

@Serializable
data class ProfileResponse(
    val id: Int = 0,
    val name: String = "",
    @SerialName("is_default")
    val isDefault: Boolean = false,
    @SerialName("flight_count")
    val flightCount: Int = 0,
    val settings: ProfileSettings = ProfileSettings(),
    @SerialName("system_template_key")
    val systemTemplateKey: String? = null,
    @SerialName("created_at")
    val createdAt: String? = null,
    @SerialName("updated_at")
    val updatedAt: String? = null,
)

typealias FlightProfileResponse = ProfileResponse

@Serializable
data class ProfileSettings(
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int? = null,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int? = null,
    @SerialName("speed_kt")
    val speedKt: Int? = null,
    val models: List<String>? = null,
    @SerialName("advisory_models")
    val advisoryModels: List<String>? = null,
    @SerialName("gramet_enabled")
    val grametEnabled: Boolean? = null,
    @SerialName("llm_digest_enabled")
    val llmDigestEnabled: Boolean? = null,
    @SerialName("icing_severity_enhance")
    val icingSeverityEnhance: Boolean? = null,
    @SerialName("icing_method")
    val icingMethod: String? = null,
    @SerialName("cloud_method")
    val cloudMethod: String? = null,
    @SerialName("convective_method")
    val convectiveMethod: String? = null,
    @SerialName("digest_guidance")
    val digestGuidance: String? = null,
    val advisories: ProfileAdvisoriesConfig? = null,
    /** "vfr_only" or "vfr_ifr". */
    @SerialName("flight_rules")
    val flightRules: String? = null,
)

@Serializable
data class ProfileAdvisoriesConfig(
    val enabled: Map<String, Boolean>? = null,
    val params: Map<String, Map<String, Double>>? = null,
    val aggregation: String? = null,
)

@Serializable
data class UsageSummaryResponse(
    val month: PeriodUsage = PeriodUsage(),
    val total: FeatureCounts = FeatureCounts(),
    val models: Map<String, ModelUsage> = emptyMap(),
)

@Serializable
data class PeriodUsage(
    val briefings: Int = 0,
    val gramet: Int = 0,
    @SerialName("llm_digest")
    val llmDigest: Int = 0,
    @SerialName("total_cost_usd")
    val totalCostUsd: Double = 0.0,
)

@Serializable
data class FeatureCounts(
    val briefings: Int = 0,
    val gramet: Int = 0,
    @SerialName("llm_digest")
    val llmDigest: Int = 0,
)

@Serializable
data class ModelUsage(
    val calls: Int = 0,
    @SerialName("total_mb")
    val totalMb: Double = 0.0,
)

// ============================================================================
// Debrief
// ============================================================================

@Serializable
data class DebriefResponse(
    @SerialName("flight_id")
    val flightId: String = "",
    val outcomes: Map<String, String> = emptyMap(),
    @SerialName("other_text")
    val otherText: String = "",
    @SerialName("rating_overall")
    val ratingOverall: Int? = null,
    @SerialName("pack_timestamp")
    val packTimestamp: String? = null,
    @SerialName("created_at")
    val createdAt: String = "",
    @SerialName("updated_at")
    val updatedAt: String = "",
    val taxonomy: DebriefTaxonomy = DebriefTaxonomy(),
)

@Serializable
data class DebriefRequest(
    val outcomes: Map<String, String>,
    @SerialName("other_text")
    val otherText: String = "",
    @SerialName("rating_overall")
    val ratingOverall: Int? = null,
)

@Serializable
data class DebriefTaxonomy(
    val severities: List<TaxonomySeverity> = emptyList(),
    val categories: List<TaxonomyCategory> = emptyList(),
)

@Serializable
data class TaxonomySeverity(
    val id: String = "",
    val label: String = "",
    val hint: String = "",
)

@Serializable
data class TaxonomyCategory(
    val id: String = "",
    val label: String = "",
    val hint: String = "",
    @SerialName("advisory_ids")
    val advisoryIds: List<String> = emptyList(),
)

// ============================================================================
// Help Catalog
// ============================================================================

@Serializable
data class HelpCatalogResponse(
    val version: Int = 1,
    val locale: String = "en",
    val metrics: Map<String, MetricHelp> = emptyMap(),
)

@Serializable
data class MetricHelp(
    val name: String = "",
    val unit: String? = null,
    val category: String? = null,
    val vibe: String = "",
    @SerialName("primary_goal")
    val primaryGoal: String = "",
    @SerialName("best_used_for")
    val bestUsedFor: String = "",
    val limitations: String = "",
    val theory: String = "",
    val wikipedia: String? = null,
    val short: String = "",
    val summary: String = "",
    val details: String = "",
    val thresholds: List<MetricThreshold>? = null,
    val relevance: String? = null,
    @SerialName("see_also")
    val seeAlso: List<String>? = null,
)

@Serializable
data class MetricThreshold(
    val min: Double? = null,
    val max: Double? = null,
    val value: String = "",
    val label: String = "",
    val risk: String = "",
    val meaning: String = "",
    val status: String? = null,
)

// ============================================================================
// System Messages ("What's New")
// ============================================================================

@Serializable
data class SystemMessageResponse(
    val id: Int = 0,
    val date: String = "",
    val title: String = "",
    val body: String = "",
    val category: String = "feature",
)

typealias SystemMessage = SystemMessageResponse

@Serializable
data class MessagesStatusResponse(
    @SerialName("unseen_count")
    val unseenCount: Int = 0,
    @SerialName("latest_id")
    val latestId: Int? = null,
)

typealias MessagesStatus = MessagesStatusResponse

// ============================================================================
// User Preferences & Push Notifications
// ============================================================================

@Serializable
enum class FlightOrder(val wireValue: String, val label: String) {
    @SerialName("furthest_first")
    FURTHEST_FIRST("furthest_first", "Furthest first"),

    @SerialName("soonest_first")
    SOONEST_FIRST("soonest_first", "Soonest first");

    val title: String
        get() = label

    val toggled: FlightOrder
        get() = if (this == FURTHEST_FIRST) SOONEST_FIRST else FURTHEST_FIRST

    companion object {
        fun fromString(s: String?): FlightOrder = when (s?.trim()?.lowercase()) {
            "soonest_first" -> SOONEST_FIRST
            else -> FURTHEST_FIRST
        }
    }
}

@Serializable
enum class BriefingUpdates(val wireValue: String, val label: String) {
    @SerialName("in_progress")
    IN_PROGRESS("in_progress", "Every refresh (incl. starting)"),

    @SerialName("daily")
    DAILY("daily", "Completed briefings"),

    @SerialName("alerts_only")
    ALERTS_ONLY("alerts_only", "Advisories worsened only");

    companion object {
        fun fromString(s: String?): BriefingUpdates = when (s?.trim()?.lowercase()) {
            "in_progress" -> IN_PROGRESS
            "alerts_only" -> ALERTS_ONLY
            else -> DAILY
        }
    }
}

@Serializable
data class PreferencesResponse(
    @SerialName("default_profile_id")
    val defaultProfileId: Int? = null,
    @SerialName("gramet_enabled")
    val grametEnabled: Boolean? = null,
    @SerialName("llm_digest_enabled")
    val llmDigestEnabled: Boolean? = null,
    @SerialName("icing_severity_enhance")
    val icingSeverityEnhance: Boolean? = null,
    @SerialName("icing_method")
    val icingMethod: String? = null,
    @SerialName("cloud_method")
    val cloudMethod: String? = null,
    @SerialName("convective_method")
    val convectiveMethod: String? = null,
    @SerialName("digest_guidance")
    val digestGuidance: String? = null,
    @SerialName("auto_refresh_hour")
    val autoRefreshHour: Int? = null,
    val advisories: ProfileAdvisoriesConfig? = null,
    @SerialName("flight_order")
    val flightOrder: FlightOrder = FlightOrder.FURTHEST_FIRST,
    @SerialName("notify_email")
    val notifyEmail: Boolean = false,
    @SerialName("notify_push")
    val notifyPush: Boolean = true,
    @SerialName("briefing_updates")
    val briefingUpdates: BriefingUpdates = BriefingUpdates.DAILY,
    @SerialName("live_updates")
    val liveUpdates: Boolean = true,
    // Never sent by `/api/user/preferences`: first-run setup reads `setup_completed` from
    // `/auth/me` ([AuthMeResponse], #900). Kept so the cached shape is unchanged.
    @SerialName("setup_completed")
    val setupCompleted: Boolean = true,
    @SerialName("has_autorouter_creds")
    val hasAutorouter: Boolean = false,
    @SerialName("autorouter_mode")
    val autorouterMode: String? = null,
) {
    val autorouterConnected: Boolean
        get() = hasAutorouter
}

typealias UserPreferencesResponse = PreferencesResponse

@Serializable
data class DeviceRegistrationRequest(
    val token: String,
    val environment: String = "production",
    val platform: String = "android",
)

@Serializable
data class BadgeStatus(
    /** Flights with a briefing update the user has not opened yet (server-derived). */
    val count: Int = 0,
)

// ============================================================================
// Feedback
// ============================================================================

/** The feedback form's categories: wire values are the server's `ALLOWED_CATEGORIES`, labels mirror iOS. */
enum class FeedbackCategory(val wireValue: String, val label: String) {
    DATA_ISSUE("data_issue", "Briefing Data Issue"),
    TOO_CONSERVATIVE("too_conservative", "Briefing Too Conservative"),
    TOO_OPTIMISTIC("too_optimistic", "Briefing Too Optimistic"),
    INCORRECT_INTERPRETATION("incorrect_interpretation", "Briefing Incorrect Interpretation"),
    OTHER("other", "Other Bug/Issue"),
}

/**
 * `POST /api/feedback` body (server `FeedbackRequest`). One endpoint takes the form, the digest
 * thumb and the live-highlight thumb; build them with the companion factories.
 */
@Serializable
data class FeedbackRequest(
    @SerialName("flight_id")
    val flightId: String = "",
    @SerialName("pack_timestamp")
    val packTimestamp: String = "",
    val category: String,
    val comment: String = "",
    val sentiment: String? = null,
    val target: String? = null,
    @SerialName("contact_ok")
    val contactOk: Boolean = true,
    val context: HighlightFeedbackContext? = null,
) {
    companion object {
        fun general(category: FeedbackCategory, comment: String, flightId: String = "", packTimestamp: String = "") =
            FeedbackRequest(
                flightId = flightId,
                packTimestamp = packTimestamp,
                category = category.wireValue,
                comment = comment,
                target = "general",
            )

        fun digestRating(flightId: String, packTimestamp: String, thumbsUp: Boolean, comment: String = "") =
            FeedbackRequest(
                flightId = flightId,
                packTimestamp = packTimestamp,
                category = "digest_rating",
                comment = comment,
                sentiment = if (thumbsUp) "up" else "down",
                target = "digest",
            )

        /**
         * A live-highlight thumb. The server keys it to the rated line, so it needs all four context
         * fields; returns null for a highlight that lacks one (nothing to rate).
         */
        fun highlightRating(
            flightId: String,
            packTimestamp: String,
            highlight: LiveHighlight,
            thumbsUp: Boolean,
        ): FeedbackRequest? {
            if (!isRatable(highlight)) return null
            return FeedbackRequest(
                flightId = flightId,
                packTimestamp = packTimestamp,
                category = "highlight_rating",
                sentiment = if (thumbsUp) "up" else "down",
                target = "live_highlight",
                context = HighlightFeedbackContext(highlight.factsHash!!, highlight.generatedAt!!, highlight.model!!, highlight.text),
            )
        }

        /** Whether [highlightRating] can build a request for `highlight`: the UI hides the thumbs otherwise. */
        fun isRatable(highlight: LiveHighlight): Boolean =
            !highlight.factsHash.isNullOrBlank() && !highlight.generatedAt.isNullOrBlank() &&
                !highlight.model.isNullOrBlank() && highlight.text.isNotBlank()
    }
}

@Serializable
data class HighlightFeedbackContext(
    @SerialName("facts_hash")
    val factsHash: String,
    @SerialName("generated_at")
    val generatedAt: String,
    val model: String,
    val text: String,
)

// ============================================================================
// PIREPs
// ============================================================================

@Serializable
data class PirepWaypointRef(
    val icao: String = "",
    val name: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    val cardinal: String? = null,
)

@Serializable
data class PirepResponse(
    val id: Int = 0,
    @SerialName("client_uuid")
    val clientUuid: String? = null,
    @SerialName("flight_id")
    val flightId: String? = null,
    @SerialName("pack_id")
    val packId: Int? = null,
    @SerialName("observed_at")
    val observedAt: String = "",
    val latitude: Double = 0.0,
    val longitude: Double = 0.0,
    @SerialName("gps_altitude_ft")
    val gpsAltitudeFt: Int? = null,
    @SerialName("reported_altitude_ft")
    val reportedAltitudeFt: Int? = null,
    @SerialName("in_cloud")
    val inCloud: Boolean? = null,
    @SerialName("icing_intensity")
    val icingIntensity: String? = null,
    @SerialName("icing_type")
    val icingType: String? = null,
    @SerialName("turbulence_intensity")
    val turbulenceIntensity: String? = null,
    @SerialName("cloud_base_ft")
    val cloudBaseFt: Int? = null,
    @SerialName("cloud_top_ft")
    val cloudTopFt: Int? = null,
    @SerialName("flight_category")
    val flightCategory: String? = null,
    @SerialName("wind_dir")
    val windDir: Int? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Int? = null,
    @SerialName("surface_temp_c")
    val surfaceTempC: Double? = null,
    val remarks: String? = null,
    @SerialName("aircraft_type")
    val aircraftType: String? = null,
    val source: String = "manual",
    @SerialName("is_own")
    val isOwn: Boolean = false,
    @SerialName("nearest_waypoint")
    val nearestWaypoint: PirepWaypointRef? = null,
)

@Serializable
data class PirepListResponse(
    val items: List<PirepResponse> = emptyList(),
    val count: Int = 0,
)

@Serializable
data class SubmitPirepRequest(
    @SerialName("client_uuid")
    val clientUuid: String,
    @SerialName("flight_id")
    val flightId: String? = null,
    @SerialName("observed_at")
    val observedAt: String,
    val latitude: Double,
    val longitude: Double,
    @SerialName("gps_altitude_ft")
    val gpsAltitudeFt: Int? = null,
    @SerialName("reported_altitude_ft")
    val reportedAltitudeFt: Int? = null,
    @SerialName("in_cloud")
    val inCloud: Boolean? = null,
    @SerialName("icing_intensity")
    val icingIntensity: String? = null,
    @SerialName("icing_type")
    val icingType: String? = null,
    @SerialName("turbulence_intensity")
    val turbulenceIntensity: String? = null,
    @SerialName("cloud_base_ft")
    val cloudBaseFt: Int? = null,
    @SerialName("cloud_top_ft")
    val cloudTopFt: Int? = null,
    @SerialName("flight_category")
    val flightCategory: String? = null,
    @SerialName("wind_dir")
    val windDir: Int? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Int? = null,
    @SerialName("surface_temp_c")
    val surfaceTempC: Double? = null,
    val remarks: String? = null,
    @SerialName("aircraft_type")
    val aircraftType: String? = null,
    val source: String = "manual",
)

// ============================================================================
// First-run setup (#900)
// ============================================================================

/**
 * The one `/auth/me` field the app reads. `/api/user/preferences` does not carry `setup_completed`;
 * `/auth/me` does (false when the user has no preferences row yet), as the web reads it. A missing
 * field means "not done".
 */
@Serializable
data class AuthMeResponse(
    @SerialName("setup_completed")
    val setupCompleted: Boolean? = null,
)

/** A system profile template (`GET /api/user/profiles/system-templates`): the profile card's description. */
@Serializable
data class SystemTemplate(
    val key: String = "",
    val name: String = "",
    val description: String = "",
)

/**
 * Body for `PUT /api/user/profiles/{id}`. The server merges `settings` sparsely: an omitted key is
 * kept, so the template's models and advisory choices survive. Null fields are left out of the wire
 * (`explicitNulls = false`), never sent as null (a null deletes the key).
 */
@Serializable
data class SetupProfileUpdate(
    @SerialName("is_default")
    val isDefault: Boolean? = null,
    val settings: SetupMission? = null,
)

/** The wizard's "Your usual mission" group, saved to the chosen profile. */
@Serializable
data class SetupMission(
    @SerialName("flight_rules")
    val flightRules: String,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int,
)

/**
 * The profile says FIKI: the IFR FIKI template, or the retired FIKI Icing card on. Mirrors the
 * server's `profile_declares_fiki` (#850).
 */
val ProfileResponse.declaresFiki: Boolean
    get() = systemTemplateKey == "ifr_fiki" || settings.advisories?.enabled?.get("fiki_icing") == true
