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
    @SerialName("as_of")
    val asOf: String? = null,
    val headline: String = "",
    val subline: String? = null,
    val comparison: String? = null,
    val highlight: LiveHighlight? = null,
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
    val model: String? = null,
    @SerialName("facts_hash")
    val factsHash: String? = null,
    @SerialName("generated_at")
    val generatedAt: String? = null,
    @SerialName("latency_ms")
    val latencyMs: Int? = null,
    val level: String = "info",
    val focus: LiveFocus? = null,
    @SerialName("change_key")
    val changeKey: String? = null,
) {
    val computedId: String
        get() = "${changeKey ?: factsHash ?: ""}|$level|$text"
}

@Serializable
data class LiveGlanceLine(
    val phase: String? = null,
    val icao: String? = null,
    val kind: String = "",
    val level: String = "info",
    val text: String = "",
    val detail: String? = null,
    val alert: Boolean = false,
    val passed: Boolean = false,
    val unavailable: List<String> = emptyList(),
    val sources: List<String> = emptyList(),
    @SerialName("proxy_caption")
    val proxyCaption: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    val focus: LiveFocus? = null,
    @SerialName("change_key")
    val changeKey: String? = null,
    val trail: LiveChangeTrail? = null,
) {
    val computedId: String
        get() = "${changeKey ?: phase ?: ""}|$kind|$text"
}

@Serializable
data class LiveFocus(
    val kind: String? = null,
    val id: String? = null,
    val bbox: List<Double>? = null,
    val layers: List<String>? = null,
    val time: String? = null,
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
    @SerialName("route_nm")
    val routeNm: Double? = null,
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double = routeNm ?: 0.0,
    @SerialName("flown_nm")
    val flownNm: Double? = null,
    @SerialName("departure_at")
    val departureAt: String? = null,
    @SerialName("arrival_at")
    val arrivalAt: String? = null,
    @SerialName("segment_nm")
    val segmentNm: Double? = null,
    @SerialName("radar_radius_nm")
    val radarRadiusNm: Double? = null,
    @SerialName("radar_time")
    val radarTime: String? = null,
    val waypoints: List<RibbonWaypoint> = emptyList(),
    val segments: List<LiveRibbonSegment> = emptyList(),
    val stations: List<RibbonStation> = emptyList(),
    val sigmets: List<RibbonSigmet> = emptyList(),
    val weather: List<RibbonWeather> = emptyList(),
    @SerialName("weather_status")
    val weatherStatus: String? = null,
    @SerialName("weather_corridor_nm")
    val weatherCorridorNm: Double? = null,
    @SerialName("weather_bin_nm")
    val weatherBinNm: Double? = null,
    val anchors: List<RouteAnchor> = emptyList(),
)

@Serializable
data class RibbonWeather(
    val id: String = "",
    val tier: String? = null,
    @SerialName("from_nm")
    val fromNm: Double? = null,
    @SerialName("to_nm")
    val toNm: Double? = null,
    val side: String? = null,
    @SerialName("near_nm")
    val nearNm: Double? = null,
    @SerialName("far_nm")
    val farNm: Double? = null,
    val profile: List<List<Double>>? = null,
    @SerialName("peak_dbz")
    val peakDbz: Double? = null,
    val intensity: String? = null,
    val flashes: Int? = null,
    @SerialName("motion_rel_deg")
    val motionRelDeg: Double? = null,
    @SerialName("speed_kt")
    val speedKt: Double? = null,
    @SerialName("storm_id")
    val stormId: String? = null,
    val code: String = "",
    val label: String = "",
    val severity: String = "moderate",
)

object FlexibleRibbonWeatherListSerializer : kotlinx.serialization.KSerializer<List<RibbonWeather>> {
    override val descriptor: kotlinx.serialization.descriptors.SerialDescriptor =
        kotlinx.serialization.builtins.ListSerializer(kotlinx.serialization.json.JsonElement.serializer()).descriptor

    override fun deserialize(decoder: kotlinx.serialization.encoding.Decoder): List<RibbonWeather> {
        val jsonDecoder = decoder as? kotlinx.serialization.json.JsonDecoder ?: return emptyList()
        val element = jsonDecoder.decodeJsonElement()
        val array = element as? kotlinx.serialization.json.JsonArray ?: return emptyList()
        return array.mapNotNull { item ->
            when (item) {
                is kotlinx.serialization.json.JsonPrimitive -> {
                    val str = item.content
                    if (str.isNotBlank()) RibbonWeather(code = str, label = str) else null
                }
                is kotlinx.serialization.json.JsonObject -> {
                    jsonDecoder.json.decodeFromJsonElement(RibbonWeather.serializer(), item)
                }
                else -> null
            }
        }
    }

    override fun serialize(encoder: kotlinx.serialization.encoding.Encoder, value: List<RibbonWeather>) {
        kotlinx.serialization.builtins.ListSerializer(RibbonWeather.serializer()).serialize(encoder, value)
    }
}

@Serializable
data class RibbonWaypoint(
    val icao: String = "",
    val name: String? = null,
    @SerialName("along_nm")
    val alongNm: Double? = null,
    @SerialName("distance_nm")
    val distanceNm: Double = alongNm ?: 0.0,
    val eta: String? = null,
) {
    val computedId: String
        get() = "$icao@$distanceNm"
}

@Serializable
data class LiveRibbonSegment(
    val index: Int? = null,
    @SerialName("from_nm")
    val fromNm: Double? = null,
    @SerialName("to_nm")
    val toNm: Double? = null,
    @SerialName("start_nm")
    val startNm: Double = fromNm ?: 0.0,
    @SerialName("end_nm")
    val endNm: Double = toNm ?: 0.0,
    @SerialName("eta_from")
    val etaFrom: String? = null,
    @SerialName("eta_to")
    val etaTo: String? = null,
    @SerialName("radar_status")
    val radarStatus: String = "none",
    @SerialName("radar_max_dbz")
    val radarMaxDbz: Double? = null,
    @SerialName("radar_intensity")
    val radarIntensity: String? = null,
    @SerialName("radar_coverage")
    val radarCoverage: Double? = null,
    @SerialName("tops_max_ft")
    val topsMaxFt: Double? = null,
    @SerialName("tops_status")
    val topsStatus: String = "none",
    val lightning: Boolean? = null,
    @SerialName("flash_count")
    val flashCount: Int = if (lightning == true) 1 else 0,
    @SerialName("nearest_flash_nm")
    val nearestFlashNm: Double? = null,
    @SerialName("lightning_status")
    val lightningStatus: String = if (lightning == true) "present" else "none",
    @SerialName("sigmet_ids")
    val sigmetIds: List<String> = emptyList(),
    @SerialName("storm_ids")
    val stormIds: List<String> = emptyList(),
    val focus: LiveFocus? = null,
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
    @SerialName("along_nm")
    val alongNm: Double? = null,
    @SerialName("cross_nm")
    val crossNm: Double? = null,
    @SerialName("enroute_distance_nm")
    val enrouteDistanceNm: Double = alongNm ?: 0.0,
    @SerialName("offset_nm")
    val offsetNm: Double = crossNm ?: 0.0,
    val eta: String? = null,
    @SerialName("metar_category")
    val metarCategory: String? = null,
    @SerialName("metar_time")
    val metarTime: String? = null,
    val convective: List<String>? = null,
    @SerialName("taf_category_at_eta")
    val tafCategoryAtEta: String? = null,
    @SerialName("taf_category")
    val tafCategory: String? = tafCategoryAtEta,
    @SerialName("taf_temporary_type")
    val tafTemporaryType: String? = null,
    @SerialName("taf_temporary_category")
    val tafTemporaryCategory: String? = null,
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
    @Serializable(with = FlexibleRibbonWeatherListSerializer::class)
    val weather: List<RibbonWeather>? = null,
    @SerialName("taf_weather")
    @Serializable(with = FlexibleRibbonWeatherListSerializer::class)
    val tafWeather: List<RibbonWeather>? = null,
    val via: List<String>? = null,
    @SerialName("obs_proxy")
    val obsProxy: ObsProxy? = null,
    val focus: LiveFocus? = null,
    val place: RoutePlace? = null,
) {
    val id: String
        get() = icao
}

@Serializable
data class RibbonSigmet(
    val id: String = "",
    val key: String = id,
    val label: String? = null,
    @SerialName("fir_id")
    val firId: String = "",
    val hazard: String = "",
    val qualifier: String? = null,
    @SerialName("from_nm")
    val fromNm: Double? = null,
    @SerialName("to_nm")
    val toNm: Double? = null,
    @SerialName("start_nm")
    val startNm: Double? = fromNm,
    @SerialName("end_nm")
    val endNm: Double? = toNm,
    @SerialName("min_distance_nm")
    val minDistanceNm: Double? = null,
    @SerialName("span_text")
    val spanText: String? = null,
    @SerialName("base_ft")
    val baseFt: Int? = null,
    @SerialName("top_ft")
    val topFt: Int? = null,
    @SerialName("valid_from")
    val validFrom: String? = null,
    @SerialName("valid_to")
    val validTo: String = "",
    val pending: Boolean = false,
    val new: Boolean? = null,
    @SerialName("is_new")
    val isNew: Boolean = new ?: false,
    val motion: String? = null,
    val focus: LiveFocus? = null,
    @SerialName("start_place")
    val startPlace: RoutePlace? = null,
    @SerialName("end_place")
    val endPlace: RoutePlace? = null,
)

@Serializable
data class RoutePlace(
    val text: String = "",
    val short: String? = null,
    val lateral: String? = null,
    val relation: String? = null,
    val anchor: String? = null,
    @SerialName("anchor_kind")
    val anchorKind: String? = null,
    @SerialName("anchor_icao")
    val anchorIcao: String? = null,
    @SerialName("anchor_name")
    val anchorName: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    @SerialName("along_nm")
    val alongNm: Double? = null,
    @SerialName("cross_nm")
    val crossNm: Double? = null,
    val side: String? = null,
)

@Serializable
data class RouteAnchor(
    val key: String = "",
    val code: String = key,
    val name: String = "",
    val kind: String = "waypoint",
    @SerialName("along_nm")
    val alongNm: Double = 0.0,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    val icao: String? = null,
    val town: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double = 0.0,
    @SerialName("bearing_deg")
    val bearingDeg: Int? = null,
    val cardinal: String? = null,
    @SerialName("along_route_nm")
    val alongRouteNm: Double = alongNm,
)

// ============================================================================
// Changes
// ============================================================================

@Serializable
data class LiveChanges(
    @SerialName("baseline_at")
    val baselineAt: String? = null,
    @SerialName("baseline_source")
    val baselineSource: String? = null,
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
    @SerialName("new_sigmets")
    val newSigmets: List<String>? = null,
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
    val replaces: String? = null,
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
    val status: String = "available",
    @SerialName("frame_time")
    val frameTime: String? = null,
    @SerialName("valid_time")
    val validTime: String? = frameTime,
    @SerialName("unavailable_since")
    val unavailableSince: String? = null,
    @SerialName("lightning_pending")
    val lightningPending: Boolean = false,
    @SerialName("corridor_nm")
    val corridorNm: Double = 50.0,
    @SerialName("route_nm")
    val routeNm: Double? = null,
    val storms: List<LiveStorm> = emptyList(),
    val cells: List<LiveStorm> = storms,
    @SerialName("direct_threat_count")
    val directThreatCount: Int = 0,
    @SerialName("vicinity_count")
    val vicinityCount: Int = 0,
)

@Serializable
data class LiveStorm(
    val id: String? = null,
    @SerialName("cell_ids")
    val cellIds: List<String> = emptyList(),
    val severity: String = "developing",
    val intensity: String? = null,
    val trend: String? = null,
    @SerialName("disposition")
    val disposition: String = "vicinity",
    @SerialName("offtrack_nm")
    val offtrackNm: Double? = null,
    @SerialName("along_nm")
    val alongNm: Double? = null,
    @SerialName("cross_nm")
    val crossNm: Double? = null,
    @SerialName("distance_nm")
    val distanceNm: Double = offtrackNm ?: 0.0,
    @SerialName("enroute_distance_nm")
    val enrouteDistanceNm: Double = alongNm ?: 0.0,
    @SerialName("bearing_from_route")
    val bearingFromRoute: String? = null,
    val side: String? = null,
    val flashes: Int? = null,
    @SerialName("flash_count")
    val flashCount: Int = flashes ?: 0,
    @SerialName("flashes_pending")
    val flashesPending: Boolean = false,
    @SerialName("flash_rate_per_min")
    val flashRatePerMin: Double = 0.0,
    @SerialName("peak_dbz")
    val peakDbz: Double? = null,
    @SerialName("max_dbz")
    val maxDbz: Double? = peakDbz,
    @SerialName("top_fl")
    val topFl: Int? = null,
    @SerialName("max_top_ft")
    val maxTopFt: Double? = topFl?.let { it * 100.0 },
    @SerialName("area_km2")
    val areaKm2: Double = 0.0,
    @SerialName("toward_deg")
    val towardDeg: Double? = null,
    @SerialName("track_deg")
    val trackDeg: Double? = towardDeg,
    @SerialName("speed_kt")
    val speedKt: Double? = null,
    @SerialName("relative_motion")
    val relativeMotion: String? = null,
    @SerialName("closing_kt")
    val closingKt: Double? = null,
    @SerialName("abeam_eta")
    val abeamEta: String? = null,
    @SerialName("minutes_to_abeam")
    val minutesToAbeam: Double? = null,
    val ahead: Boolean? = null,
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
    val history: List<LiveStormTrackPoint>? = null,
    val track: List<LiveStormTrackPoint>? = history,
    val estimate: LiveStormEstimate? = null,
    val estimates: List<LiveStormEstimate>? = null,
    val focus: LiveFocus? = null,
) {
    val computedId: String
        get() = id ?: "$enrouteDistanceNm:$distanceNm:$headline"
}

@Serializable
data class LiveStormTrackPoint(
    val at: String? = null,
    @SerialName("offtrack_nm")
    val offtrackNm: Double = 0.0,
    @SerialName("cross_nm")
    val crossNm: Double = 0.0,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("age_minutes")
    val ageMinutes: Double = 0.0,
) {
    val computedId: String
        get() = "${at ?: ageMinutes}:$lat:$lon:$offtrackNm"
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
    @SerialName("cpa_time")
    val cpaTime: String? = null,
    @SerialName("at_eta_offtrack_nm")
    val atEtaOfftrackNm: Double? = null,
    @SerialName("horizon_min")
    val horizonMin: Double? = null,
    @SerialName("cpa_minutes")
    val cpaMinutes: Double? = horizonMin,
    val place: RoutePlace? = null,
) {
    val id: String
        get() = horizon.ifEmpty { "${cpaTime ?: ""}:$cpaNm" }
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
