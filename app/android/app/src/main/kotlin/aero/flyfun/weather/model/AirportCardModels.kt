package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// ============================================================================
// Airport cards (#880 server, #883 Android)
//
// Wire types for the Observed tab's departure / arrival cards. Every value is
// the server's (`models/observations.py`, `models/live.py`): the client only
// lays them out. All optional and defaulted, so an older server decodes.
// ============================================================================

/** The TAF read at one whole hour, for the card's strip. */
@Serializable
data class TafHour(
    val time: String = "",
    @SerialName("prevailing_category")
    val prevailingCategory: String? = null,
    // The worst TEMPO/PROB group, only when strictly worse than prevailing.
    @SerialName("temporary_category")
    val temporaryCategory: String? = null,
    @SerialName("temporary_type")
    val temporaryType: String? = null,
    // The hour holding the ETA (the ETD for the departure): outlined.
    @SerialName("is_eta")
    val isEta: Boolean = false,
)

/** One METAR present-weather group in words ("-DZ" → "Light drizzle"). */
@Serializable
data class WeatherWord(
    val code: String = "",
    val text: String = "",
    // thunder, hail, snow, freezing, showers, rain, drizzle, fog, mist, haze,
    // smoke, dust, squall, funnel, other.
    val icon: String = "other",
)

/** The METAR crosswind on the best runway end, as the card shows it. */
@Serializable
data class CrosswindReading(
    val runway: String = "",
    // Where it comes from: "left" / "right" / "" (down the runway).
    val side: String = "",
    val kt: Int = 0,
    @SerialName("gust_kt")
    val gustKt: Int? = null,
    val text: String = "",
)

/**
 * The server's runway + wind picture (#758, `models/runway_wind.py`): runways
 * with true headings and every wind sample laid on them, with the server's
 * best end. Named `Wire` apart from the client-built [RunwayWindPicture].
 */
@Serializable
data class RunwayWindWire(
    val icao: String = "",
    val runways: List<RunwayWire> = emptyList(),
    val winds: List<WindAtAirportWire> = emptyList(),
) {
    fun wind(source: String): WindAtAirportWire? = winds.firstOrNull { it.wind.source == source }
}

@Serializable
data class RunwayWire(
    val id: String = "",
    val ends: List<RunwayEndWire> = emptyList(),
)

@Serializable
data class RunwayEndWire(
    val ident: String = "",
    @SerialName("heading_true")
    val headingTrue: Double = 0.0,
)

@Serializable
data class WindSampleWire(
    val source: String = "",
    val time: String? = null,
    @SerialName("direction_true")
    val directionTrue: Int? = null,
    @SerialName("speed_kt")
    val speedKt: Int? = null,
    @SerialName("gust_kt")
    val gustKt: Int? = null,
    val variable: Boolean = false,
    val calm: Boolean = false,
)

@Serializable
data class WindAtAirportWire(
    val wind: WindSampleWire = WindSampleWire(),
    @SerialName("best_end")
    val bestEnd: String? = null,
    val advisory: String? = null,
)

/** One storm near a terminal ("9 NM NE · 45 dBZ, lightning · closing 8 kt"). */
@Serializable
data class LiveDestinationCell(
    val id: String = "",
    val text: String = "",
    val focus: LiveFocus? = null,
)

/**
 * One terminal's card block: `glance.destination` (#816) or `glance.departure`
 * (#880). The two share every field but the time (`eta` / `etd`) and the
 * departure's fold, so one type carries both.
 */
@Serializable
data class LiveGlanceTerminal(
    val icao: String = "",
    val name: String? = null,
    val eta: String? = null,
    val etd: String? = null,
    @SerialName("metar_text")
    val metarText: String = "",
    @SerialName("metar_category")
    val metarCategory: String? = null,
    @SerialName("metar_time")
    val metarTime: String? = null,
    @SerialName("proxy_caption")
    val proxyCaption: String? = null,
    @SerialName("taf_text")
    val tafText: String = "",
    @SerialName("taf_category")
    val tafCategory: String? = null,
    @SerialName("wind_text")
    val windText: String? = null,
    @SerialName("wind_advisory")
    val windAdvisory: String? = null,
    @SerialName("cells_text")
    val cellsText: String = "",
    val cells: List<LiveDestinationCell> = emptyList(),
    @SerialName("lightning_text")
    val lightningText: String? = null,
    // An alert-tier change row stands behind that row (PR 887): tone it amber.
    @SerialName("cells_alert")
    val cellsAlert: Boolean = false,
    @SerialName("lightning_alert")
    val lightningAlert: Boolean = false,
    val alert: Boolean = false,
    val unavailable: List<String> = emptyList(),
    val focus: LiveFocus? = null,
    // The field as the card reads it: its own row or its proxy laid in.
    val airport: AirportObservation? = null,
    // Departure only.
    val folded: Boolean = false,
    @SerialName("folded_at")
    val foldedAt: String? = null,
    @SerialName("folded_source")
    val foldedSource: String? = null,
    @SerialName("folded_text")
    val foldedText: String? = null,
)

/** The flight strip (#816): where the flight is and when it lands, one line. */
@Serializable
data class LiveGlanceFlight(
    val phase: String = "planned",
    val text: String = "",
    val source: String? = null,
    val eta: String? = null,
    @SerialName("eta_delay_min")
    val etaDelayMin: Int? = null,
)
