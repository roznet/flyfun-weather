package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Response of `GET /api/flights/archive?view=list` (#1031): the owner's Past flights, filtered,
 * paged and counted by the server. The app renders rows, chips and counts as given and computes
 * none of them (#1032). Mirrors iOS `PastArchiveResponse`.
 */
@Serializable
data class PastArchiveResponse(
    val view: String = "list",
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
    val facets: PastArchiveFacets = PastArchiveFacets(),
    val rows: List<PastArchiveRow> = emptyList(),
)

@Serializable
data class PastArchiveFacets(
    val source: String = "forecast",
    /** Matches per tag for the current filter without the tag chips. */
    val tags: Map<String, Int> = emptyMap(),
    /** Matches per outcome for the current filter without the outcome chips. */
    val outcomes: Map<String, Int> = emptyMap(),
    @SerialName("not_recorded")
    val notRecorded: Int = 0,
)

@Serializable
data class PastArchiveTrip(
    val id: String,
    val name: String = "",
)

/**
 * One Past flight. `tagsRecorded == false`: the briefing was gone before it was recorded,
 * shown as "Hazards not recorded", never as "none".
 */
@Serializable
data class PastArchiveRow(
    val id: String,
    @SerialName("departure_time")
    val departureTime: String = "",
    val departure: String? = null,
    val destination: String? = null,
    @SerialName("route_name")
    val routeName: String = "",
    val trip: PastArchiveTrip? = null,
    /** Worst status per tag, amber or worse only: `{"ICE": "red"}`. */
    @SerialName("forecast_tags")
    val forecastTags: Map<String, String> = emptyMap(),
    @SerialName("forecast_worst")
    val forecastWorst: String? = null,
    @SerialName("tags_recorded")
    val tagsRecorded: Boolean = false,
    @SerialName("reported_tags")
    val reportedTags: List<String> = emptyList(),
    val decision: String? = null,
    val debriefed: Boolean = false,
    @SerialName("differs_from_forecast")
    val differsFromForecast: Boolean = false,
    @SerialName("distance_nm")
    val distanceNm: Double? = null,
) {
    val title: String
        get() = listOfNotNull(departure, destination).takeIf { it.isNotEmpty() }?.joinToString(" → ") ?: routeName

    val departureEpochMs: Long? get() = IsoTime.parseEpochMs(departureTime)
}

/**
 * The finder's chips, in the order every client shows them (web `helpers/past-archive.ts`,
 * iOS `PastChips`): change one, change all.
 */
object PastChips {
    data class Chip(
        /** Archive tag (`ICE`) or outcome key (`flown`) the server takes. */
        val id: String,
        val label: String,
    )

    val hazards: List<Chip> = listOf(
        Chip("ICE", "Icing"), Chip("TS", "Convection"), Chip("IMC", "Low cloud"),
        Chip("WIND", "Wind"), Chip("TURB", "Turbulence"), Chip("FRZ", "Freezing precip"),
        Chip("VIS", "Visibility"),
    )
    val outcomes: List<Chip> = listOf(
        Chip("flown", "Flown"), Chip("cancelled", "Cancelled"), Chip("monitoring", "Monitor only"),
        Chip("debriefed", "Debriefed"), Chip("differs", "Differed from forecast"),
    )
    val hideInactive: List<String> = listOf("cancelled", "monitoring")

    fun labelForTag(tag: String): String = hazards.firstOrNull { it.id == tag }?.label ?: tag
    fun labelForOutcome(key: String): String = outcomes.firstOrNull { it.id == key }?.label ?: key
}

/** The finder's filter state; [queryItems] is the whole client-side logic. */
data class PastFilter(
    val query: String = "",
    val tags: List<String> = emptyList(),
    val reported: Boolean = false,
    val outcomes: List<String> = emptyList(),
    val hideInactive: Boolean = false,
) {
    val isActive: Boolean
        get() = query.isNotBlank() || tags.isNotEmpty() || outcomes.isNotEmpty() || hideInactive

    fun queryItems(limit: Int, offset: Int): List<Pair<String, String>> = buildList {
        query.trim().takeIf { it.isNotEmpty() }?.let { add("q" to it) }
        if (tags.isNotEmpty()) add("tags" to tags.joinToString(","))
        if (reported) add("source" to "reported")
        if (outcomes.isNotEmpty()) add("outcome" to outcomes.joinToString(","))
        if (hideInactive) add("hide" to PastChips.hideInactive.joinToString(","))
        add("limit" to limit.toString())
        if (offset > 0) add("offset" to offset.toString())
    }

    companion object {
        fun toggled(list: List<String>, value: String): List<String> =
            if (value in list) list - value else list + value
    }
}
