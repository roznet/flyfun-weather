package aero.flyfun.weather.data

import java.time.Instant
import java.time.format.DateTimeParseException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * What the server says when it can't answer: down for maintenance, or
 * temporarily unavailable. Mirrors `ServiceStatus` on iOS.
 *
 * When the app behind weather.flyfun.aero is down or switched into
 * maintenance, Caddy answers every API call with a 502/503/504 and a small
 * JSON body written by `scripts/ops/maintenance.py`:
 *
 *     {"service_status": "maintenance" | "unavailable",
 *      "detail": "FlyFun Weather is down for planned maintenance. Expected back around 14:30Z.",
 *      "until": "2026-11-07T14:30:00Z" | null, "updated_at": "..."}
 *
 * [ApiClient.validateStatus] still throws [ApiError.ServerError] for it (so
 * every existing 5xx decision is unchanged) and reports it to
 * [ServiceStatusMonitor], which drives one app-wide banner.
 */
data class ServiceStatus(
    val kind: Kind,
    val message: String,
    val until: Instant?,
) {
    enum class Kind { MAINTENANCE, UNAVAILABLE }

    companion object {
        const val GENERIC_MESSAGE =
            "FlyFun Weather is temporarily unavailable. Please try again in a few minutes."

        /** Gateway codes: the proxy answered for an app that couldn't. */
        val GATEWAY_STATUS_CODES = setOf(502, 503, 504)

        private val json = Json { ignoreUnknownKeys = true }

        /** The Caddy status body on a 5xx, or null for anything else (an app error). */
        fun parse(statusCode: Int, body: ByteArray): ServiceStatus? {
            if (statusCode < 500 || body.isEmpty()) return null
            val obj = runCatching {
                json.parseToJsonElement(body.toString(Charsets.UTF_8)) as? JsonObject
            }.getOrNull() ?: return null
            val kind = when ((obj["service_status"] as? JsonPrimitive)?.contentOrNull) {
                "maintenance" -> Kind.MAINTENANCE
                "unavailable" -> Kind.UNAVAILABLE
                else -> return null
            }
            val detail = (obj["detail"] as? JsonPrimitive)?.contentOrNull?.trim()
            val until = (obj["until"] as? JsonPrimitive)?.contentOrNull?.let {
                try {
                    Instant.parse(it)
                } catch (_: DateTimeParseException) {
                    null
                }
            }
            return ServiceStatus(kind, detail?.takeIf { it.isNotEmpty() } ?: GENERIC_MESSAGE, until)
        }
    }
}

/**
 * App-wide "the server can't answer" state: set by [ApiClient] when a call
 * comes back with the status body, cleared by the next 2xx.
 */
object ServiceStatusMonitor {
    private val _current = MutableStateFlow<ServiceStatus?>(null)
    val current: StateFlow<ServiceStatus?> = _current.asStateFlow()

    fun report(status: ServiceStatus) {
        _current.value = status
    }

    fun clear() {
        if (_current.value != null) _current.value = null
    }
}
