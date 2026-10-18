package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class LiveLayerResponse(
    @SerialName("flight_id")
    val flightId: String = "",
    @SerialName("pack_timestamp")
    val packTimestamp: String? = null,
    @SerialName("live_updated_at")
    val liveUpdatedAt: String? = null,
    @SerialName("route_observations")
    val routeObservations: RouteObservations? = null,
    @SerialName("route_sigmets")
    val routeSigmets: RouteSigmets? = null,
    @SerialName("observed_conditions")
    val observedConditions: ObservedConditions? = null,
    val changes: LiveChanges? = null,
    @SerialName("last_refresh_delta")
    val lastRefreshDelta: RefreshDelta? = null,
    val glance: LiveGlance? = null,
    val ribbon: LiveRibbon? = null,
    val storms: LiveStorms? = null,
) {
    /** True when the live layer carries at least one observation block to overlay on the pack. */
    val hasObservationOverlay: Boolean
        get() = routeObservations != null || routeSigmets != null || observedConditions != null

    val liveUpdatedEpochMs: Long?
        get() = IsoTime.parseEpochMs(liveUpdatedAt)

    val packEpochMs: Long?
        get() = IsoTime.parseEpochMs(packTimestamp)

    /**
     * True when this layer has a strictly newer `live_updated_at` than [other]
     * (or when [other] has no timestamp yet).
     */
    fun supersedes(other: LiveLayerResponse?): Boolean {
        if (other == null) return true
        val mine = liveUpdatedEpochMs ?: return false
        val theirs = other.liveUpdatedEpochMs ?: return true
        return mine > theirs
    }
}

// ============================================================================
// Glance
// ============================================================================

@Serializable
data class LiveGlance(
    val status: String = "quiet",
    val headline: String = "",
    val subline: String? = null,
    val highlights: List<LiveHighlight> = emptyList(),
    @SerialName("degraded_sources")
    val degradedSources: List<String> = emptyList(),
    val lines: List<LiveGlanceLine>? = null,
    @SerialName("quiet_summary")
    val quietSummary: String? = null,
)

@Serializable
data class LiveHighlight(
    val text: String = "",
    val level: String = "info",
    val focus: LiveFocus? = null,
    @SerialName("change_key")
    val changeKey: String? = null,
) {
    val computedId: String
        get() = "${changeKey ?: ""}|$level|$text"
}

@Serializable
data class LiveGlanceLine(
    val kind: String = "",
    val level: String = "info",
    val text: String = "",
    val detail: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    val focus: LiveFocus? = null,
    @SerialName("change_key")
    val changeKey: String? = null,
    val trail: LiveChangeTrail? = null,
) {
    val computedId: String
        get() = "${changeKey ?: ""}|$kind|$text"
}

@Serializable
data class LiveFocus(
    val layer: String? = null,
    @SerialName("station_icao")
    val stationIcao: String? = null,
    @SerialName("segment_index")
    val segmentIndex: Int? = null,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    @SerialName("sigmet_key")
    val sigmetKey: String? = null,
    @SerialName("storm_id")
    val stormId: String? = null,
)

// ============================================================================
// Ribbon
// ============================================================================

@Serializable
data class LiveRibbon(
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double = 0.0,
    val waypoints: List<RibbonWaypoint> = emptyList(),
    val segments: List<LiveRibbonSegment> = emptyList(),
    val stations: List<RibbonStation> = emptyList(),
    val sigmets: List<RibbonSigmet> = emptyList(),
)

@Serializable
data class RibbonWeather(
    val code: String = "",
    val label: String = "",
    val severity: String = "moderate",
)

@Serializable
data class RibbonWaypoint(
    val icao: String = "",
    val name: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double = 0.0,
) {
    val computedId: String
        get() = "$icao@$distanceNm"
}

@Serializable
data class LiveRibbonSegment(
    val index: Int? = null,
    @SerialName("start_nm")
    val startNm: Double = 0.0,
    @SerialName("end_nm")
    val endNm: Double = 0.0,
    @SerialName("radar_status")
    val radarStatus: String = "none",
    @SerialName("radar_max_dbz")
    val radarMaxDbz: Double? = null,
    @SerialName("radar_coverage")
    val radarCoverage: Double? = null,
    @SerialName("tops_max_ft")
    val topsMaxFt: Double? = null,
    @SerialName("tops_status")
    val topsStatus: String = "none",
    @SerialName("flash_count")
    val flashCount: Int = 0,
    @SerialName("nearest_flash_nm")
    val nearestFlashNm: Double? = null,
    @SerialName("lightning_status")
    val lightningStatus: String = "none",
    val place: RoutePlace? = null,
) {
    val computedId: String
        get() = "${index ?: -1}:$startNm-$endNm"
}

@Serializable
data class RibbonStation(
    val icao: String = "",
    val name: String? = null,
    val role: String = "corridor",
    @SerialName("enroute_distance_nm")
    val enrouteDistanceNm: Double = 0.0,
    @SerialName("offset_nm")
    val offsetNm: Double = 0.0,
    @SerialName("metar_category")
    val metarCategory: String? = null,
    @SerialName("taf_category")
    val tafCategory: String? = null,
    @SerialName("model_category")
    val modelCategory: String? = null,
    @SerialName("category_match")
    val categoryMatch: String? = null,
    @SerialName("metar_ceiling_ft")
    val metarCeilingFt: Int? = null,
    @SerialName("metar_visibility_m")
    val metarVisibilityM: Int? = null,
    @SerialName("wind_advisory")
    val windAdvisory: String? = null,
    @SerialName("has_change")
    val hasChange: Boolean = false,
    @SerialName("change_direction")
    val changeDirection: String? = null,
    val weather: List<RibbonWeather>? = null,
    @SerialName("taf_weather")
    val tafWeather: List<RibbonWeather>? = null,
    @SerialName("obs_proxy")
    val obsProxy: ObsProxy? = null,
    val place: RoutePlace? = null,
) {
    val id: String
        get() = icao
}

@Serializable
data class RibbonSigmet(
    val key: String = "",
    @SerialName("fir_id")
    val firId: String = "",
    val hazard: String = "",
    val qualifier: String? = null,
    @SerialName("start_nm")
    val startNm: Double? = null,
    @SerialName("end_nm")
    val endNm: Double? = null,
    @SerialName("min_distance_nm")
    val minDistanceNm: Double? = null,
    @SerialName("base_ft")
    val baseFt: Int? = null,
    @SerialName("top_ft")
    val topFt: Int? = null,
    @SerialName("valid_to")
    val validTo: String = "",
    @SerialName("is_new")
    val isNew: Boolean = false,
    @SerialName("start_place")
    val startPlace: RoutePlace? = null,
    @SerialName("end_place")
    val endPlace: RoutePlace? = null,
) {
    val id: String
        get() = key
}

@Serializable
data class RoutePlace(
    val text: String = "",
    val anchor: RouteAnchor = RouteAnchor(),
)

@Serializable
data class RouteAnchor(
    val code: String = "",
    val name: String = "",
    val kind: String = "waypoint",
    @SerialName("distance_nm")
    val distanceNm: Double = 0.0,
    @SerialName("bearing_deg")
    val bearingDeg: Int? = null,
    val cardinal: String? = null,
    @SerialName("along_route_nm")
    val alongRouteNm: Double = 0.0,
)

// ============================================================================
// Changes
// ============================================================================

@Serializable
data class LiveChanges(
    @SerialName("baseline_at")
    val baselineAt: String? = null,
    @SerialName("computed_at")
    val computedAt: String = "",
    val changes: List<LiveChange> = emptyList(),
    @SerialName("worsened_count")
    val worsenedCount: Int = 0,
    @SerialName("improved_count")
    val improvedCount: Int = 0,
    @SerialName("alert_count")
    val alertCount: Int = 0,
    @SerialName("recently_cleared")
    val recentlyCleared: List<LiveChange>? = null,
)

@Serializable
data class LiveChange(
    val key: String = "",
    val kind: String = "",
    val source: String = "",
    val direction: String = "",
    val tier: String = "",
    val role: String = "",
    val icao: String? = null,
    @SerialName("station_id")
    val stationId: String? = null,
    @SerialName("from_value")
    val fromValue: String? = null,
    @SerialName("to_value")
    val toValue: String? = null,
    @SerialName("observed_at")
    val observedAt: String? = null,
    @SerialName("enroute_distance_nm")
    val enrouteDistanceNm: Double? = null,
    val message: String = "",
    @SerialName("new_alert")
    val newAlert: Boolean = false,
    val trail: LiveChangeTrail? = null,
    @SerialName("cleared_at")
    val clearedAt: String? = null,
    val place: RoutePlace? = null,
) {
    val id: String
        get() = key
}

@Serializable
data class LiveChangeTrail(
    val spans: List<LiveTrailSpan> = emptyList(),
    @SerialName("times_today")
    val timesToday: Int = 0,
    val reports: List<LiveTrailReport>? = null,
    @SerialName("baseline_source")
    val baselineSource: String? = null,
)

@Serializable
data class LiveTrailSpan(
    val start: String = "",
    val end: String? = null,
)

@Serializable
data class LiveTrailReport(
    val at: String = "",
    val category: String = "",
    @SerialName("report_type")
    val reportType: String = "METAR",
    @SerialName("wind_advisory")
    val windAdvisory: String? = null,
    @SerialName("crosswind_kt")
    val crosswindKt: Double? = null,
    @SerialName("best_runway_id")
    val bestRunwayId: String? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
    @SerialName("visibility_m")
    val visibilityM: Int? = null,
)

@Serializable
data class RefreshDelta(
    val worsened: Boolean = false,
    val messages: List<String> = emptyList(),
    @SerialName("computed_at")
    val computedAt: String = "",
)

// ============================================================================
// Storms
// ============================================================================

@Serializable
data class LiveStorms(
    @SerialName("valid_time")
    val validTime: String? = null,
    @SerialName("corridor_nm")
    val corridorNm: Double = 50.0,
    val cells: List<LiveStorm> = emptyList(),
    @SerialName("direct_threat_count")
    val directThreatCount: Int = 0,
    @SerialName("vicinity_count")
    val vicinityCount: Int = 0,
)

@Serializable
data class LiveStorm(
    val id: String? = null,
    val severity: String = "developing",
    @SerialName("disposition")
    val disposition: String = "vicinity",
    @SerialName("distance_nm")
    val distanceNm: Double = 0.0,
    @SerialName("enroute_distance_nm")
    val enrouteDistanceNm: Double = 0.0,
    @SerialName("bearing_from_route")
    val bearingFromRoute: String? = null,
    val side: String? = null,
    @SerialName("flash_count")
    val flashCount: Int = 0,
    @SerialName("flash_rate_per_min")
    val flashRatePerMin: Double = 0.0,
    @SerialName("max_dbz")
    val maxDbz: Double? = null,
    @SerialName("max_top_ft")
    val maxTopFt: Double? = null,
    @SerialName("area_km2")
    val areaKm2: Double = 0.0,
    @SerialName("track_deg")
    val trackDeg: Double? = null,
    @SerialName("speed_kt")
    val speedKt: Double? = null,
    @SerialName("closing_kt")
    val closingKt: Double? = null,
    @SerialName("cpa_nm")
    val cpaNm: Double? = null,
    @SerialName("cpa_minutes")
    val cpaMinutes: Double? = null,
    @SerialName("intercept_minutes")
    val interceptMinutes: Double? = null,
    @SerialName("intercept_enroute_nm")
    val interceptEnrouteNm: Double? = null,
    val headline: String = "",
    val detail: String = "",
    val place: RoutePlace? = null,
    @SerialName("intercept_place")
    val interceptPlace: RoutePlace? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    @SerialName("radius_nm")
    val radiusNm: Double? = null,
    val track: List<LiveStormTrackPoint>? = null,
    val estimates: List<LiveStormEstimate>? = null,
) {
    val computedId: String
        get() = id ?: "$enrouteDistanceNm:$distanceNm:$headline"
}

@Serializable
data class LiveStormTrackPoint(
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("age_minutes")
    val ageMinutes: Double = 0.0,
) {
    val computedId: String
        get() = "$ageMinutes:$lat:$lon"
}

@Serializable
data class LiveStormEstimate(
    val horizon: String = "",
    val label: String = "",
    val confidence: String = "medium",
    val disposition: String = "vicinity",
    val text: String = "",
    @SerialName("cpa_nm")
    val cpaNm: Double? = null,
    @SerialName("cpa_minutes")
    val cpaMinutes: Double? = null,
    val place: RoutePlace? = null,
) {
    val id: String
        get() = horizon
}

// ============================================================================
// Live Position Reporting (GPX / Device GPS -> Server)
// ============================================================================

@Serializable
data class LivePositionFix(
    val lat: Double,
    val lon: Double,
    @SerialName("altitude_ft")
    val altitudeFt: Double? = null,
    @SerialName("ground_speed_kt")
    val groundSpeedKt: Double? = null,
    @SerialName("track_deg")
    val trackDeg: Double? = null,
    @SerialName("recorded_at")
    val recordedAt: String,
)

@Serializable
data class LivePositionRequest(
    val fixes: List<LivePositionFix>,
)

@Serializable
data class LivePositionResult(
    val accepted: Int = 0,
    @SerialName("active_flight_id")
    val activeFlightId: String? = null,
    @SerialName("along_track_nm")
    val alongTrackNm: Double? = null,
    @SerialName("cross_track_nm")
    val crossTrackNm: Double? = null,
    @SerialName("ground_speed_kt")
    val groundSpeedKt: Double? = null,
    @SerialName("altitude_ft")
    val altitudeFt: Double? = null,
    @SerialName("phase")
    val phase: String? = null,
)

object LiveTime {
    /**
     * Parses an ISO-8601 timestamp into epoch milliseconds (mirrors `LiveTime.parse` on iOS).
     */
    fun parseEpochMs(iso: String?): Long? = IsoTime.parseEpochMs(iso)
}
