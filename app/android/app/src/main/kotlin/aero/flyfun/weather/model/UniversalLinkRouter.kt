package aero.flyfun.weather.model

import java.net.URI
import java.net.URLDecoder

/**
 * Pure URL router for `https://weather.flyfun.aero/...` Universal Links / App Links.
 * Mirrors `AppState.navigationTarget(forUniversalLink:)` on iOS.
 */
object UniversalLinkRouter {
    const val UNIVERSAL_LINK_HOST = "weather.flyfun.aero"

    fun navigationTarget(uriString: String): NavigationTarget? {
        val trimmed = uriString.trim()
        if (trimmed.isEmpty()) return null
        val uri = runCatching { URI(trimmed) }.getOrNull() ?: return null
        return navigationTarget(uri)
    }

    fun navigationTarget(uri: URI): NavigationTarget? {
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase().orEmpty()
        val path = uri.path.orEmpty()
        val params = parseQuery(uri.rawQuery)

        if (scheme == "flyfunweather") {
            return when (host) {
                "briefing" -> {
                    val flightId = (params["flight_id"] ?: params["flight"])?.takeIf { it.isNotEmpty() } ?: return null
                    val packTimestamp = (params["pack_timestamp"] ?: params["t"])?.takeIf { it.isNotEmpty() }
                    val obsRaw = params["open_observed"] ?: params["observed"]
                    val openObserved = obsRaw == "1" || obsRaw?.lowercase() == "true"
                    NavigationTarget.Briefing(
                        flightId = flightId,
                        packTimestamp = packTimestamp,
                        openObserved = openObserved,
                    )
                }
                "trip" -> {
                    val tripId = (params["trip_id"] ?: params["id"] ?: params["trip"])?.takeIf { it.isNotEmpty() } ?: return null
                    NavigationTarget.Trip(tripId = tripId)
                }
                else -> null
            }
        }

        if (scheme != "https" && scheme != "http") return null
        if (host != UNIVERSAL_LINK_HOST) return null

        return when {
            path == "/briefing.html" -> {
                val flightId = params["flight"]?.takeIf { it.isNotEmpty() } ?: return null
                val packTimestamp = params["t"]?.takeIf { it.isNotEmpty() }
                val openObserved = params["observed"] == "1" || params["observed"]?.lowercase() == "true"
                NavigationTarget.Briefing(
                    flightId = flightId,
                    packTimestamp = packTimestamp,
                    openObserved = openObserved,
                )
            }

            path == "/trip.html" -> {
                val tripId = (params["id"] ?: params["trip"])?.takeIf { it.isNotEmpty() } ?: return null
                NavigationTarget.Trip(tripId = tripId)
            }

            path == "/maps.html" -> {
                fun value(name: String) = params[name]?.takeIf { it.isNotEmpty() }
                val tab = params["tab"]
                if (tab == "historical") {
                    // The web's `hist.*` share state (iOS `historicalMapDeepLink`)
                    NavigationTarget.HistoricalMap(
                        date = value("hist.date"),
                        time = value("hist.time"),
                        lead = value("hist.lead")?.toIntOrNull(),
                        source = value("hist.source"),
                        metric = value("hist.metric"),
                        airport = value("hist.apt"),
                        view = MapViewport.parse(value("hist.view")),
                    )
                } else {
                    // A link to a web tab the app doesn't have (now, synoptic, climatology) still
                    // lands on the forecast map, positioned on that tab's own `*.view`
                    val tabViewKey = when (tab) {
                        "now" -> "now.view"
                        "synoptic" -> "syn.view"
                        "climatology" -> "clim.view"
                        else -> null
                    }
                    NavigationTarget.ForecastMap(
                        dayOffset = value("fc.day")?.toIntOrNull(),
                        // Malformed → dropped, so `fc.day` applies
                        date = value("fc.date")?.takeIf { ForecastLinkedDate.parse(it) != null },
                        hourUtc = value("fc.hour")?.toIntOrNull(),
                        model = value("fc.model"),
                        metric = value("fc.metric"),
                        airport = value("fc.apt"),
                        grametInitKey = value("fc.gramet_init"),
                        view = tabViewKey?.let { MapViewport.parse(value(it)) } ?: MapViewport.parse(value("fc.view")),
                    )
                }
            }

            path.startsWith("/s/") -> {
                val segments = path.removePrefix("/").split("/")
                if (segments.size != 2) return null
                val code = segments[1]
                if (!isValidShareCode(code)) return null
                NavigationTarget.SharedFlight(code = code)
            }

            path.startsWith("/t/") -> {
                val segments = path.removePrefix("/").split("/")
                if (segments.size != 2) return null
                val code = segments[1]
                if (!isValidShareCode(code)) return null
                NavigationTarget.SharedTrip(code = code)
            }

            else -> null
        }
    }

    /**
     * Share codes are 8-char base62 strings generated by the server; accept 4..16 ASCII alphanumeric chars.
     */
    fun isValidShareCode(code: String): Boolean {
        if (code.length !in 4..16) return false
        return code.all { ch ->
            ch in 'a'..'z' || ch in 'A'..'Z' || ch in '0'..'9'
        }
    }

    private fun parseQuery(rawQuery: String?): Map<String, String> {
        if (rawQuery.isNullOrEmpty()) return emptyMap()
        val map = LinkedHashMap<String, String>()
        for (part in rawQuery.split('&')) {
            if (part.isEmpty()) continue
            val eqIdx = part.indexOf('=')
            if (eqIdx < 0) {
                val key = decodeComponent(part)
                map[key] = ""
            } else {
                val key = decodeComponent(part.substring(0, eqIdx))
                val value = decodeComponent(part.substring(eqIdx + 1))
                map[key] = value
            }
        }
        return map
    }

    private fun decodeComponent(s: String): String =
        runCatching { URLDecoder.decode(s, Charsets.UTF_8.name()) }.getOrDefault(s)
}
