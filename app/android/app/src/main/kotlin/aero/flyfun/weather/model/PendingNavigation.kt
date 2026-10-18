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

    @Serializable
    data class ForecastMap(
        val dayOffset: Int? = null,
        val hourUtc: Int? = null,
        val model: String? = null,
        val metric: String? = null,
        val grametInitKey: String? = null,
        val viewMode: String? = null,
    ) : NavigationTarget

    @Serializable
    data class HistoricalMap(
        val date: String? = null,
        val hourUtc: Int? = null,
        val metric: String? = null,
        val viewMode: String? = null,
    ) : NavigationTarget
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
