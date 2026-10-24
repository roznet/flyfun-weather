package aero.flyfun.weather.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonPrimitive
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

// ============================================================================
// Advisories
// ============================================================================

@Serializable
data class AdvisoriesResponse(
    val advisories: List<RouteAdvisoryResult> = emptyList(),
    val catalog: List<AdvisoryCatalogEntry> = emptyList(),
    @SerialName("route_name")
    val routeName: String = "",
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int = 0,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int = 0,
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double = 0.0,
    val models: List<String> = emptyList(),
    val aggregation: String = "worst",
    @SerialName("airport_conditions")
    val airportConditions: AirportConditions? = null,
    val alternates: AlternatesResponse? = null,
)

@Serializable
data class RouteAdvisoryResult(
    @SerialName("advisory_id")
    val advisoryId: String = "",
    @SerialName("aggregate_status")
    val aggregateStatus: String = "unavailable",
    @SerialName("aggregate_detail")
    val aggregateDetail: String = "",
    @SerialName("per_model")
    val perModel: List<ModelAdvisoryResult> = emptyList(),
    @SerialName("parameters_used")
    val parametersUsed: Map<String, Double> = emptyMap(),
    val mitigations: List<Mitigation>? = null,
    val highlights: AdvisoryHighlights? = null,
) {
    val id: String
        get() = advisoryId

    val resolvedStatus: Assessment
        get() = Assessment.fromString(aggregateStatus)
}

@Serializable
data class Mitigation(
    val id: String = "",
    val kind: String = "",
    val text: String = "",
    val target: String? = null,
)

@Serializable
data class AdvisoryHighlights(
    val preset: String? = null,
    val ribbon: Map<String, List<RibbonSegment>>? = null,
    val regions: Map<String, List<HighlightRegion>>? = null,
)

@Serializable
data class RibbonSegment(
    @SerialName("from_nm")
    val fromNm: Double = 0.0,
    @SerialName("to_nm")
    val toNm: Double = 0.0,
    val status: String = "",
)

@Serializable
data class HighlightRegion(
    @SerialName("from_nm")
    val fromNm: Double = 0.0,
    @SerialName("to_nm")
    val toNm: Double = 0.0,
    @SerialName("base_ft")
    val baseFt: Double? = null,
    @SerialName("top_ft")
    val topFt: Double? = null,
    val status: String = "",
)

@Serializable
data class ModelAdvisoryResult(
    val model: String = "",
    val status: String = "unavailable",
    val detail: String = "",
    @SerialName("affected_points")
    val affectedPoints: Int = 0,
    @SerialName("total_points")
    val totalPoints: Int = 0,
    @SerialName("affected_pct")
    val affectedPct: Double = 0.0,
    @SerialName("affected_nm")
    val affectedNm: Double = 0.0,
    @SerialName("total_nm")
    val totalNm: Double = 0.0,
    @SerialName("cross_check")
    val crossCheck: String? = null,
) {
    val id: String
        get() = model

    val resolvedStatus: Assessment
        get() = Assessment.fromString(status)
}

@Serializable
data class AdvisoryCatalogEntry(
    val id: String = "",
    val name: String = "",
    @SerialName("short_description")
    val shortDescription: String = "",
    val description: String = "",
    val category: String = "",
    @SerialName("default_enabled")
    val defaultEnabled: Boolean = true,
    @SerialName("altitude_dependent")
    val altitudeDependent: Boolean = false,
    val parameters: List<AdvisoryParameterDef> = emptyList(),
)

@Serializable
data class AdvisoryParameterDef(
    val key: String = "",
    val label: String = "",
    val description: String = "",
    val type: String = "number",
    val unit: String = "",
    val default: Double = 0.0,
    val min: Double? = null,
    val max: Double? = null,
    val step: Double? = null,
)

// ============================================================================
// Airport Conditions & Runway Wind Picture
// ============================================================================

@Serializable
data class AirportConditions(
    val departure: AirportConditionsSummary,
    val arrival: AirportConditionsSummary,
)

@Serializable
data class AirportConditionsSummary(
    val icao: String = "",
    val name: String = "",
    @SerialName("runway_ends")
    val runwayEnds: List<RunwayEnd> = emptyList(),
    val conditions: List<AirportModelCondition> = emptyList(),
)

@Serializable
data class RunwayEnd(
    val id: String = "",
    @SerialName("heading_deg")
    val headingDeg: Double = 0.0,
)

@Serializable
data class AirportModelCondition(
    val model: String = "",
    @SerialName("flight_category")
    val flightCategory: String = "",
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
    @SerialName("visibility_sm")
    val visibilitySm: Double? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("wind_direction_deg")
    val windDirectionDeg: Double? = null,
    @SerialName("wind_gust_kt")
    val windGustKt: Double? = null,
    @SerialName("best_runway")
    val bestRunway: RunwayWind? = null,
    @SerialName("all_runways")
    val allRunways: List<RunwayWind> = emptyList(),
) {
    val id: String
        get() = model
}

@Serializable
data class RunwayWind(
    @SerialName("runway_id")
    val runwayId: String = "",
    @SerialName("heading_deg")
    val headingDeg: Double = 0.0,
    @SerialName("crosswind_kt")
    val crosswindKt: Double = 0.0,
    @SerialName("headwind_kt")
    val headwindKt: Double = 0.0,
) {
    val id: String
        get() = runwayId
}

data class RunwayEndInfo(
    val id: String,
    val headingTrue: Double,
)

data class RunwayInfo(
    val id: String,
    val lengthFt: Int,
    val widthFt: Int,
    val surface: String,
    val lighted: Boolean = false,
    val closed: Boolean = false,
    val end1: RunwayEndInfo,
    val end2: RunwayEndInfo,
)

data class WindSample(
    val source: String,
    val directionDeg: Double,
    val speedKt: Double,
    val gustKt: Double? = null,
) {
    val id: String
        get() = source
}

data class EndComponents(
    val headwindKt: Double,
    val crosswindSignedKt: Double,
) {
    val crosswindKt: Double
        get() = abs(crosswindSignedKt)

    companion object {
        fun compute(windDir: Double, windSpeed: Double, runwayHeading: Double): EndComponents {
            val angleRad = (windDir - runwayHeading) * Math.PI / 180.0
            return EndComponents(
                headwindKt = windSpeed * cos(angleRad),
                crosswindSignedKt = windSpeed * sin(angleRad),
            )
        }
    }
}

data class WindAtAirport(
    val sample: WindSample,
    val bestEndId: String?,
    val bestEndHeading: Double?,
    val bestHeadwindKt: Double?,
    val bestCrosswindSignedKt: Double?,
) {
    val id: String
        get() = sample.source

    companion object {
        fun evaluate(sample: WindSample, ends: List<RunwayEndInfo>): WindAtAirport {
            if (ends.isEmpty()) {
                return WindAtAirport(sample, null, null, null, null)
            }
            var bestEnd = ends.first()
            var bestComp = EndComponents.compute(sample.directionDeg, sample.speedKt, bestEnd.headingTrue)

            for (end in ends.drop(1)) {
                val comp = EndComponents.compute(sample.directionDeg, sample.speedKt, end.headingTrue)
                val endHeadwind = comp.headwindKt >= 0
                val bestHeadwind = bestComp.headwindKt >= 0
                val isBetter = if (endHeadwind != bestHeadwind) {
                    endHeadwind
                } else if (abs(comp.crosswindKt - bestComp.crosswindKt) > 0.05) {
                    comp.crosswindKt < bestComp.crosswindKt
                } else {
                    comp.headwindKt > bestComp.headwindKt
                }
                if (isBetter) {
                    bestEnd = end
                    bestComp = comp
                }
            }
            return WindAtAirport(
                sample = sample,
                bestEndId = bestEnd.id,
                bestEndHeading = bestEnd.headingTrue,
                bestHeadwindKt = bestComp.headwindKt,
                bestCrosswindSignedKt = bestComp.crosswindSignedKt,
            )
        }
    }
}

data class RunwayWindPicture(
    val icao: String,
    val name: String,
    val runways: List<RunwayInfo>,
    val ends: List<RunwayEndInfo>,
    val winds: List<WindAtAirport>,
    val primarySource: String?,
)

// ============================================================================
// Advisory Detail (drill-down endpoint)
// ============================================================================

@Serializable
data class AdvisoryDetailResponse(
    @SerialName("advisory_id")
    val advisoryId: String = "",
    @SerialName("aggregate_status")
    val aggregateStatus: String = "unavailable",
    @SerialName("aggregate_detail")
    val aggregateDetail: String = "",
    @SerialName("parameters_used")
    val parametersUsed: Map<String, Double> = emptyMap(),
    @SerialName("cross_check_note")
    val crossCheckNote: String? = null,
    val name: String? = null,
    val category: String? = null,
    val description: String? = null,
    @SerialName("convective_note")
    val convectiveNote: String? = null,
    @SerialName("per_model")
    val perModel: List<ModelAdvisoryDetail> = emptyList(),
    val convective: Map<String, ConvectiveModelDetail>? = null,
) {
    val resolvedStatus: Assessment
        get() = Assessment.fromString(aggregateStatus)
}

@Serializable
data class ModelAdvisoryDetail(
    val model: String = "",
    val status: String = "unavailable",
    val detail: String = "",
    @SerialName("affected_pct")
    val affectedPct: Double = 0.0,
    @SerialName("affected_nm")
    val affectedNm: Double = 0.0,
    @SerialName("total_nm")
    val totalNm: Double = 0.0,
    @SerialName("cross_check")
    val crossCheck: String? = null,
) {
    val id: String
        get() = model

    val resolvedStatus: Assessment
        get() = Assessment.fromString(status)
}

@Serializable
data class ConvectiveModelDetail(
    @SerialName("assessment_method")
    val assessmentMethod: String? = null,
    @SerialName("method_counts")
    val methodCounts: Map<String, Int>? = null,
    val thermo: ConvectiveThermo? = null,
    val nwp: ConvectiveNwp? = null,
)

@Serializable
data class ConvectiveThermo(
    @SerialName("cape_range_jkg")
    val capeRangeJkg: List<Double>? = null,
    val peak: ConvectivePeak? = null,
)

@Serializable
data class ConvectivePeak(
    @SerialName("cape_jkg")
    val capeJkg: Double? = null,
    @SerialName("el_top_ft")
    val elTopFt: Double? = null,
    @SerialName("risk_level")
    val riskLevel: String? = null,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
    @SerialName("waypoint_icao")
    val waypointIcao: String? = null,
    @SerialName("valid_time")
    val validTime: String? = null,
    val eta: String? = null,
)

@Serializable
data class ConvectiveNwp(
    @SerialName("max_cover_pct")
    val maxCoverPct: Double? = null,
    @SerialName("peak_top_ft")
    val peakTopFt: Double? = null,
    val note: String? = null,
)

// ============================================================================
// Digest
// ============================================================================

/**
 * Serializer for `watch_items` which can be either a JSON array of strings or a
 * newline-delimited string on the wire.
 */
object FlexibleStringListSerializer : KSerializer<List<String>?> {
    private val listSerializer = ListSerializer(String.serializer())
    override val descriptor: SerialDescriptor = listSerializer.descriptor

    override fun deserialize(decoder: Decoder): List<String>? {
        val jsonDecoder = decoder as? JsonDecoder ?: return listSerializer.deserialize(decoder)
        return when (val element = jsonDecoder.decodeJsonElement()) {
            is JsonNull -> null
            is JsonArray -> element.mapNotNull { (it as? JsonPrimitive)?.content }
            is JsonPrimitive -> {
                if (!element.isString) null
                else element.content
                    .split('\n')
                    .map { it.trim() }
                    .filter { it.isNotEmpty() }
            }
            else -> null
        }
    }

    @OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
    override fun serialize(encoder: Encoder, value: List<String>?) {
        if (value == null) {
            encoder.encodeNull()
        } else {
            listSerializer.serialize(encoder, value)
        }
    }
}

@Serializable
data class DigestResponse(
    val assessment: String? = null,
    @SerialName("assessment_reason")
    val assessmentReason: String? = null,
    val synoptic: String? = null,
    @SerialName("specific_concerns")
    val specificConcerns: String? = null,
    val trend: String? = null,
    @Serializable(with = FlexibleStringListSerializer::class)
    @SerialName("watch_items")
    val watchItems: List<String>? = null,
    val winds: String? = null,
    val icing: String? = null,
    val turbulence: String? = null,
    val precipitation: String? = null,
    val visibility: String? = null,
    @SerialName("model_agreement")
    val modelAgreement: String? = null,
    val recommendations: String? = null,
    @SerialName("llm_digest")
    val llmDigest: String? = null,
) {
    val resolvedAssessment: Assessment
        get() = Assessment.fromString(assessment)
}

typealias DigestContent = DigestResponse

// ============================================================================
// Snapshot
// ============================================================================

@Serializable
data class SnapshotResponse(
    val route: RouteConfig? = null,
    @SerialName("target_date")
    val targetDate: String = "",
    @SerialName("days_out")
    val daysOut: Int = 0,
    @SerialName("departure_time")
    val departureTime: String? = null,
    val analyses: List<WaypointAnalysis> = emptyList(),
    @SerialName("route_observations")
    val routeObservations: RouteObservations? = null,
    @SerialName("route_sigmets")
    val routeSigmets: RouteSigmets? = null,
    @SerialName("observed_conditions")
    val observedConditions: ObservedConditions? = null,
)

@Serializable
data class RouteConfig(
    val name: String = "",
    val waypoints: List<Waypoint> = emptyList(),
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int = 8000,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int = 18000,
    @SerialName("flight_duration_hours")
    val flightDurationHours: Double = 2.0,
)

@Serializable
data class Waypoint(
    val icao: String = "",
    val name: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
) {
    val id: String
        get() = icao
}

@Serializable
data class WaypointAnalysis(
    val waypoint: Waypoint = Waypoint(),
    @SerialName("target_time")
    val targetTime: String = "",
    @SerialName("wind_components")
    val windComponents: Map<String, WindComponent> = emptyMap(),
    val sounding: Map<String, SoundingAnalysisSummary>? = null,
) {
    val id: String
        get() = waypoint.icao
}

@Serializable
data class WindComponent(
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double = 0.0,
    @SerialName("wind_direction_deg")
    val windDirectionDeg: Double = 0.0,
    @SerialName("track_deg")
    val trackDeg: Double = 0.0,
    @SerialName("headwind_kt")
    val headwindKt: Double = 0.0,
    @SerialName("crosswind_kt")
    val crosswindKt: Double = 0.0,
)

@Serializable
data class SoundingAnalysisSummary(
    val indices: ThermodynamicIndicesSummary? = null,
    @SerialName("cloud_cover_low_pct")
    val cloudCoverLowPct: Double? = null,
    @SerialName("cloud_cover_mid_pct")
    val cloudCoverMidPct: Double? = null,
    @SerialName("cloud_cover_high_pct")
    val cloudCoverHighPct: Double? = null,
)

@Serializable
data class ThermodynamicIndicesSummary(
    @SerialName("freezing_level_ft")
    val freezingLevelFt: Double? = null,
    @SerialName("cape_surface_jkg")
    val capeSurfaceJkg: Double? = null,
    @SerialName("sounding_ceiling_ft")
    val soundingCeilingFt: Double? = null,
    @SerialName("nwp_ceiling_ft")
    val nwpCeilingFt: Double? = null,
)

// ============================================================================
// Route Observations (D-0 METAR/TAF)
// ============================================================================

@Serializable
data class RouteObservations(
    @SerialName("corridor_nm")
    val corridorNm: Double = 15.0,
    @SerialName("fetch_time")
    val fetchTime: String = "",
    @SerialName("airports_found")
    val airportsFound: Int = 0,
    @SerialName("airports_with_metar")
    val airportsWithMetar: Int = 0,
    @SerialName("airports_with_taf")
    val airportsWithTaf: Int = 0,
    val airports: List<AirportObservation> = emptyList(),
    val comparisons: List<ObservationComparison> = emptyList(),
    @SerialName("worst_metar_category")
    val worstMetarCategory: String? = null,
    @SerialName("worst_taf_category")
    val worstTafCategory: String? = null,
    @SerialName("has_conflicts")
    val hasConflicts: Boolean = false,
    @SerialName("phenomena_along_route")
    val phenomenaAlongRoute: List<String> = emptyList(),
)

@Serializable
data class ObsProxy(
    val icao: String = "",
    val name: String? = null,
    val distanceNm: Double = 0.0,
)

@Serializable
data class AirportObservation(
    val icao: String = "",
    val name: String? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    @SerialName("distance_from_route_nm")
    val distanceFromRouteNm: Double = 0.0,
    @SerialName("enroute_distance_nm")
    val enrouteDistanceNm: Double? = null,
    @SerialName("nearest_waypoint_icao")
    val nearestWaypointIcao: String = "",
    @SerialName("has_metar")
    val hasMetar: Boolean = false,
    @SerialName("metar_raw")
    val metarRaw: String? = null,
    @SerialName("metar_time")
    val metarTime: String? = null,
    @SerialName("metar_flight_category")
    val metarFlightCategory: String? = null,
    @SerialName("metar_ceiling_ft")
    val metarCeilingFt: Int? = null,
    @SerialName("metar_visibility_m")
    val metarVisibilityM: Int? = null,
    @SerialName("metar_wind_dir")
    val metarWindDir: Int? = null,
    @SerialName("metar_wind_speed_kt")
    val metarWindSpeedKt: Int? = null,
    @SerialName("metar_wind_gust_kt")
    val metarWindGustKt: Int? = null,
    @SerialName("metar_weather")
    val metarWeather: List<String> = emptyList(),
    @SerialName("metar_temperature_c")
    val metarTemperatureC: Int? = null,
    @SerialName("metar_dewpoint_c")
    val metarDewpointC: Int? = null,
    @SerialName("metar_qnh")
    val metarQnh: Double? = null,
    @SerialName("has_taf")
    val hasTaf: Boolean = false,
    @SerialName("taf_raw")
    val tafRaw: String? = null,
    @SerialName("taf_flight_category_at_eta")
    val tafFlightCategoryAtEta: String? = null,
    @SerialName("taf_trend_type")
    val tafTrendType: String? = null,
    @SerialName("taf_wind_dir")
    val tafWindDir: Int? = null,
    @SerialName("taf_wind_speed_kt")
    val tafWindSpeedKt: Int? = null,
    @SerialName("taf_wind_gust_kt")
    val tafWindGustKt: Int? = null,
    @SerialName("taf_applicable_lines")
    val tafApplicableLines: List<Int> = emptyList(),
    @SerialName("taf_at_eta")
    val tafAtEta: TafAtEta? = null,
    @SerialName("metar_wind_advisory")
    val metarWindAdvisory: String? = null,
    @SerialName("metar_best_runway_id")
    val metarBestRunwayId: String? = null,
    @SerialName("metar_crosswind_kt")
    val metarCrosswindKt: Double? = null,
    @SerialName("metar_headwind_kt")
    val metarHeadwindKt: Double? = null,
    @SerialName("taf_wind_advisory")
    val tafWindAdvisory: String? = null,
    @SerialName("taf_best_runway_id")
    val tafBestRunwayId: String? = null,
    @SerialName("taf_crosswind_kt")
    val tafCrosswindKt: Double? = null,
    @SerialName("taf_headwind_kt")
    val tafHeadwindKt: Double? = null,
    @SerialName("eta_hour_offset")
    val etaHourOffset: Int? = null,
    @SerialName("obs_proxy")
    val obsProxy: ObsProxy? = null,
    // --- The airport card's readings (#880, server `analysis/airport_card`). Drawn as is (#883).
    @SerialName("metar_report_type")
    val metarReportType: String? = null,
    @SerialName("metar_previous_flight_category")
    val metarPreviousFlightCategory: String? = null,
    @SerialName("metar_previous_time")
    val metarPreviousTime: String? = null,
    @SerialName("taf_prevailing_category_at_eta")
    val tafPrevailingCategoryAtEta: String? = null,
    @SerialName("taf_temporary_category_at_eta")
    val tafTemporaryCategoryAtEta: String? = null,
    @SerialName("taf_temporary_type")
    val tafTemporaryType: String? = null,
    @SerialName("taf_hours")
    val tafHours: List<TafHour> = emptyList(),
    @SerialName("metar_trend")
    val metarTrend: String? = null,
    @SerialName("metar_ceiling_meter")
    val metarCeilingMeter: Double? = null,
    @SerialName("metar_visibility_meter")
    val metarVisibilityMeter: Double? = null,
    @SerialName("metar_visibility_text")
    val metarVisibilityText: String? = null,
    @SerialName("metar_sky_text")
    val metarSkyText: String? = null,
    @SerialName("metar_cavok")
    val metarCavok: Boolean = false,
    @SerialName("metar_wind_short")
    val metarWindShort: String? = null,
    @SerialName("metar_weather_words")
    val metarWeatherWords: List<WeatherWord> = emptyList(),
    @SerialName("metar_crosswind")
    val metarCrosswind: CrosswindReading? = null,
    @SerialName("runway_wind")
    val runwayWind: RunwayWindWire? = null,
) {
    val id: String
        get() = icao
}

@Serializable
data class TafAtEta(
    @SerialName("flight_category")
    val flightCategory: String? = null,
    @SerialName("visibility_m")
    val visibilityM: Int? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
    @SerialName("wind_dir")
    val windDir: Int? = null,
    @SerialName("wind_variable")
    val windVariable: Boolean? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Int? = null,
    @SerialName("wind_gust_kt")
    val windGustKt: Int? = null,
    val weather: List<String>? = null,
    @SerialName("trend_type")
    val trendType: String? = null,
)

@Serializable
data class ObservationComparison(
    val icao: String = "",
    @SerialName("obs_category")
    val obsCategory: String? = null,
    @SerialName("model_category")
    val modelCategory: String? = null,
    @SerialName("category_match")
    val categoryMatch: String = "",
    @SerialName("ceiling_delta_ft")
    val ceilingDeltaFt: Int? = null,
    @SerialName("visibility_delta_m")
    val visibilityDeltaM: Double? = null,
    @SerialName("wind_speed_delta_kt")
    val windSpeedDeltaKt: Double? = null,
    @SerialName("model_wind_dir")
    val modelWindDir: Double? = null,
    @SerialName("model_wind_speed_kt")
    val modelWindSpeedKt: Double? = null,
    @SerialName("model_wind_gust_kt")
    val modelWindGustKt: Double? = null,
    @SerialName("model_wind_advisory")
    val modelWindAdvisory: String? = null,
    @SerialName("model_best_runway_id")
    val modelBestRunwayId: String? = null,
    @SerialName("model_crosswind_kt")
    val modelCrosswindKt: Double? = null,
    @SerialName("wind_advisory_match")
    val windAdvisoryMatch: String? = null,
    val detail: String = "",
) {
    val id: String
        get() = icao
}

// ============================================================================
// Route SIGMETs / AIRMETs
// ============================================================================

@Serializable
data class RouteSigmets(
    @SerialName("corridor_nm")
    val corridorNm: Double = 50.0,
    @SerialName("fetch_time")
    val fetchTime: String = "",
    @SerialName("altitude_low_ft")
    val altitudeLowFt: Int = 0,
    @SerialName("altitude_high_ft")
    val altitudeHighFt: Int = 18000,
    @SerialName("time_window_from")
    val timeWindowFrom: String = "",
    @SerialName("time_window_to")
    val timeWindowTo: String = "",
    @SerialName("route_firs")
    val routeFirs: List<String> = emptyList(),
    val count: Int = 0,
    val hazards: List<String> = emptyList(),
    @SerialName("has_severe")
    val hasSevere: Boolean = false,
    val sigmets: List<SigmetAlongRoute> = emptyList(),
)

@Serializable
data class SigmetAlongRoute(
    @SerialName("fir_id")
    val firId: String = "",
    @SerialName("fir_name")
    val firName: String = "",
    val hazard: String = "",
    val qualifier: String? = null,
    @SerialName("base_ft")
    val baseFt: Int? = null,
    @SerialName("top_ft")
    val topFt: Int? = null,
    @SerialName("valid_from")
    val validFrom: String = "",
    @SerialName("valid_to")
    val validTo: String = "",
    val direction: String? = null,
    @SerialName("speed_kt")
    val speedKt: Int? = null,
    @SerialName("raw_text")
    val rawText: String = "",
    @SerialName("matched_firs")
    val matchedFirs: List<String> = emptyList(),
    @SerialName("min_distance_nm")
    val minDistanceNm: Double? = null,
    @SerialName("enroute_distance_from_nm")
    val enrouteDistanceFromNm: Double? = null,
    @SerialName("enroute_distance_to_nm")
    val enrouteDistanceToNm: Double? = null,
    val coords: List<List<Double>> = emptyList(),
    @SerialName("series_id")
    val seriesId: String? = null,
) {
    val id: String
        get() = "$firId-$hazard-$validFrom-${rawText.hashCode()}"
}

// ============================================================================
// Observed Conditions (D-0 radar + satellite + lightning)
// ============================================================================

@Serializable
data class ObservedAnnulus(
    @SerialName("inner_nm")
    val innerNm: Double = 0.0,
    @SerialName("outer_nm")
    val outerNm: Double = 0.0,
    val coverage: Double? = null,
    val max: Double? = null,
    val mean: Double? = null,
    val p90: Double? = null,
    @SerialName("sample_count")
    val sampleCount: Int? = null,
    @SerialName("valid_count")
    val validCount: Int? = null,
    @SerialName("worst_quadrant")
    val worstQuadrant: String? = null,
    val status: String? = null,
)

@Serializable
data class ObservedField(
    val source: String? = null,
    @SerialName("valid_time")
    val validTime: String? = null,
    val status: String? = null,
    val annuli: List<ObservedAnnulus>? = null,
    @SerialName("along_route_coverage")
    val alongRouteCoverage: Double? = null,
    @SerialName("along_route_max")
    val alongRouteMax: Double? = null,
    @SerialName("along_route_p90")
    val alongRouteP90: Double? = null,
)

@Serializable
data class ObservedTopsAnnulus(
    @SerialName("inner_nm")
    val innerNm: Double = 0.0,
    @SerialName("outer_nm")
    val outerNm: Double = 0.0,
    val coverage: Double? = null,
    @SerialName("min_temp_k")
    val minTempK: Double? = null,
    @SerialName("p10_temp_k")
    val p10TempK: Double? = null,
    @SerialName("mean_temp_k")
    val meanTempK: Double? = null,
    @SerialName("max_top_ft")
    val maxTopFt: Double? = null,
    @SerialName("p90_top_ft")
    val p90TopFt: Double? = null,
    @SerialName("worst_quadrant")
    val worstQuadrant: String? = null,
    val status: String? = null,
)

@Serializable
data class ObservedTopsField(
    val source: String? = null,
    @SerialName("valid_time")
    val validTime: String? = null,
    val status: String? = null,
    val annuli: List<ObservedTopsAnnulus>? = null,
    @SerialName("along_route_coverage")
    val alongRouteCoverage: Double? = null,
    @SerialName("along_route_min_temp_k")
    val alongRouteMinTempK: Double? = null,
    @SerialName("along_route_max_top_ft")
    val alongRouteMaxTopFt: Double? = null,
    @SerialName("along_route_p90_top_ft")
    val alongRouteP90TopFt: Double? = null,
)

@Serializable
data class ObservedFlashAnnulus(
    @SerialName("inner_nm")
    val innerNm: Double = 0.0,
    @SerialName("outer_nm")
    val outerNm: Double = 0.0,
    @SerialName("flash_count")
    val flashCount: Int? = null,
    @SerialName("flash_rate_per_min")
    val flashRatePerMin: Double? = null,
    @SerialName("flash_density_per_min_km2")
    val flashDensityPerMinKm2: Double? = null,
    @SerialName("nearest_nm")
    val nearestNm: Double? = null,
    @SerialName("nearest_quadrant")
    val nearestQuadrant: String? = null,
    val trend: String? = null,
    val status: String? = null,
)

@Serializable
data class ObservedFlashField(
    val source: String? = null,
    @SerialName("valid_time")
    val validTime: String? = null,
    @SerialName("window_minutes")
    val windowMinutes: Double? = null,
    val status: String? = null,
    val annuli: List<ObservedFlashAnnulus>? = null,
    @SerialName("along_route_flash_count")
    val alongRouteFlashCount: Int? = null,
    @SerialName("along_route_nearest_nm")
    val alongRouteNearestNm: Double? = null,
    @SerialName("along_route_nearest_segment_index")
    val alongRouteNearestSegmentIndex: Int? = null,
    @SerialName("along_route_trend")
    val alongRouteTrend: String? = null,
)

@Serializable
data class ObservedStationRef(
    val icao: String = "",
    val name: String? = null,
    val role: String? = null,
    val lat: Double? = null,
    val lon: Double? = null,
    val radar: ObservedField? = null,
    @SerialName("cloud_tops")
    val cloudTops: ObservedTopsField? = null,
    val lightning: ObservedFlashField? = null,
    val summary: String? = null,
    val level: String? = null,
    @SerialName("metar_consistency")
    val metarConsistency: String? = null,
    @SerialName("metar_consistency_note")
    val metarConsistencyNote: String? = null,
)

@Serializable
data class ObservedSummaryEntry(
    val text: String = "",
    val level: String? = null,
    val category: String? = null,
    val target: String? = null,
)

@Serializable
data class ObservedSourceStatus(
    val source: String = "",
    val available: Boolean = true,
    val reason: String? = null,
    @SerialName("latest_valid_time")
    val latestValidTime: String? = null,
    @SerialName("covered_fraction")
    val coveredFraction: Double? = null,
    val status: String = "",
    @SerialName("valid_time")
    val validTime: String? = null,
    @SerialName("age_minutes")
    val ageMinutes: Double? = null,
)

@Serializable
data class ObservedAttribution(
    val id: String = "",
    val name: String = "",
    val producer: String? = null,
    val license: String? = null,
    val url: String? = null,
    val text: String? = null,
)

@Serializable
data class ObservedConditions(
    @SerialName("computed_at")
    val computedAt: String? = null,
    @SerialName("fetch_time")
    val fetchTime: String? = null,
    @SerialName("corridor_nm")
    val corridorNm: Double? = null,
    @SerialName("radii_nm")
    val radiiNm: List<Double>? = null,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int? = null,
    val reflectivity: ObservedField? = null,
    @SerialName("rain_rate")
    val rainRate: ObservedField? = null,
    val radar: ObservedField? = null,
    @SerialName("cloud_tops")
    val cloudTops: ObservedTopsField? = null,
    val lightning: ObservedFlashField? = null,
    val stations: List<ObservedStationRef>? = null,
    @Serializable(with = FlexibleStringListSerializer::class)
    val summary: List<String>? = null,
    @SerialName("summary_lines")
    val summaryLines: List<String>? = null,
    @SerialName("summary_entries")
    val summaryEntries: List<ObservedSummaryEntry>? = null,
    val sources: List<ObservedSourceStatus>? = null,
    val attribution: List<ObservedAttribution>? = null,
    @SerialName("has_any_field")
    val hasAnyField: Boolean? = null,
)

@Serializable
data class ObservedImageryFrame(
    val timestamp: String = "",
    @SerialName("valid_time")
    val validTime: String = "",
)

@Serializable
data class ObservedFramesResponse(
    val frames: List<ObservedImageryFrame> = emptyList(),
)

@Serializable
data class ObservedImageryStatusResponse(
    val radar: ObservedSourceStatus? = null,
    @SerialName("cloud_tops")
    val cloudTops: ObservedSourceStatus? = null,
    val lightning: ObservedSourceStatus? = null,
    val attribution: List<ObservedAttribution>? = null,
)

// =============================================================================
// SYNC — `GET /api/observed/flashes` (src/weatherbrief/api/observed.py) and the
// iOS `Models/API/ObservedFlashes.swift`. Online only: the route map asks while
// it is on screen, nothing is cached to disk.
// =============================================================================

/** One lightning flash with its own time, so the map can fade it by age. */
@Serializable
data class ObservedFlash(
    val lat: Double,
    val lon: Double,
    /** ISO-8601 instant of the flash itself (not of the frame it came in). */
    val time: String,
)

/** Lightning inside a box: every retained flash (or the last `minutes`). */
@Serializable
data class ObservedFlashesResponse(
    @Serializable(with = LossyListSerializer.FlashList::class)
    val flashes: List<ObservedFlash> = emptyList(),
    val count: Int? = null,
    /** The newest lightning frame's valid time; null when no frame is held. */
    @SerialName("newest_valid_time")
    val newestValidTime: String? = null,
    /** The product's own accumulation window (10 min for MTG LI). */
    @SerialName("window_minutes")
    val windowMinutes: Double? = null,
    @SerialName("retention_minutes")
    val retentionMinutes: Double? = null,
    /** `{}` when no frame is held: every field optional. */
    val attribution: ObservedAttribution? = null,
)

// =============================================================================
// SYNC — `src/weatherbrief/observed/cells/display.py` (DISPLAY_SCHEMA
// `observed-cells-display/1`), `observed/cells_display.py::frames_status`, and
// the iOS `Models/API/ObservedCells.swift`. Every number is nullable: the node
// writes rounded floats and nulls freely, and a missing value must read as "–",
// never as zero.
// =============================================================================

/** `/api/observed/cells/frames`: overlay stamps newest first plus the stale state. */
@Serializable
data class CellFramesResponse(
    /** False when the server runs without `WB_CELLS_INGEST_ENABLED`: an answer, not an error. */
    val enabled: Boolean = false,
    val frames: List<CellFrame> = emptyList(),
    val newest: CellFrame? = null,
    val stale: Boolean = true,
    /** Shipped by the server so the client never hard-codes the threshold. */
    @SerialName("stale_after_minutes")
    val staleAfterMinutes: Double = 25.0,
    @SerialName("unavailable_since")
    val unavailableSince: String? = null,
    /** `/api/observed/cells/{stamp}.json`: `{stamp}` takes a frame's [CellFrame.displayKey]. */
    @SerialName("url_template")
    val urlTemplate: String = "/api/observed/cells/{stamp}.json",
)

@Serializable
data class CellFrame(
    /** `YYYYMMDDTHHMM`: string order is time order. */
    val stamp: String,
    @SerialName("valid_time")
    val validTime: String,
    @SerialName("received_at")
    val receivedAt: String? = null,
    @SerialName("age_minutes")
    val ageMinutes: Double? = null,
    /** `<stamp>.r<n>`: the frame's newest revision (#666). Null from a server older than revisions. */
    val key: String? = null,
    val revision: Int? = null,
) {
    /** What goes into the URL: the newest revision (immutable), else the bare stamp. */
    val displayKey: String get() = key ?: stamp
}

/**
 * One display file: one radar frame's cells, outlines and the frame's own times.
 *
 * Decoded leniently, as on iOS: `cells` is decoded element by element, so a cell missing a
 * required field is dropped, not the whole overlay.
 */
@Serializable
data class CellDisplay(
    val schema: String? = null,
    @SerialName("policy_version")
    val policyVersion: String? = null,
    @SerialName("code_revision")
    val codeRevision: String? = null,
    @SerialName("valid_time")
    val validTime: String? = null,
    @SerialName("window_minutes")
    val windowMinutes: Double? = null,
    val times: CellDisplayTimes? = null,
    /** Inputs still on their way (`["lightning"]` until its frame lands, #666): "pending", never "none". */
    val pending: List<String>? = null,
    val unavailable: List<CellUnavailable>? = null,
    /** 0 when first published, 1 once its lightning landed (#666). */
    val revision: Int? = null,
    @SerialName("rain_min_area_km2")
    val rainMinAreaKm2: Double? = null,
    @SerialName("arrow_minutes")
    val arrowMinutes: Double? = null,
    /** Tier (`rain20` / `core35` / `core41`) → polylines, each `[[lat, lon], …]`. */
    val outlines: Map<String, List<List<List<Double>>>>? = null,
    @Serializable(with = LossyListSerializer.CellList::class)
    val cells: List<DisplayCell> = emptyList(),
)

@Serializable
data class CellDisplayTimes(
    val radar: String? = null,
    val rate: String? = null,
    val lightning: String? = null,
    @SerialName("cloud_top")
    val cloudTop: String? = null,
)

@Serializable
data class CellUnavailable(
    val what: String,
    val reason: String? = null,
)

@Serializable
data class DisplayCell(
    val id: String,
    /** `rain20` | `core35` | `core41` */
    val tier: String,
    val lat: Double,
    val lon: Double,
    @SerialName("area_km2")
    val areaKm2: Double? = null,
    @SerialName("peak_dbz")
    val peakDbz: Double? = null,
    @SerialName("rate_peak_mm_h")
    val ratePeakMmH: Double? = null,
    val flashes: Double? = null,
    /** True while the frame's lightning has not landed yet (#666). */
    @SerialName("flashes_pending")
    val flashesPending: Boolean? = null,
    /** The rain rate's own time when it is older than the radar (#666). */
    @SerialName("rate_as_of")
    val rateAsOf: String? = null,
    @SerialName("top_fl")
    val topFl: Double? = null,
    val truncated: Boolean? = null,
    @SerialName("age_min")
    val ageMin: Double? = null,
    val event: String? = null,
    val trend: CellTrend? = null,
    val motion: CellMotion? = null,
    /** `[lat, lon]` after `arrow_minutes` at the current motion; null unless the motion is `available`. */
    val arrow: List<Double>? = null,
) {
    val isCore: Boolean get() = tier != "rain20"
}

@Serializable
data class CellTrend(
    /** developing | decaying | steady | mixed | new (others possible) */
    val state: String? = null,
    @SerialName("window_min")
    val windowMin: Double? = null,
    @SerialName("d_peak_db")
    val dPeakDb: Double? = null,
    @SerialName("area_ratio")
    val areaRatio: Double? = null,
    @SerialName("d_flashes")
    val dFlashes: Double? = null,
)

@Serializable
data class CellMotion(
    /** available | withheld | unsupported | no_pair (others possible) */
    val status: String? = null,
    val reason: String? = null,
    @SerialName("speed_kt")
    val speedKt: Double? = null,
    @SerialName("toward_deg")
    val towardDeg: Double? = null,
)

/** A list decoded element by element: an element that does not decode is skipped, not the list. */
open class LossyListSerializer<T>(private val element: KSerializer<T>) : KSerializer<List<T>> {
    override val descriptor: SerialDescriptor = ListSerializer(element).descriptor

    override fun deserialize(decoder: Decoder): List<T> {
        val json = decoder as? JsonDecoder ?: return ListSerializer(element).deserialize(decoder)
        val array = json.decodeJsonElement() as? JsonArray ?: return emptyList()
        return array.mapNotNull { item -> runCatching { json.json.decodeFromJsonElement(element, item) }.getOrNull() }
    }

    override fun serialize(encoder: Encoder, value: List<T>) {
        ListSerializer(element).serialize(encoder, value)
    }

    object CellList : LossyListSerializer<DisplayCell>(DisplayCell.serializer())
    object FlashList : LossyListSerializer<ObservedFlash>(ObservedFlash.serializer())
}

// ============================================================================
// Route Analyses (cross-section + model comparison)
// ============================================================================

@Serializable
data class RouteNightInterval(
    @SerialName("start_distance_nm")
    val startDistanceNm: Double = 0.0,
    @SerialName("end_distance_nm")
    val endDistanceNm: Double = 0.0,
    val phase: String = "night",
)

@Serializable
data class RouteSunAnalysis(
    @SerialName("night_intervals")
    val nightIntervals: List<RouteNightInterval> = emptyList(),
)

@Serializable
data class RouteAnalysesResponse(
    @SerialName("route_name")
    val routeName: String = "",
    @SerialName("target_date")
    val targetDate: String = "",
    @SerialName("departure_time")
    val departureTime: String = "",
    @SerialName("flight_duration_hours")
    val flightDurationHours: Double = 0.0,
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double = 0.0,
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int = 8000,
    @SerialName("flight_ceiling_ft")
    val flightCeilingFt: Int? = null,
    val models: List<String> = emptyList(),
    val analyses: List<RoutePointAnalysis> = emptyList(),
    val sun: RouteSunAnalysis? = null,
)

@Serializable
data class RoutePointAnalysis(
    @SerialName("point_index")
    val pointIndex: Int = 0,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("distance_from_origin_nm")
    val distanceFromOriginNm: Double = 0.0,
    @SerialName("waypoint_icao")
    val waypointIcao: String? = null,
    @SerialName("waypoint_name")
    val waypointName: String? = null,
    @SerialName("interpolated_time")
    val interpolatedTime: String = "",
    @SerialName("forecast_hour")
    val forecastHour: String = "",
    @SerialName("track_deg")
    val trackDeg: Double = 0.0,
    @SerialName("wind_components")
    val windComponents: Map<String, WindComponent> = emptyMap(),
    val sounding: Map<String, SoundingAnalysis> = emptyMap(),
    @SerialName("model_divergence")
    val modelDivergence: List<ModelDivergence> = emptyList(),
    @SerialName("cruise_temperature_c")
    val cruiseTemperatureC: Map<String, Double>? = null,
) {
    val id: Int
        get() = pointIndex
}

@Serializable
data class SldZone(
    @SerialName("base_ft")
    val baseFt: Double = 0.0,
    @SerialName("top_ft")
    val topFt: Double = 0.0,
    val risk: String = "light",
    val mechanism: String? = null,
)

@Serializable
data class SoundingAnalysis(
    val indices: ThermodynamicIndices? = null,
    @SerialName("cloud_layers")
    val cloudLayers: List<EnhancedCloudLayer>? = null,
    @SerialName("nwp_cloud_layers")
    val nwpCloudLayers: List<EnhancedCloudLayer>? = null,
    @SerialName("icing_zones")
    val icingZones: List<IcingZone>? = null,
    @SerialName("icing_ogimet_nwp_zones")
    val icingOgimetNwpZones: List<IcingZone>? = null,
    @SerialName("sfip_zones")
    val sfipZones: List<SfipZone>? = null,
    @SerialName("ieng_icing_zones")
    val iengIcingZones: List<IcingZone>? = null,
    @SerialName("sld_zones")
    val sldZones: List<SldZone>? = null,
    @SerialName("inversion_layers")
    val inversionLayers: List<InversionLayer>? = null,
    val convective: ConvectiveAssessment? = null,
    @SerialName("convective_nwp")
    val convectiveNwp: ConvectiveAssessment? = null,
    @SerialName("vertical_motion")
    val verticalMotion: VerticalMotionAssessment? = null,
    @SerialName("cloud_cover_low_pct")
    val cloudCoverLowPct: Double? = null,
    @SerialName("cloud_cover_mid_pct")
    val cloudCoverMidPct: Double? = null,
    @SerialName("cloud_cover_high_pct")
    val cloudCoverHighPct: Double? = null,
    @SerialName("nwp_cloud_diagnostics")
    val nwpCloudDiagnostics: NWPCloudDiagnostics? = null,
)

@Serializable
data class NWPCloudDiagnostics(
    val low: NWPCloudLayerDiag = NWPCloudLayerDiag(),
    val mid: NWPCloudLayerDiag = NWPCloudLayerDiag(),
    val high: NWPCloudLayerDiag = NWPCloudLayerDiag(),
    @SerialName("ceiling_ft")
    val ceilingFt: Double? = null,
)

@Serializable
data class NWPCloudLayerDiag(
    @SerialName("cover_pct")
    val coverPct: Double? = null,
    @SerialName("base_ft")
    val baseFt: Double? = null,
    @SerialName("top_ft")
    val topFt: Double? = null,
)

@Serializable
data class ThermodynamicIndices(
    @SerialName("lcl_altitude_ft")
    val lclAltitudeFt: Double? = null,
    @SerialName("lcl_pressure_hpa")
    val lclPressureHpa: Double? = null,
    @SerialName("lfc_altitude_ft")
    val lfcAltitudeFt: Double? = null,
    @SerialName("lfc_pressure_hpa")
    val lfcPressureHpa: Double? = null,
    @SerialName("el_altitude_ft")
    val elAltitudeFt: Double? = null,
    @SerialName("el_pressure_hpa")
    val elPressureHpa: Double? = null,
    @SerialName("cape_surface_jkg")
    val capeSurfaceJkg: Double? = null,
    @SerialName("cin_surface_jkg")
    val cinSurfaceJkg: Double? = null,
    @SerialName("lifted_index")
    val liftedIndex: Double? = null,
    @SerialName("k_index")
    val kIndex: Double? = null,
    @SerialName("total_totals")
    val totalTotals: Double? = null,
    @SerialName("precipitable_water_mm")
    val precipitableWaterMm: Double? = null,
    @SerialName("freezing_level_ft")
    val freezingLevelFt: Double? = null,
    @SerialName("minus10c_level_ft")
    val minus10cLevelFt: Double? = null,
    @SerialName("minus20c_level_ft")
    val minus20cLevelFt: Double? = null,
    @SerialName("bulk_shear_0_6km_kt")
    val bulkShear06kmKt: Double? = null,
    @SerialName("bulk_shear_0_1km_kt")
    val bulkShear01kmKt: Double? = null,
    @SerialName("sounding_ceiling_ft")
    val soundingCeilingFt: Double? = null,
    @SerialName("nwp_ceiling_ft")
    val nwpCeilingFt: Double? = null,
)

typealias SoundingIndicesResponse = ThermodynamicIndices

@Serializable
data class EnhancedCloudLayer(
    @SerialName("base_ft")
    val baseFt: Double = 0.0,
    @SerialName("top_ft")
    val topFt: Double = 0.0,
    @SerialName("base_pressure_hpa")
    val basePressureHpa: Double? = null,
    @SerialName("top_pressure_hpa")
    val topPressureHpa: Double? = null,
    @SerialName("thickness_ft")
    val thicknessFt: Double? = null,
    val coverage: String? = null,
    @SerialName("mean_dewpoint_depression_c")
    val meanDewpointDepressionC: Double? = null,
    @SerialName("mean_cloud_cover_pct")
    val meanCloudCoverPct: Double? = null,
)

@Serializable
data class IcingZone(
    @SerialName("base_ft")
    val baseFt: Double = 0.0,
    @SerialName("top_ft")
    val topFt: Double = 0.0,
    @SerialName("base_pressure_hpa")
    val basePressureHpa: Double? = null,
    @SerialName("top_pressure_hpa")
    val topPressureHpa: Double? = null,
    val risk: String = "none",
    @SerialName("icing_type")
    val icingType: String = "none",
    @SerialName("mean_temperature_c")
    val meanTemperatureC: Double? = null,
    @SerialName("mean_rh_pct")
    val meanRhPct: Double? = null,
    @SerialName("mean_icing_index")
    val meanIcingIndex: Double? = null,
)

@Serializable
@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
data class SfipZone(
    @SerialName("base_ft")
    val baseFt: Double = 0.0,
    @SerialName("top_ft")
    val topFt: Double = 0.0,
    @SerialName("base_pressure_hpa")
    val basePressureHpa: Double? = null,
    @SerialName("top_pressure_hpa")
    val topPressureHpa: Double? = null,
    val risk: String = "none",
    @SerialName("icing_type")
    val icingType: String = "none",
    @SerialName("mean_sfip_100")
    @kotlinx.serialization.json.JsonNames("mean_sfip100")
    val meanSfip100: Double? = null,
    @SerialName("mean_temperature_c")
    val meanTemperatureC: Double? = null,
    @SerialName("mean_rh_pct")
    val meanRhPct: Double? = null,
    val variant: String? = null,
)

@Serializable
data class InversionLayer(
    @SerialName("base_ft")
    val baseFt: Double = 0.0,
    @SerialName("top_ft")
    val topFt: Double = 0.0,
    @SerialName("base_pressure_hpa")
    val basePressureHpa: Double? = null,
    @SerialName("top_pressure_hpa")
    val topPressureHpa: Double? = null,
    @SerialName("strength_c")
    val strengthC: Double = 0.0,
    @SerialName("base_temp_c")
    val baseTempC: Double? = null,
    @SerialName("top_temp_c")
    val topTempC: Double? = null,
    @SerialName("surface_based")
    val surfaceBased: Boolean? = null,
)

@Serializable
data class ConvectiveAssessment(
    @SerialName("risk_level")
    val riskLevel: String = "none",
    @SerialName("cape_jkg")
    val capeJkg: Double? = null,
    @SerialName("cin_jkg")
    val cinJkg: Double? = null,
    @SerialName("lcl_ft")
    val lclFt: Double? = null,
    @SerialName("lfc_ft")
    val lfcFt: Double? = null,
    @SerialName("el_ft")
    val elFt: Double? = null,
    @SerialName("base_ft")
    val baseFt: Double? = null,
    @SerialName("top_ft")
    val topFt: Double? = null,
    @SerialName("cover_pct")
    val coverPct: Double? = null,
    @SerialName("bulk_shear_0_6km_kt")
    val bulkShear06kmKt: Double? = null,
    @SerialName("lifted_index")
    val liftedIndex: Double? = null,
    @SerialName("k_index")
    val kIndex: Double? = null,
    @SerialName("total_totals")
    val totalTotals: Double? = null,
    @SerialName("severe_modifiers")
    val severeModifiers: List<String>? = null,
    val method: String? = null,
)

@Serializable
data class VerticalMotionAssessment(
    val classification: String = "unavailable",
    @SerialName("max_omega_pa_s")
    val maxOmegaPaS: Double? = null,
    @SerialName("max_w_fpm")
    val maxWFpm: Double? = null,
    @SerialName("max_w_level_ft")
    val maxWLevelFt: Double? = null,
    @SerialName("cat_risk_layers")
    val catRiskLayers: List<CATRiskLayer>? = null,
    @SerialName("e_shear_layers")
    val eShearLayers: List<CATRiskLayer>? = null,
)

@Serializable
data class CATRiskLayer(
    @SerialName("base_ft")
    val baseFt: Double = 0.0,
    @SerialName("top_ft")
    val topFt: Double = 0.0,
    @SerialName("base_pressure_hpa")
    val basePressureHpa: Double? = null,
    @SerialName("top_pressure_hpa")
    val topPressureHpa: Double? = null,
    @SerialName("richardson_number")
    val richardsonNumber: Double? = null,
    @SerialName("wind_shear_kt_per_1000ft")
    val windShearKtPer1000ft: Double? = null,
    val risk: String = "none",
)

@Serializable
data class ModelDivergence(
    val variable: String = "",
    @SerialName("model_values")
    val modelValues: Map<String, Double> = emptyMap(),
    val mean: Double = 0.0,
    val spread: Double = 0.0,
    val agreement: String = "good",
)

// ============================================================================
// Elevation Profile
// ============================================================================

@Serializable
data class ElevationResponse(
    @SerialName("route_name")
    val routeName: String = "",
    val points: List<ElevationPoint> = emptyList(),
    @SerialName("max_elevation_ft")
    val maxElevationFt: Double = 0.0,
    @SerialName("total_distance_nm")
    val totalDistanceNm: Double = 0.0,
)

@Serializable
data class ElevationPoint(
    @SerialName("distance_nm")
    val distanceNm: Double = 0.0,
    @SerialName("elevation_ft")
    val elevationFt: Double = 0.0,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
)

// ============================================================================
// Sounding Profile (Skew-T)
// ============================================================================

@Serializable
data class SoundingProfileResponse(
    @SerialName("point_index")
    val pointIndex: Int = 0,
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("distance_from_origin_nm")
    val distanceFromOriginNm: Double = 0.0,
    @SerialName("waypoint_icao")
    val waypointIcao: String? = null,
    @SerialName("waypoint_name")
    val waypointName: String? = null,
    @SerialName("interpolated_time")
    val interpolatedTime: String = "",
    val model: String = "",
    @SerialName("cruise_altitude_ft")
    val cruiseAltitudeFt: Int = 8000,
    @SerialName("track_deg")
    val trackDeg: Double? = null,
    @SerialName("terrain_elevation_ft")
    val terrainElevationFt: Double? = null,
    val levels: List<SoundingProfileLevel> = emptyList(),
    @SerialName("parcel_path")
    val parcelPath: List<ParcelPoint>? = null,
    val indices: ThermodynamicIndices? = null,
    @SerialName("cloud_layers")
    val cloudLayers: List<EnhancedCloudLayer>? = null,
    @SerialName("nwp_cloud_layers")
    val nwpCloudLayers: List<EnhancedCloudLayer>? = null,
    @SerialName("icing_zones")
    val icingZones: List<IcingZone>? = null,
    @SerialName("icing_ogimet_nwp_zones")
    val icingOgimetNwpZones: List<IcingZone>? = null,
    @SerialName("sfip_zones")
    val sfipZones: List<SfipZone>? = null,
    @SerialName("inversion_layers")
    val inversionLayers: List<InversionLayer>? = null,
    val convective: ConvectiveAssessment? = null,
)

@Serializable
@OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
data class SoundingProfileLevel(
    @SerialName("pressure_hpa")
    val pressureHpa: Double = 0.0,
    @SerialName("altitude_ft")
    val altitudeFt: Double? = null,
    @SerialName("temperature_c")
    val temperatureC: Double = 0.0,
    @SerialName("dewpoint_c")
    val dewpointC: Double? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("wind_direction_deg")
    val windDirectionDeg: Double? = null,
    @SerialName("relative_humidity_pct")
    val relativeHumidityPct: Double? = null,
    @SerialName("dewpoint_depression_c")
    val dewpointDepressionC: Double? = null,
    @SerialName("wet_bulb_c")
    val wetBulbC: Double? = null,
    @SerialName("theta_e_k")
    val thetaEK: Double? = null,
    @SerialName("lapse_rate_c_per_km")
    val lapseRateCPerKm: Double? = null,
    @SerialName("icing_index")
    val icingIndex: Double? = null,
    @SerialName("icing_index_nwp")
    val icingIndexNwp: Double? = null,
    @SerialName("sfip_100")
    @kotlinx.serialization.json.JsonNames("sfip")
    val sfip100: Double? = null,
    @SerialName("cloud_liquid_water_g_kg")
    val cloudLiquidWaterGKg: Double? = null,
    @SerialName("cloud_liquid_water_g_m3")
    val cloudLiquidWaterGM3: Double? = null,
    @SerialName("ice_mixing_ratio_g_kg")
    val iceMixingRatioGKg: Double? = null,
    @SerialName("cloud_area_fraction_pct")
    val cloudAreaFractionPct: Double? = null,
    @SerialName("richardson_number")
    val richardsonNumber: Double? = null,
    @SerialName("vertical_velocity_pa_s")
    val verticalVelocityPaS: Double? = null,
    @SerialName("omega_pa_s")
    val omegaPaS: Double? = null,
    @SerialName("w_fpm")
    val wFpm: Double? = null,
)

@Serializable
data class ParcelPoint(
    @SerialName("pressure_hpa")
    val pressureHpa: Double = 0.0,
    @SerialName("temperature_c")
    val temperatureC: Double = 0.0,
)

typealias ParcelPathPointResponse = ParcelPoint

// ============================================================================
// Alternates
// ============================================================================

@Serializable
data class AlternatesResponse(
    @SerialName("weather_picks")
    val weatherPicks: List<AlternateAxisPick>? = null,
    val candidates: List<AlternateAirport> = emptyList(),
    @SerialName("destination_icao")
    val destinationIcao: String? = null,
    @SerialName("search_radius_nm")
    val searchRadiusNm: Double? = null,
    @SerialName("eval_hour")
    val evalHour: String? = null,
    val requirement: AlternateRequirement? = null,
)

typealias RouteAlternates = AlternatesResponse

@Serializable
data class AlternateAirport(
    val icao: String = "",
    val name: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("elevation_ft")
    val elevationFt: Int? = null,
    @SerialName("distance_to_dest_nm")
    val distanceToDestNm: Double = 0.0,
    @SerialName("bearing_from_dest_deg")
    val bearingFromDestDeg: Double = 0.0,
    val axis: String = "",
    @SerialName("is_enroute")
    val isEnroute: Boolean = false,
    @SerialName("worst_category")
    val worstCategory: String = "",
    @SerialName("ceiling_ft")
    val ceilingFt: Int? = null,
    @SerialName("visibility_sm")
    val visibilitySm: Double? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("wind_direction_deg")
    val windDirectionDeg: Double? = null,
    @SerialName("wind_gust_kt")
    val windGustKt: Double? = null,
    @SerialName("best_runway")
    val bestRunway: RunwayWind? = null,
    val trend: String = "steady",
    @SerialName("eval_plus1h_category")
    val evalPlus1hCategory: String? = null,
    @SerialName("per_model_categories")
    val perModelCategories: Map<String, String> = emptyMap(),
    val status: String = "unavailable",
    val summary: String = "",
    @SerialName("longest_runway_ft")
    val longestRunwayFt: Int? = null,
    @SerialName("has_hard_surface")
    val hasHardSurface: Boolean = false,
    @SerialName("has_lighted_runway")
    val hasLightedRunway: Boolean = false,
    @SerialName("has_fuel")
    val hasFuel: Boolean = false,
    @SerialName("is_border_entry")
    val isBorderEntry: Boolean = false,
    @SerialName("has_metar")
    val hasMetar: Boolean = false,
    @SerialName("has_taf")
    val hasTaf: Boolean = false,
    @SerialName("taf_category_at_eta")
    val tafCategoryAtEta: String? = null,
    @SerialName("taf_ceiling_ft")
    val tafCeilingFt: Int? = null,
    @SerialName("taf_visibility_sm")
    val tafVisibilitySm: Double? = null,
    @SerialName("metar_category")
    val metarCategory: String? = null,
    @SerialName("obs_proxy")
    val obsProxy: ObsProxy? = null,
    @SerialName("operational_flags")
    val operationalFlags: List<OperationalFlag> = emptyList(),
    @SerialName("score_breakdown")
    val scoreBreakdown: Map<String, Double> = emptyMap(),
    val score: Double = 0.0,
    val qual: AlternateQual? = null,
) {
    val id: String
        get() = icao

    val resolvedStatus: Assessment
        get() = Assessment.fromString(status)
}

@Serializable
data class OperationalFlag(
    val key: String = "",
    val label: String = "",
    val status: String = "",
    val detail: String = "",
)

@Serializable
data class AlternateAxisPick(
    val axis: String = "",
    val label: String = "",
    val airport: AlternateAirport,
) {
    val id: String
        get() = "${axis}_${airport.icao}"
}

@Serializable
data class AlternateQual(
    @SerialName("faa_standard")
    val faaStandard: Boolean? = null,
    @SerialName("easa_standard")
    val easaStandard: Boolean? = null,
    @SerialName("taf_verified")
    val tafVerified: Boolean? = null,
    @SerialName("ceiling_margin_ft")
    val ceilingMarginFt: Int? = null,
    @SerialName("vis_margin_m")
    val visMarginM: Int? = null,
    val disqualifier: String? = null,
)

@Serializable
data class AlternateRequirement(
    @SerialName("eval_window_start")
    val evalWindowStart: String = "",
    @SerialName("eval_window_end")
    val evalWindowEnd: String = "",
    @SerialName("worst_ceiling_ft")
    val worstCeilingFt: Int? = null,
    @SerialName("worst_visibility_sm")
    val worstVisibilitySm: Double? = null,
    @SerialName("worst_visibility_m")
    val worstVisibilityM: Int? = null,
    @SerialName("worst_category")
    val worstCategory: String? = null,
    @SerialName("has_ts_risk")
    val hasTsRisk: Boolean = false,
    @SerialName("taf_in_window")
    val tafInWindow: Boolean = false,
    val faa: RegAlternateTrigger? = null,
    val easa: RegAlternateTrigger? = null,
)

@Serializable
data class RegAlternateTrigger(
    val status: String = "",
    val rule: String = "",
    val reason: String = "",
    @SerialName("ceiling_threshold_ft")
    val ceilingThresholdFt: Int = 0,
    @SerialName("visibility_threshold")
    val visibilityThreshold: String = "",
)

// ============================================================================
// Flexible Departure / Time Explorer
// ============================================================================

@Serializable
enum class TimeScanJobStatus(val wireValue: String) {
    @SerialName("pending")
    PENDING("pending"),

    @SerialName("running")
    RUNNING("running"),

    @SerialName("ready")
    READY("ready"),

    @SerialName("failed")
    FAILED("failed"),
}

@Serializable
data class TimeScanStatusDTO(
    val status: TimeScanJobStatus = TimeScanJobStatus.PENDING,
    @SerialName("scanned_at")
    val scannedAt: String? = null,
    @SerialName("candidate_count")
    val candidateCount: Int = 0,
    @SerialName("best_departure_time")
    val bestDepartureTime: String? = null,
    @SerialName("best_assessment")
    val bestAssessment: String? = null,
    @SerialName("best_red_count")
    val bestRedCount: Int? = null,
    @SerialName("best_amber_count")
    val bestAmberCount: Int? = null,
    @SerialName("current_assessment")
    val currentAssessment: String? = null,
    @SerialName("current_red_count")
    val currentRedCount: Int? = null,
    @SerialName("current_amber_count")
    val currentAmberCount: Int? = null,
    val summary: String? = null,
    val error: String? = null,
)

@Serializable
enum class TimeConfidence(val wireValue: String) {
    @SerialName("low")
    LOW("low"),

    @SerialName("medium")
    MEDIUM("medium"),

    @SerialName("high")
    HIGH("high"),
}

@Serializable
data class TimeConfirmationDTO(
    val show: Boolean = false,
    @SerialName("days_out")
    val daysOut: Int = 0,
    val confidence: TimeConfidence = TimeConfidence.LOW,
    val headline: String = "",
    val detail: String = "",
    @SerialName("recommended_departure_time")
    val recommendedDepartureTime: String? = null,
    @SerialName("recommended_is_current")
    val recommendedIsCurrent: Boolean = false,
)

@Serializable
data class TimeCandidate(
    @SerialName("departure_time")
    val departureTime: String = "",
    @SerialName("target_date")
    val targetDate: String = "",
    @SerialName("target_hour_utc")
    val targetHourUtc: Int = 0,
    @SerialName("is_current")
    val isCurrent: Boolean = false,
    val rank: Int = 0,
    val score: Double = 0.0,
    val assessment: String = "unavailable",
    @SerialName("advisory_summary")
    val advisorySummary: AdvisorySummary = AdvisorySummary(),
    val advisories: List<RouteAdvisoryResult> = emptyList(),
    @SerialName("models_available")
    val modelsAvailable: List<String> = emptyList(),
    @SerialName("models_missing")
    val modelsMissing: List<String> = emptyList(),
    @SerialName("pros_vs_current")
    val prosVsCurrent: List<String> = emptyList(),
    @SerialName("cons_vs_current")
    val consVsCurrent: List<String> = emptyList(),
    val note: String? = null,
    @SerialName("airport_conditions")
    val airportConditions: AirportConditions? = null,
) {
    val id: String
        get() = departureTime

    val resolvedAssessment: Assessment
        get() = Assessment.fromString(assessment)
}

typealias TimeCandidateDTO = TimeCandidate

@Serializable
data class TimeWindowDTO(
    val mode: FlexibilityMode = FlexibilityMode.FIXED,
    val days: Int = 1,
    @SerialName("from_utc")
    val fromUtc: Int = 6,
    @SerialName("to_utc")
    val toUtc: Int = 18,
    @SerialName("step_hours")
    val stepHours: Int = 3,
)

@Serializable
data class TimeBaselineDTO(
    @SerialName("departure_time")
    val departureTime: String = "",
    val assessment: String = "unavailable",
    @SerialName("red_count")
    val redCount: Int = 0,
    @SerialName("amber_count")
    val amberCount: Int = 0,
    @SerialName("green_count")
    val greenCount: Int = 0,
    val score: Double = 0.0,
)

@Serializable
data class TimeWindowScanDTO(
    val window: TimeWindowDTO = TimeWindowDTO(),
    val baseline: TimeBaselineDTO? = null,
    @SerialName("recommended_departure_time")
    val recommendedDepartureTime: String? = null,
    val summary: String = "",
    val candidates: List<TimeCandidate> = emptyList(),
)

@Serializable
data class TimeOptionsResponse(
    @SerialName("flight_id")
    val flightId: String = "",
    val status: TimeScanStatusDTO = TimeScanStatusDTO(),
    val confirmation: TimeConfirmationDTO = TimeConfirmationDTO(),
    val scan: TimeWindowScanDTO? = null,
)

@Serializable
data class ConfirmTimeOptionRequest(
    @SerialName("departure_time")
    val departureTime: String,
    @SerialName("lock_to_fixed")
    val lockToFixed: Boolean = true,
    @SerialName("refresh_now")
    val refreshNow: Boolean = true,
)

// ============================================================================
// Airport Weather & Forecast / Historical Maps
// ============================================================================

@Serializable
data class AirportWeatherResponse(
    val icao: String = "",
    val name: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("elevation_ft")
    val elevationFt: Int? = null,
    @SerialName("runway_ends")
    val runwayEnds: List<RunwayEnd> = emptyList(),
    @SerialName("forecast_day")
    val forecastDay: Int = 0,
    val date: String = "",
    @SerialName("model_init_times")
    val modelInitTimes: Map<String, String> = emptyMap(),
    val hours: List<AirportHourEntry> = emptyList(),
    @SerialName("day_summary")
    val daySummary: Map<String, AirportWeatherDaySummary>? = null,
)

@Serializable
data class AirportHourEntry(
    @SerialName("hour_utc")
    val hourUtc: Int = 0,
    val consensus: Map<String, String>? = null,
    val models: Map<String, AirportWeatherModelEntry> = emptyMap(),
) {
    val id: Int
        get() = hourUtc
}

@Serializable
data class AirportWeatherModelEntry(
    val category: String? = null,
    @SerialName("wind_dir")
    val windDir: Double? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("wind_gust_kt")
    val windGustKt: Double? = null,
    @SerialName("crosswind_kt")
    val crosswindKt: Double? = null,
    @SerialName("headwind_kt")
    val headwindKt: Double? = null,
    @SerialName("best_runway")
    val bestRunway: String? = null,
    @SerialName("all_runways")
    val allRunways: List<RunwayWind>? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Double? = null,
    @SerialName("lcl_ft")
    val lclFt: Double? = null,
    @SerialName("visibility_m")
    val visibilityM: Double? = null,
    @SerialName("cape_jkg")
    val capeJkg: Double? = null,
    @SerialName("convective_risk")
    val convectiveRisk: String? = null,
    @SerialName("cloud_cover_pct")
    val cloudCoverPct: Double? = null,
    @SerialName("cloud_low_pct")
    val cloudLowPct: Double? = null,
    @SerialName("cloud_mid_pct")
    val cloudMidPct: Double? = null,
    @SerialName("cloud_high_pct")
    val cloudHighPct: Double? = null,
    @SerialName("precip_mm")
    val precipMm: Double? = null,
    @SerialName("rain_mm")
    val rainMm: Double? = null,
    @SerialName("snowfall_cm")
    val snowfallCm: Double? = null,
    @SerialName("freezing_level_ft")
    val freezingLevelFt: Double? = null,
    @SerialName("temperature_c")
    val temperatureC: Double? = null,
    @SerialName("dewpoint_c")
    val dewpointC: Double? = null,
    @SerialName("pressure_hpa")
    val pressureHpa: Double? = null,
    @SerialName("weather_code")
    val weatherCode: Int? = null,
    @SerialName("taf_applicable_lines")
    val tafApplicableLines: List<Int>? = null,
)

@Serializable
data class AirportWeatherDaySummary(
    @SerialName("worst_category")
    val worstCategory: String? = null,
    @SerialName("max_crosswind_kt")
    val maxCrosswindKt: Double? = null,
    @SerialName("max_wind_speed_kt")
    val maxWindSpeedKt: Double? = null,
    @SerialName("max_wind_gust_kt")
    val maxWindGustKt: Double? = null,
    @SerialName("min_ceiling_ft")
    val minCeilingFt: Double? = null,
    @SerialName("max_cape_jkg")
    val maxCapeJkg: Double? = null,
    @SerialName("worst_convective_risk")
    val worstConvectiveRisk: String? = null,
    @SerialName("total_precip_mm")
    val totalPrecipMm: Double? = null,
)

@Serializable
data class ForecastDaysResponse(
    val days: List<ForecastDayEntry> = emptyList(),
    @SerialName("max_day")
    val maxDay: Int? = null,
)

/** One day of `/api/maps/forecast/days` (`compute_day_availability` on the server). */
@Serializable
data class ForecastDayEntry(
    val day: Int = 0,
    val date: String = "",
    /** False when the day is beyond the current model horizon (no hours yet). */
    val available: Boolean = true,
    /** Sorted UTC sample hours that have data (plain ints on the wire). */
    val hours: List<Int> = emptyList(),
    /** Models present across those hours. */
    val models: List<String> = emptyList(),
) {
    val id: Int
        get() = day
}
