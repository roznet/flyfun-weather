package aero.flyfun.weather.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class PackMetaResponse(
    @SerialName("flight_id")
    val flightId: String = "",
    @SerialName("fetch_timestamp")
    val fetchTimestamp: String = "",
    @SerialName("days_out")
    val daysOut: Int = 0,
    @SerialName("has_gramet")
    val hasGramet: Boolean = false,
    @SerialName("has_skewt")
    val hasSkewt: Boolean = false,
    @SerialName("has_digest")
    val hasDigest: Boolean = false,
    @SerialName("has_advisories")
    val hasAdvisories: Boolean = false,
    val assessment: String? = null,
    @SerialName("assessment_reason")
    val assessmentReason: String? = null,
    @SerialName("diagnostics_count")
    val diagnosticsCount: Int? = null,
    @SerialName("watcher_changes_summary")
    val watcherChangesSummary: String? = null,
    @SerialName("model_init_times")
    val modelInitTimes: Map<String, Int> = emptyMap(),
    @SerialName("grib_init_times")
    val gribInitTimes: Map<String, Int>? = null,
    @SerialName("models_skipped_region")
    val modelsSkippedRegion: List<String> = emptyList(),
    @SerialName("has_alt_advisories")
    val hasAltAdvisories: Boolean? = null,
    @SerialName("alt_assessment")
    val altAssessment: String? = null,
    @SerialName("alt_departure_time")
    val altDepartureTime: String? = null,
    @SerialName("data_status")
    val dataStatus: DataStatus? = null,
    @SerialName("is_historical")
    val isHistorical: Boolean = false,
    @SerialName("has_pireps")
    val hasPireps: Boolean? = null,
    val charts: PackChartsManifest? = null,
) {
    val id: String
        get() = "$flightId-$fetchTimestamp"

    val resolvedAssessment: Assessment
        get() = Assessment.fromString(assessment)

    val resolvedAltAssessment: Assessment
        get() = Assessment.fromString(altAssessment)

    val fetchEpochMs: Long?
        get() = IsoTime.parseEpochMs(fetchTimestamp)
}

@Serializable
data class DataStatus(
    val fresh: Boolean = true,
    @SerialName("stale_models")
    val staleModels: List<String> = emptyList(),
    @SerialName("model_init_times")
    val modelInitTimes: Map<String, Int> = emptyMap(),
    @SerialName("next_expected_update")
    val nextExpectedUpdate: String? = null,
    @SerialName("next_expected_model")
    val nextExpectedModel: String? = null,
)

@Serializable
data class PackChartsManifest(
    @SerialName("in_coverage")
    val inCoverage: Boolean = false,
    val sources: List<PackChartSource> = emptyList(),
)

@Serializable
data class PackChartSource(
    val source: String = "",
    val label: String = "",
    val attribution: String = "",
    @SerialName("default_chart_id")
    val defaultChartId: String? = null,
    @SerialName("overlay_url")
    val overlayUrl: String? = null,
    val charts: List<PackChart> = emptyList(),
) {
    val id: String
        get() = source
}

@Serializable
data class PackChart(
    @SerialName("chart_id")
    val chartId: String = "",
    val label: String = "",
    val caption: String = "",
    @SerialName("valid_time")
    val validTime: String? = null,
    @SerialName("init_time")
    val initTime: String? = null,
    val url: String = "",
    @SerialName("overlay_key")
    val overlayKey: String? = null,
    @SerialName("cache_key")
    val cacheKey: String = "",
    @SerialName("file_ext")
    val fileExt: String = "png",
    val available: Boolean = true,
    val prefetch: Boolean = false,
) {
    val id: String
        get() = chartId

    /** Relative path inside the shared on-disk chart cache: `"<cacheKey>.<fileExt>"`. */
    val cachePath: String
        get() = "$cacheKey.$fileExt"

    companion object {
        /**
         * Magic-byte check so an HTML/JSON error page returned behind a 200 (proxy hiccup)
         * is never cached under an immutable key.
         * Supports PNG (`89 50 4E 47`), GIF (`GIF87a`/`GIF89a`), JPEG (`FF D8 FF`), and WebP (`RIFF....WEBP`).
         */
        fun looksLikeImage(bytes: ByteArray): Boolean {
            if (bytes.size < 4) return false
            val b0 = bytes[0].toInt() and 0xFF
            val b1 = bytes[1].toInt() and 0xFF
            val b2 = bytes[2].toInt() and 0xFF
            val b3 = bytes[3].toInt() and 0xFF

            // PNG: 89 50 4E 47
            if (b0 == 0x89 && b1 == 0x50 && b2 == 0x4E && b3 == 0x47) return true
            // GIF: 'G' 'I' 'F' '8'
            if (b0 == 0x47 && b1 == 0x49 && b2 == 0x46 && b3 == 0x38) return true
            // JPEG: FF D8 FF
            if (b0 == 0xFF && b1 == 0xD8 && b2 == 0xFF) return true
            // WebP: 'R' 'I' 'F' 'F' .... 'W' 'E' 'B' 'P'
            if (bytes.size >= 12 &&
                b0 == 0x52 && b1 == 0x49 && b2 == 0x46 && b3 == 0x46 &&
                (bytes[8].toInt() and 0xFF) == 0x57 &&
                (bytes[9].toInt() and 0xFF) == 0x45 &&
                (bytes[10].toInt() and 0xFF) == 0x42 &&
                (bytes[11].toInt() and 0xFF) == 0x50
            ) {
                return true
            }
            return false
        }
    }
}

@Serializable
data class ChartOverlayWaypoint(
    val icao: String = "",
    val x: Double = 0.0,
    val y: Double = 0.0,
) {
    val id: String
        get() = icao
}

@Serializable
data class ChartOverlayEntry(
    val width: Int = 0,
    val height: Int = 0,
    val waypoints: List<ChartOverlayWaypoint> = emptyList(),
)

typealias ChartOverlay = Map<String, ChartOverlayEntry>

@Serializable
data class RefreshEvent(
    val type: String = "",
    val stage: String? = null,
    val detail: String? = null,
    val progress: Double? = null,
    val pack: PackMetaResponse? = null,
    val message: String? = null,
    val elapsed: Double? = null,
)

@Serializable
data class RefreshDecision(
    val action: String = "",
    val reason: String = "",
    @SerialName("stale_models")
    val staleModels: List<String>? = null,
    @SerialName("next_expected_model")
    val nextExpectedModel: String? = null,
    @SerialName("next_expected_time")
    val nextExpectedTime: String? = null,
)

@Serializable
data class RefreshStatusResponse(
    val status: String = "",
    @SerialName("flight_id")
    val flightId: String = "",
    val stage: String? = null,
    val detail: String? = null,
    @SerialName("triggered_by")
    val triggeredBy: String? = null,
    @SerialName("queued_at")
    val queuedAt: String? = null,
)

@Serializable
data class ActiveRefreshResponse(
    val active: Boolean = false,
    val stage: String? = null,
    val detail: String? = null,
    @SerialName("triggered_by")
    val triggeredBy: String? = null,
)
