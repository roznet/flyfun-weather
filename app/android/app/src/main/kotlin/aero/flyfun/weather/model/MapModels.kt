// SYNC: iOS app/flyfun-weather/flyfun-weather/Models/API/ForecastMapResponse.swift
//       iOS app/flyfun-weather/flyfun-weather/Models/API/HistoricalMapResponse.swift
//       server src/weatherbrief/api/maps.py (assemble_map_airports, tasks/historical_map.py)
package aero.flyfun.weather.model

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.descriptors.buildClassSerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

// ============================================================================
// Forecast map (`GET /api/maps/forecast?day=&hour=`)
// ============================================================================

/**
 * Every watchlist airport (~619) with per-model forecasts plus **both** server-baked consensus
 * blocks (worst + majority). The one payload the forecast map colours from: a metric or model
 * switch is a pure recolour, only a day/hour change refetches (`designs/forecast-page.md`).
 */
@Serializable
data class ForecastMapResponse(
    /** Valid time of this slot (ISO8601 UTC). */
    @SerialName("forecast_time")
    val forecastTime: String? = null,
    /** Per-model init time (ISO8601 UTC); keys are a subset of gfs/icon/ecmwf. */
    @SerialName("model_init_times")
    val modelInitTimes: Map<String, String> = emptyMap(),
    val airports: List<ForecastMapAirport> = emptyList(),
)

/** One airport in the forecast map payload. */
@Serializable
data class ForecastMapAirport(
    val icao: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    /** Most-precise approach type, or null when the field has no IAP/nav data. */
    @SerialName("approach_type")
    val approachType: String? = null,
    /** Present models only (ICON is absent on D+5/D+6). Keys: gfs/icon/ecmwf. */
    val models: Map<String, ForecastModelEntry> = emptyMap(),
    /** Worst-mode consensus. */
    val consensus: ForecastConsensus = ForecastConsensus.EMPTY,
    /**
     * Majority-mode consensus. Absent on an older/partial payload: [consensus] stands in, as the
     * web `getConsensus` does.
     */
    @SerialName("consensus_majority")
    val consensusMajorityOrNull: ForecastConsensus? = null,
    /** D-0 METAR/TAF block; carried on the wire, not rendered yet (as on iOS). */
    val observation: ForecastObservation? = null,
) {
    val id: String get() = icao

    val consensusMajority: ForecastConsensus get() = consensusMajorityOrNull ?: consensus

    fun consensus(mode: ForecastModelMode): ForecastConsensus =
        if (mode == ForecastModelMode.Majority) consensusMajority else consensus

    /**
     * The cell the active model/mode reads: a consensus block in a consensus mode, else the
     * individual model entry (null when that model is absent for this airport/hour).
     */
    fun cell(mode: ForecastModelMode): ForecastCellData? = when (mode) {
        ForecastModelMode.Worst -> consensus
        ForecastModelMode.Majority -> consensusMajority
        is ForecastModelMode.Model -> models[mode.name]
    }

    /**
     * Per-model FAA/EASA alternate-required flags reduced for a consensus mode (`aggAltRequired`):
     * worst = any model says yes; majority = modal with a worst tiebreak. Null when no present model
     * carries the flag.
     */
    fun aggregatedAltRequired(mode: ForecastModelMode): AltRequired? {
        val flags = models.values.mapNotNull { it.altRequired }
        if (flags.isEmpty()) return null
        val majority = mode == ForecastModelMode.Majority
        return AltRequired(
            faa = ordinalYes(flags.map { it.faa }, majority),
            easa = ordinalYes(flags.map { it.easa }, majority),
        )
    }

    private fun ordinalYes(bools: List<Boolean>, majority: Boolean): Boolean {
        if (!majority) return bools.any { it }
        val yes = bools.count { it }
        // Modal; ties break to the worst ordinal (yes).
        return yes >= bools.size - yes
    }
}

/**
 * Fields common to a per-model entry and a consensus block, addressed by the catalog's string field
 * names so colour evaluation is data-driven.
 */
interface ForecastCellData {
    /** A banded numeric field (`wind_speed_kt`, `ceiling_ft`, …), null when absent. */
    fun numericField(name: String): Double?

    /** A categorical field (`flight_category` / `convective_risk`). */
    fun categoryField(name: String): String?

    /** FAA/EASA alternate-required flags for this cell, null when unknown. */
    val altRequired: AltRequired?
}

/**
 * One model's forecast for an airport. Numeric fields are nullable; the wind components and
 * `alt_required` are key-absent when unavailable. The historical map's METAR/TAF blocks carry the
 * same keys, and its model entries add `valid_time` / `model_init_time` (their provenance).
 */
@Serializable
data class ForecastModelEntry(
    @SerialName("ceiling_ft")
    val ceilingFt: Double? = null,
    @SerialName("visibility_m")
    val visibilityM: Double? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("wind_dir_deg")
    val windDirDeg: Double? = null,
    @SerialName("wind_gust_kt")
    val windGustKt: Double? = null,
    @SerialName("cloud_cover_pct")
    val cloudCoverPct: Double? = null,
    @SerialName("cape_jkg")
    val capeJkg: Double? = null,
    /** Never null server-side ("none" fallback). */
    @SerialName("convective_risk")
    val convectiveRisk: String? = null,
    @SerialName("temperature_c")
    val temperatureC: Double? = null,
    /** 2 m dewpoint (#948). Absent from responses cached before it was added. */
    @SerialName("dewpoint_c")
    val dewpointC: Double? = null,
    @SerialName("flight_category")
    val flightCategory: String? = null,
    @SerialName("crosswind_kt")
    val crosswindKt: Double? = null,
    @SerialName("headwind_kt")
    val headwindKt: Double? = null,
    @SerialName("best_runway_id")
    val bestRunwayId: String? = null,
    @SerialName("gust_crosswind_kt")
    val gustCrosswindKt: Double? = null,
    @SerialName("gust_headwind_kt")
    val gustHeadwindKt: Double? = null,
    @SerialName("alt_required")
    override val altRequired: AltRequired? = null,
    /** Historical model entries only: the valid time this entry was read at. */
    @SerialName("valid_time")
    val validTime: String? = null,
    /** Historical model entries only: the run this entry came from. */
    @SerialName("model_init_time")
    val modelInitTime: String? = null,
) : ForecastCellData {
    override fun numericField(name: String): Double? = when (name) {
        "wind_speed_kt" -> windSpeedKt
        "crosswind_kt" -> crosswindKt
        "headwind_kt" -> headwindKt
        "ceiling_ft" -> ceilingFt
        "cape_jkg" -> capeJkg
        "visibility_m" -> visibilityM
        "cloud_cover_pct" -> cloudCoverPct
        "wind_dir_deg" -> windDirDeg
        "temperature_c" -> temperatureC
        // Gusts: the card renders these alongside the steady value (as web does)
        "wind_gust_kt" -> windGustKt
        "gust_crosswind_kt" -> gustCrosswindKt
        "gust_headwind_kt" -> gustHeadwindKt
        else -> null
    }

    override fun categoryField(name: String): String? = when (name) {
        "flight_category" -> flightCategory
        "convective_risk" -> convectiveRisk
        else -> null
    }
}

/**
 * Cross-model consensus (worst or majority, both baked server-side). Numeric fields are key-absent
 * when no model supplied the value; `flight_category` and `agreement` are always present.
 */
@Serializable
data class ForecastConsensus(
    @SerialName("flight_category")
    val flightCategory: String? = null,
    /** Field name → "consistent" | "mixed" | "divergent"; variable key set. */
    val agreement: Map<String, String> = emptyMap(),
    @SerialName("convective_risk")
    val convectiveRisk: String? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("ceiling_ft")
    val ceilingFt: Double? = null,
    @SerialName("cape_jkg")
    val capeJkg: Double? = null,
    @SerialName("visibility_m")
    val visibilityM: Double? = null,
    @SerialName("crosswind_kt")
    val crosswindKt: Double? = null,
    @SerialName("headwind_kt")
    val headwindKt: Double? = null,
    @SerialName("cloud_cover_pct")
    val cloudCoverPct: Double? = null,
    @SerialName("wind_dir_deg")
    val windDirDeg: Double? = null,
) : ForecastCellData {
    /** Aggregated from the models, not baked here. */
    override val altRequired: AltRequired? get() = null

    override fun numericField(name: String): Double? = when (name) {
        "wind_speed_kt" -> windSpeedKt
        "crosswind_kt" -> crosswindKt
        "headwind_kt" -> headwindKt
        "ceiling_ft" -> ceilingFt
        "cape_jkg" -> capeJkg
        "visibility_m" -> visibilityM
        "cloud_cover_pct" -> cloudCoverPct
        "wind_dir_deg" -> windDirDeg
        else -> null
    }

    override fun categoryField(name: String): String? = when (name) {
        "flight_category" -> flightCategory
        "convective_risk" -> convectiveRisk
        else -> null
    }

    /** Agreement bucket for a metric (the per-active-metric ring); null when unlabelled. */
    fun agreement(forMetric: String): String? = agreement[agreementKey(forMetric)]

    companion object {
        /**
         * A block with no values: the consensus of a historical airport that has observations but
         * no model data. Only read in a consensus mode, where such airports are never shown.
         */
        val EMPTY = ForecastConsensus()

        /**
         * Which `agreement` key a metric reads ("do the models disagree about the thing you're
         * looking at"). Crosswind and headwind proxy to wind, convective to CAPE (web
         * `metricAgreementKey`).
         */
        fun agreementKey(forMetric: String): String = when (forMetric) {
            "flight_category" -> "flight_category"
            "wind_speed_kt", "crosswind_kt", "headwind_kt" -> "wind_speed_kt"
            "ceiling_ft" -> "ceiling_ft"
            "cape_jkg", "convective_risk" -> "cape_jkg"
            "visibility_m" -> "visibility_m"
            "cloud_cover_pct" -> "cloud_cover_pct"
            else -> "flight_category"
        }
    }
}

/** FAA/EASA alternate-required flags. */
@Serializable
data class AltRequired(
    val faa: Boolean = false,
    val easa: Boolean = false,
)

/** D-0 METAR/TAF block carried on the forecast payload; decoded, not rendered yet. */
@Serializable
data class ForecastObservation(
    @SerialName("metar_raw")
    val metarRaw: String? = null,
    @SerialName("observation_time")
    val observationTime: String? = null,
    @SerialName("flight_category")
    val flightCategory: String? = null,
    @SerialName("wind_speed_kt")
    val windSpeedKt: Double? = null,
    @SerialName("wind_dir_deg")
    val windDirDeg: Double? = null,
    @SerialName("taf_raw")
    val tafRaw: String? = null,
)

/**
 * How the map and card colour: a consensus reduction or one individual model. [token] is the
 * wire/URL token (`fc.model`): worst/majority/gfs/icon/ecmwf.
 */
sealed interface ForecastModelMode {
    val token: String

    data object Worst : ForecastModelMode {
        override val token = "worst"
    }

    data object Majority : ForecastModelMode {
        override val token = "majority"
    }

    data class Model(val name: String) : ForecastModelMode {
        override val token: String get() = name
    }

    /** Worst/majority are consensus modes (agreement ring shown). */
    val isConsensus: Boolean get() = this !is Model

    /** The card's consensus column mirrors the map mode, falling back to worst for a model. */
    val consensusMode: ForecastModelMode get() = if (isConsensus) this else Worst

    companion object {
        fun fromToken(token: String): ForecastModelMode = when (token) {
            "worst" -> Worst
            "majority" -> Majority
            else -> Model(token)
        }
    }
}

/** `GET /api/flights/frequent-airports`: the pilot's usual airports, most used first. */
@Serializable
data class FrequentAirportsResponse(
    val departures: List<FrequentAirport> = emptyList(),
    val destinations: List<FrequentAirport> = emptyList(),
)

@Serializable
data class FrequentAirport(
    val icao: String = "",
    val count: Int = 0,
)

// ============================================================================
// Historical map (`GET /api/maps/historical?at=&lead=`, #629)
// ============================================================================

/**
 * For one past instant (30-min UTC grid): every watchlist airport's METAR, its TAF re-read at that
 * instant, and each model's forecast from the run fetched before `at - lead days`. **All selection
 * is server-side** (`tasks/historical_map.py`); the client picks a block and colours it with the
 * forecast map's catalog. Airport entries share the forecast map's shape, with METAR/TAF under
 * `observed`; those never feed the consensus, which is null for an airport with no model data.
 */
@Serializable
data class HistoricalMapResponse(
    /** The snapped instant (ISO8601 UTC). */
    val at: String = "",
    @SerialName("lead_days")
    val leadDays: Int = 0,
    /** `at - lead days`: runs fetched after this are excluded. */
    @SerialName("run_cutoff")
    val runCutoff: String? = null,
    /** Per-source provenance, keyed metar/taf/gfs/icon/ecmwf. */
    val sources: Map<String, HistoricalSourceInfo> = emptyMap(),
    val airports: List<HistoricalMapAirport> = emptyList(),
)

/** What one source is based on, or why it has nothing. */
@Serializable
data class HistoricalSourceInfo(
    val available: Boolean = false,
    /** Models only: `beyond_horizon` | `no_valid_time` | `no_run`. */
    val reason: String? = null,
    /** Airports carrying this source. */
    val count: Int? = null,
    /** METAR only: the oldest report still shown as current. */
    @SerialName("max_age_min")
    val maxAgeMin: Int? = null,
    @SerialName("valid_time")
    val validTime: String? = null,
    @SerialName("model_init_time")
    val modelInitTime: String? = null,
    @SerialName("fetched_at")
    val fetchedAt: String? = null,
    @SerialName("lead_hours")
    val leadHours: Int? = null,
)

/** One airport at the historical instant. */
@Serializable
data class HistoricalMapAirport(
    val icao: String = "",
    val lat: Double = 0.0,
    val lon: Double = 0.0,
    @SerialName("approach_type")
    val approachType: String? = null,
    /** Present models only (keys gfs/icon/ecmwf), each with its run + valid time. */
    val models: Map<String, ForecastModelEntry> = emptyMap(),
    /** Consensus over the NWP models only; null when no model has data here. */
    val consensus: ForecastConsensus? = null,
    @SerialName("consensus_majority")
    val consensusMajorityOrNull: ForecastConsensus? = null,
    val observed: HistoricalObserved? = null,
) {
    val id: String get() = icao
    val metar: HistoricalMetar? get() = observed?.metar
    val taf: HistoricalTaf? get() = observed?.taf
    val consensusMajority: ForecastConsensus? get() = consensusMajorityOrNull ?: consensus

    /** The consensus block a consensus source reads. */
    fun consensus(source: HistoricalSource): ForecastConsensus? =
        if (source == HistoricalSource.MAJORITY) consensusMajority else consensus

    /** The value block a source reads; null when this airport has nothing for it (or a consensus). */
    fun entry(source: HistoricalSource): ForecastModelEntry? = when (source) {
        HistoricalSource.METAR -> metar?.values
        HistoricalSource.TAF -> taf?.values
        HistoricalSource.WORST, HistoricalSource.MAJORITY -> null
        HistoricalSource.GFS, HistoricalSource.ICON, HistoricalSource.ECMWF -> models[source.token]
    }

    /**
     * This airport as the shared marker layer's [ForecastMapAirport], presenting one source, or null
     * when it has nothing for that source (left off the map rather than drawn as a misleading
     * default, as on the web). METAR/TAF are presented as the one "model" shown and read through
     * `Model("metar")` / `Model("taf")`, so the catalog colours them exactly as it colours a model.
     * Consensus sources keep the NWP models only: an observation must never vote in the
     * alternate-required aggregation.
     */
    fun forecastAirport(source: HistoricalSource): ForecastMapAirport? {
        val presented: Map<String, ForecastModelEntry> = when (source) {
            HistoricalSource.METAR, HistoricalSource.TAF -> {
                val observed = entry(source) ?: return null
                mapOf(source.token to observed)
            }
            HistoricalSource.WORST, HistoricalSource.MAJORITY -> {
                if (consensus == null) return null
                models
            }
            HistoricalSource.GFS, HistoricalSource.ICON, HistoricalSource.ECMWF -> {
                if (models[source.token] == null) return null
                models
            }
        }
        return asForecastAirport(presented)
    }

    /** Every source side by side, keyed by its token, for the card: each cell reads `Model(token)`. */
    val allSourcesAirport: ForecastMapAirport
        get() {
            val all = models.toMutableMap()
            metar?.let { all[HistoricalSource.METAR.token] = it.values }
            taf?.let { all[HistoricalSource.TAF.token] = it.values }
            return asForecastAirport(all)
        }

    private fun asForecastAirport(models: Map<String, ForecastModelEntry>) = ForecastMapAirport(
        icao = icao,
        lat = lat,
        lon = lon,
        approachType = approachType,
        models = models,
        consensus = consensus ?: ForecastConsensus.EMPTY,
        consensusMajorityOrNull = consensusMajority ?: ForecastConsensus.EMPTY,
    )
}

@Serializable
data class HistoricalObserved(
    val metar: HistoricalMetar? = null,
    val taf: HistoricalTaf? = null,
)

/**
 * The METAR shown for the instant: the latest report no older than the server's max age. The
 * object is flat on the wire: [values] reads the model-shaped keys (category, ceiling, visibility,
 * wind, runway components, alternate-required) from the same object as the report fields.
 */
@Serializable(with = HistoricalMetarSerializer::class)
data class HistoricalMetar(
    val values: ForecastModelEntry = ForecastModelEntry(),
    val report: Report = Report(),
) {
    val observationTime: String? get() = report.observationTime
    val ageMin: Int? get() = report.ageMin
    /** "METAR" or "SPECI". */
    val reportType: String? get() = report.reportType
    val raw: String? get() = report.raw

    @Serializable
    data class Report(
        @SerialName("observation_time")
        val observationTime: String? = null,
        @SerialName("age_min")
        val ageMin: Int? = null,
        @SerialName("report_type")
        val reportType: String? = null,
        val raw: String? = null,
        val weather: List<String> = emptyList(),
        @SerialName("dewpoint_c")
        val dewpointC: Double? = null,
        val qnh: Double? = null,
    )
}

/**
 * The TAF read at the instant (`analysis/taf_reading.read_taf_at`): prevailing conditions with the
 * worst TEMPO/PROB group. `values.flightCategory` is the governing category. Flat on the wire, as
 * [HistoricalMetar].
 */
@Serializable(with = HistoricalTafSerializer::class)
data class HistoricalTaf(
    val values: ForecastModelEntry = ForecastModelEntry(),
    val reading: Reading = Reading(),
) {
    val prevailingCategory: String? get() = reading.prevailingCategory
    val temporaryCategory: String? get() = reading.temporaryCategory
    /** e.g. "TEMPO", "PROB30"; null when only the prevailing group applies. */
    val temporaryType: String? get() = reading.temporaryType
    val issueTime: String? get() = reading.issueTime
    val raw: String? get() = reading.raw

    @Serializable
    data class Reading(
        @SerialName("prevailing_category")
        val prevailingCategory: String? = null,
        @SerialName("temporary_category")
        val temporaryCategory: String? = null,
        @SerialName("temporary_type")
        val temporaryType: String? = null,
        @SerialName("trend_type")
        val trendType: String? = null,
        @SerialName("significant_weather")
        val significantWeather: List<String> = emptyList(),
        @SerialName("issue_time")
        val issueTime: String? = null,
        val raw: String? = null,
    )
}

/**
 * Decodes one flat JSON object into two typed halves (the model-shaped values + the report fields),
 * and merges them back on encode. iOS decodes the same object twice the same way.
 */
private abstract class FlatPairSerializer<T, A, B>(
    serialName: String,
    private val first: KSerializer<A>,
    private val second: KSerializer<B>,
) : KSerializer<T> {
    override val descriptor: SerialDescriptor = buildClassSerialDescriptor(serialName)

    abstract fun build(a: A, b: B): T
    abstract fun split(value: T): Pair<A, B>

    override fun deserialize(decoder: Decoder): T {
        val json = (decoder as JsonDecoder).json
        val element: JsonElement = decoder.decodeJsonElement()
        return build(json.decodeFromJsonElement(first, element), json.decodeFromJsonElement(second, element))
    }

    override fun serialize(encoder: Encoder, value: T) {
        val json = (encoder as JsonEncoder).json
        val (a, b) = split(value)
        val merged = json.encodeToJsonElement(first, a).jsonObject + json.encodeToJsonElement(second, b).jsonObject
        encoder.encodeJsonElement(JsonObject(merged))
    }
}

private object HistoricalMetarSerializer : FlatPairSerializer<HistoricalMetar, ForecastModelEntry, HistoricalMetar.Report>(
    "aero.flyfun.weather.model.HistoricalMetar",
    ForecastModelEntry.serializer(),
    HistoricalMetar.Report.serializer(),
) {
    override fun build(a: ForecastModelEntry, b: HistoricalMetar.Report) = HistoricalMetar(a, b)
    override fun split(value: HistoricalMetar) = value.values to value.report
}

private object HistoricalTafSerializer : FlatPairSerializer<HistoricalTaf, ForecastModelEntry, HistoricalTaf.Reading>(
    "aero.flyfun.weather.model.HistoricalTaf",
    ForecastModelEntry.serializer(),
    HistoricalTaf.Reading.serializer(),
) {
    override fun build(a: ForecastModelEntry, b: HistoricalTaf.Reading) = HistoricalTaf(a, b)
    override fun split(value: HistoricalTaf) = value.values to value.reading
}

/** `GET /api/maps/historical/range`: what the pickers can offer. */
@Serializable
data class HistoricalRangeResponse(
    /** Latest selectable instant (the server's 30-min floor of "now"). */
    val latest: String = "",
    /** Oldest METAR/TAF; null when there are none. */
    @SerialName("earliest_observation")
    val earliestObservation: String? = null,
    /** Oldest model snapshot; days before it are METAR/TAF only. */
    @SerialName("earliest_model")
    val earliestModel: String? = null,
    @SerialName("step_minutes")
    val stepMinutes: Int = 30,
    @SerialName("model_sample_hours")
    val modelSampleHours: List<Int> = emptyList(),
    @SerialName("metar_max_age_min")
    val metarMaxAgeMin: Int = 90,
    @SerialName("model_max_age_min")
    val modelMaxAgeMin: Int = 180,
    val models: List<String> = emptyList(),
    @SerialName("observed_sources")
    val observedSources: List<String> = emptyList(),
    /** Which models are stored at each lead (a model beyond its map horizon isn't). */
    val leads: List<HistoricalLead> = emptyList(),
)

@Serializable
data class HistoricalLead(
    @SerialName("lead_days")
    val leadDays: Int = 0,
    val models: List<String> = emptyList(),
)

/** What the historical map colours from. [token] is the web's `hist.source` token and payload key. */
enum class HistoricalSource(val token: String) {
    METAR("metar"),
    TAF("taf"),
    WORST("worst"),
    MAJORITY("majority"),
    GFS("gfs"),
    ICON("icon"),
    ECMWF("ecmwf"),
    ;

    val label: String
        get() = when (this) {
            METAR -> "METAR"
            TAF -> "TAF"
            WORST -> "Worst"
            MAJORITY -> "Majority"
            GFS, ICON, ECMWF -> token.uppercase()
        }

    val isObserved: Boolean get() = this == METAR || this == TAF
    val isConsensus: Boolean get() = this == WORST || this == MAJORITY

    /** The mode the shared marker layer and catalog read this source through. */
    val mapMode: ForecastModelMode
        get() = when (this) {
            WORST -> ForecastModelMode.Worst
            MAJORITY -> ForecastModelMode.Majority
            else -> ForecastModelMode.Model(token)
        }

    companion object {
        val observed = listOf(METAR, TAF)
        val consensusModes = listOf(WORST, MAJORITY)
        val nwpModels = listOf(GFS, ICON, ECMWF)

        fun fromToken(token: String?): HistoricalSource? = entries.firstOrNull { it.token == token }
    }
}
