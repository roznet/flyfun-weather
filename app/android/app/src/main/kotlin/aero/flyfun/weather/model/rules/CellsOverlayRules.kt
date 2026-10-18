package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.CellDisplay
import aero.flyfun.weather.model.DisplayCell
import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.ObservedImageryFrame
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/**
 * Metadata entry for a single convective cell frame in the radar timeline.
 */
data class CellFrameEntry(
    val timestamp: String,
    val validTime: String? = null,
    val epochMs: Long? = null,
) {
    fun resolvedEpochMs(): Long? =
        epochMs ?: CellsOverlayRules.parseFrameEpochMs(validTime) ?: CellsOverlayRules.parseFrameEpochMs(timestamp)

    companion object {
        fun fromImageryFrame(frame: ObservedImageryFrame): CellFrameEntry = CellFrameEntry(
            timestamp = frame.timestamp,
            validTime = frame.validTime,
            epochMs = CellsOverlayRules.parseFrameEpochMs(frame.validTime)
                ?: CellsOverlayRules.parseFrameEpochMs(frame.timestamp),
        )
    }
}

/**
 * Age bucket classification for Blitzortung lightning strikes relative to a reference radar frame timestamp.
 */
enum class LightningAgeBucket(
    val maxAgeSeconds: Long,
    val alpha: Float,
    val colorHex: Long,
) {
    FRESH_5M(300L, 1.0f, 0xFFFACC15L),
    RECENT_15M(900L, 0.75f, 0xFFF97316L),
    OLDER_30M(1800L, 0.45f, 0xFFEF4444L),
    EXPIRED(Long.MAX_VALUE, 0.0f, 0xFF6B7280L),
}

/**
 * A single observed lightning strike with its classified age bucket.
 */
data class ClassifiedLightningFlash(
    val lat: Double,
    val lon: Double,
    val epochSec: Long,
    val ageSeconds: Long,
    val bucket: LightningAgeBucket,
)

/**
 * Pure rules for convective cell frame matching, staleness detection, operational cell filtering,
 * motion arrow projection, and Blitzortung lightning strike age bucketing.
 * Mirrors iOS `CellsOverlay.swift` and `ObservedLightning.swift`.
 */
object CellsOverlayRules {

    /** Maximum drift in milliseconds between a radar frame and a cell frame before refusing the match (10 min). */
    const val MAX_FRAME_DRIFT_MS: Long = 10L * 60L * 1000L

    /** Default staleness threshold in minutes for convective cell frames. */
    const val DEFAULT_STALE_THRESHOLD_MINUTES: Int = 25

    /** Default forward motion projection horizon in minutes. */
    const val DEFAULT_PROJECTION_MINUTES: Double = 30.0

    /** Maximum future tolerance (seconds) when matching strikes to a scrubbed frame. */
    const val MAX_FUTURE_SLOP_SECONDS: Long = 120L

    private val COMPACT_ISO_FORMATTER: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'", Locale.US)
    private val COMPACT_MIN_FORMATTER: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyyMMddHHmm", Locale.US)
    private val COMPACT_UNDERSCORE_FORMATTER: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyyMMdd_HHmm", Locale.US)

    /**
     * Parse either an ISO-8601 timestamp (`2026-04-12T10:25:00Z`) or compact radar stamp
     * (`20260412T102500Z`, `202604121025`, `20260412_1025`) into epoch milliseconds.
     */
    fun parseFrameEpochMs(raw: String?): Long? {
        if (raw.isNullOrBlank()) return null
        val trimmed = raw.trim()
        IsoTime.parseEpochMs(trimmed)?.let { return it }

        for (fmt in listOf(COMPACT_ISO_FORMATTER, COMPACT_MIN_FORMATTER, COMPACT_UNDERSCORE_FORMATTER)) {
            runCatching {
                LocalDateTime.parse(trimmed, fmt).toInstant(ZoneOffset.UTC).toEpochMilli()
            }.getOrNull()?.let { return it }
        }
        return null
    }

    /**
     * Match a cell frame to a target radar frame timestamp.
     *
     * - If `frames` is empty, returns `null`.
     * - If `targetRadarStamp` is null or blank, returns the latest frame in `frames`.
     * - If an exact timestamp or validTime match exists, returns it immediately.
     * - Otherwise selects the closest frame by epoch time within [MAX_FRAME_DRIFT_MS] (10 minutes),
     *   preferring the earlier frame on exact ties.
     */
    fun matchCellFrame(
        frames: List<CellFrameEntry>,
        targetRadarStamp: String?,
    ): CellFrameEntry? {
        if (frames.isEmpty()) return null
        if (targetRadarStamp.isNullOrBlank()) {
            return frames.maxByOrNull { it.resolvedEpochMs() ?: Long.MIN_VALUE } ?: frames.last()
        }

        val targetTrimmed = targetRadarStamp.trim()
        frames.firstOrNull {
            it.timestamp == targetTrimmed || it.validTime == targetTrimmed
        }?.let { return it }

        val targetMs = parseFrameEpochMs(targetTrimmed) ?: return null

        var bestEntry: CellFrameEntry? = null
        var bestDiff = Long.MAX_VALUE
        var bestEpoch = Long.MAX_VALUE

        for (entry in frames) {
            val entryMs = entry.resolvedEpochMs() ?: continue
            val diff = abs(entryMs - targetMs)
            if (diff < bestDiff || (diff == bestDiff && entryMs < bestEpoch)) {
                bestDiff = diff
                bestEpoch = entryMs
                bestEntry = entry
            }
        }

        return if (bestEntry != null && bestDiff <= MAX_FRAME_DRIFT_MS) bestEntry else null
    }

    /**
     * Overload matching `ObservedImageryFrame` directly.
     */
    fun matchImageryFrame(
        frames: List<ObservedImageryFrame>,
        targetRadarStamp: String?,
    ): ObservedImageryFrame? {
        if (frames.isEmpty()) return null
        val entries = frames.map { CellFrameEntry.fromImageryFrame(it) }
        val matched = matchCellFrame(entries, targetRadarStamp) ?: return null
        return frames.firstOrNull {
            it.timestamp == matched.timestamp && it.validTime == matched.validTime
        }
    }

    /**
     * Returns true if the cell frame is older than `staleThresholdMinutes` (default 25 min) relative to `nowEpochMs`.
     */
    fun isCellFrameStale(
        frameTimestampEpochMs: Long,
        nowEpochMs: Long,
        staleThresholdMinutes: Int = DEFAULT_STALE_THRESHOLD_MINUTES,
    ): Boolean {
        val thresholdMs = staleThresholdMinutes.toLong() * 60_000L
        return (nowEpochMs - frameTimestampEpochMs) > thresholdMs
    }

    /**
     * Overload checking staleness from an ISO or compact timestamp string.
     */
    fun isCellFrameStale(
        frameTimestamp: String?,
        nowEpochMs: Long,
        staleThresholdMinutes: Int = DEFAULT_STALE_THRESHOLD_MINUTES,
    ): Boolean {
        val frameMs = parseFrameEpochMs(frameTimestamp) ?: return true
        return isCellFrameStale(frameMs, nowEpochMs, staleThresholdMinutes)
    }

    /**
     * Determine whether a `DisplayCell` is a suspect (unconfirmed radar-only) cell vs a lightning-confirmed core.
     */
    fun isSuspectCell(cell: DisplayCell): Boolean {
        val sev = cell.severity.trim().lowercase(Locale.US)
        if (sev == "suspect" || sev == "unconfirmed" || sev == "rain_ring") return true
        if (cell.id.startsWith("suspect", ignoreCase = true)) return true
        return false
    }

    /**
     * Filter operational cells according to `suppressSuspect`.
     *
     * - When `suppressSuspect == false` (Operational OFF / show all), all cells are kept.
     * - When `suppressSuspect == true` (Operational ON), suspect cells are removed so only
     *   confirmed convective cells remain.
     */
    fun filterOperationalCells(
        display: CellDisplay,
        suppressSuspect: Boolean,
    ): CellDisplay {
        if (!suppressSuspect) return display
        val currentCells = display.cells ?: return display
        val filtered = currentCells.filterNot { isSuspectCell(it) }
        return display.copy(cells = filtered)
    }

    /**
     * Project a cell's centroid forward along its motion track (`dirFromDeg` bearing in degrees true,
     * `speedKt` in knots) by `minutesAhead` minutes (default 30.0 min).
     *
     * Returns `(projectedLat, projectedLon)`.
     */
    fun projectCellArrow(
        lat: Double,
        lon: Double,
        speedKt: Double,
        dirFromDeg: Double,
        minutesAhead: Double = DEFAULT_PROJECTION_MINUTES,
    ): Pair<Double, Double> {
        if (speedKt <= 0.0 || minutesAhead <= 0.0 || speedKt.isNaN() || dirFromDeg.isNaN()) {
            return lat to lon
        }
        val distanceKm = speedKt * 1.852 * (minutesAhead / 60.0)
        return projectPosition(lat = lat, lon = lon, bearingDeg = dirFromDeg, distanceKm = distanceKm)
    }

    /**
     * Project a `(lat, lon)` coordinate along a true bearing (`0° = N`, `90° = E`) by `distanceKm`.
     */
    fun projectPosition(
        lat: Double,
        lon: Double,
        bearingDeg: Double,
        distanceKm: Double,
    ): Pair<Double, Double> {
        if (distanceKm <= 0.0) return lat to lon
        val latRad = lat * PI / 180.0
        val bearingRad = bearingDeg * PI / 180.0
        val dLat = (distanceKm / 111.32) * cos(bearingRad)
        val cosLat = max(0.01, abs(cos(latRad)))
        val dLon = (distanceKm / (111.32 * cosLat)) * sin(bearingRad)
        return (lat + dLat) to (lon + dLon)
    }

    /**
     * Classify a Blitzortung lightning strike's age (`refEpochSec - flashEpochSec`) into a [LightningAgeBucket]:
     * - `0..5 min` (`<= 300s`): [LightningAgeBucket.FRESH_5M]
     * - `5..15 min` (`301..900s`): [LightningAgeBucket.RECENT_15M]
     * - `15..30 min` (`901..1800s`): [LightningAgeBucket.OLDER_30M]
     * - `> 30 min` (`> 1800s`) or `> 2 min` in the future: [LightningAgeBucket.EXPIRED]
     */
    fun classifyFlashAge(
        flashEpochSec: Long,
        refEpochSec: Long,
    ): LightningAgeBucket {
        val ageSec = refEpochSec - flashEpochSec
        if (ageSec < -MAX_FUTURE_SLOP_SECONDS) return LightningAgeBucket.EXPIRED
        val effectiveAge = max(0L, ageSec)
        return when {
            effectiveAge <= LightningAgeBucket.FRESH_5M.maxAgeSeconds -> LightningAgeBucket.FRESH_5M
            effectiveAge <= LightningAgeBucket.RECENT_15M.maxAgeSeconds -> LightningAgeBucket.RECENT_15M
            effectiveAge <= LightningAgeBucket.OLDER_30M.maxAgeSeconds -> LightningAgeBucket.OLDER_30M
            else -> LightningAgeBucket.EXPIRED
        }
    }
}
