package aero.flyfun.weather.model

import kotlinx.serialization.Serializable

/**
 * Deep-link and Universal Link navigation targets across the app.
 */
@Serializable
sealed interface NavigationTarget {
    @Serializable
    data class Briefing(
        val flightId: String,
        val packTimestamp: String? = null,
        val openObserved: Boolean = false,
    ) : NavigationTarget

    @Serializable
    data class Trip(
        val tripId: String,
    ) : NavigationTarget

    @Serializable
    data class SharedFlight(
        val code: String,
    ) : NavigationTarget

    @Serializable
    data class SharedTrip(
        val code: String,
    ) : NavigationTarget

    /** The forecast map's `fc.*` share state (iOS `MapDeepLink`). */
    @Serializable
    data class ForecastMap(
        val dayOffset: Int? = null,
        /** Absolute UTC `YYYY-MM-DD` (`fc.date`, #840); wins over [dayOffset] when set. */
        val date: String? = null,
        val hourUtc: Int? = null,
        /** `fc.model`: worst / majority / gfs / icon / ecmwf. */
        val model: String? = null,
        val metric: String? = null,
        /** `fc.apt`: the airport to open once the payload loads. */
        val airport: String? = null,
        val grametInitKey: String? = null,
        /** The sender's map position (`fc.view`, or the linked web tab's own `*.view`). */
        val view: MapViewport? = null,
    ) : NavigationTarget

    /** The historical map's `hist.*` share state (iOS `HistoricalMapDeepLink`). */
    @Serializable
    data class HistoricalMap(
        /** `hist.date`, `YYYY-MM-DD` (UTC). */
        val date: String? = null,
        /** `hist.time`, `HH:mm` (UTC); the instant applies only when both date and time are set. */
        val time: String? = null,
        /** `hist.lead`, 0 (latest runs) … 6 days. */
        val lead: Int? = null,
        /** `hist.source`: metar / taf / worst / majority / gfs / icon / ecmwf. */
        val source: String? = null,
        val metric: String? = null,
        /** `hist.apt`. */
        val airport: String? = null,
        val view: MapViewport? = null,
    ) : NavigationTarget
}

/**
 * A shared web map position (`*.view=lat,lon,zoom`, Leaflet zoom). Parsed as iOS `MapViewport`:
 * finite, in range, zoom rounded into 1…18; anything else is dropped.
 */
@Serializable
data class MapViewport(
    val lat: Double,
    val lon: Double,
    val zoom: Int,
) {
    companion object {
        fun parse(token: String?): MapViewport? {
            val parts = token?.split(",")?.map { it.trim().toDoubleOrNull() } ?: return null
            if (parts.size != 3) return null
            val (lat, lon, zoom) = parts
            if (lat == null || lon == null || zoom == null) return null
            if (!lat.isFinite() || !lon.isFinite() || !zoom.isFinite()) return null
            if (lat !in -90.0..90.0 || lon !in -180.0..180.0) return null
            val z = Math.round(zoom).toInt()
            return if (z in 1..18) MapViewport(lat, lon, z) else null
        }
    }
}

/**
 * Handoff store for pending navigation intents (e.g. from deep links, shortcuts, or notifications).
 */
object PendingNavigation {
    @Volatile
    private var pendingFlightId: String? = null

    @Volatile
    private var pendingTarget: NavigationTarget? = null

    fun setFlightId(flightId: String) {
        pendingFlightId = flightId
        pendingTarget = NavigationTarget.Briefing(flightId = flightId)
    }

    fun consumeFlightId(): String? {
        val id = pendingFlightId
        pendingFlightId = null
        return id
    }

    fun setTarget(target: NavigationTarget) {
        pendingTarget = target
        if (target is NavigationTarget.Briefing) {
            pendingFlightId = target.flightId
        }
    }

    fun consumeTarget(): NavigationTarget? {
        val target = pendingTarget
        pendingTarget = null
        pendingFlightId = null
        return target
    }

    fun post(target: NavigationTarget) = setTarget(target)

    fun consume(): NavigationTarget? = consumeTarget()
}
