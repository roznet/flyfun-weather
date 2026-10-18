package aero.flyfun.weather.model

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.json.Json
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * Shared JSON serializer instance configured for FlyFun Weather server payloads.
 */
@OptIn(ExperimentalSerializationApi::class)
val WbJson: Json = Json {
    ignoreUnknownKeys = true
    isLenient = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
}

/**
 * ISO-8601 date/time parsing helpers that work identically on Android (API 31+) and JVM unit tests.
 */
object IsoTime {
    /**
     * Parses an ISO-8601 timestamp string (with or without fractional seconds, with `Z`, `+00:00`,
     * or naive UTC `yyyy-MM-dd'T'HH:mm:ss`) into epoch milliseconds, or returns `null` if invalid.
     */
    fun parseEpochMs(iso: String?): Long? {
        if (iso.isNullOrBlank()) return null
        val trimmed = iso.trim()
        runCatching { return Instant.parse(trimmed).toEpochMilli() }
        runCatching { return OffsetDateTime.parse(trimmed, DateTimeFormatter.ISO_OFFSET_DATE_TIME).toInstant().toEpochMilli() }
        runCatching {
            val ldt = LocalDateTime.parse(trimmed, DateTimeFormatter.ISO_LOCAL_DATE_TIME)
            return ldt.toInstant(ZoneOffset.UTC).toEpochMilli()
        }
        runCatching {
            val ld = LocalDate.parse(trimmed, DateTimeFormatter.ISO_LOCAL_DATE)
            return ld.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
        }
        return null
    }

    /**
     * Formats epoch milliseconds as an ISO-8601 UTC string (`2026-07-15T12:00:00Z`).
     */
    fun formatUtc(epochMs: Long): String =
        DateTimeFormatter.ISO_INSTANT.format(Instant.ofEpochMilli(epochMs))
}
