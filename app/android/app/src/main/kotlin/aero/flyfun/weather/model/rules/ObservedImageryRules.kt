package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.IsoTime
import aero.flyfun.weather.model.ObservedConditions
import aero.flyfun.weather.model.ObservedFramesResponse
import aero.flyfun.weather.model.ObservedImageryFrame
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Observed imagery on the briefing route map (#998): radar (reflectivity or rain rate, one at a
 * time) over an optional satellite-infrared underlay, as Web Mercator tiles served by
 * `/api/observed/tiles/{source}/{stamp}/{z}/{x}/{y}.png`, keyed by the frame stamp listed by
 * `/api/observed/frames/{source}`. Pure, so what is drawn and what the badge says are unit-tested;
 * the canvas only applies the result.
 *
 * SYNC: iOS `Views/Map/ObservedMapImagery.swift`, web `route-map/observed-overlay-geometry.ts`.
 */
object ObservedImageryRules {
    /** Radar sources, in menu order. */
    val RADAR_SOURCES = listOf("opera_dbzh", "opera_rate")
    const val SATELLITE_SOURCE = "satellite_ir"

    /** Reflectivity by default: highest cadence, the one that answers "is that cell on my route". */
    const val DEFAULT_SELECTION = "opera_dbzh"
    const val DEFAULT_OPACITY = 0.75f
    /** Fixed, like web and iOS: the opacity control stays about the radar. */
    const val SATELLITE_OPACITY = 0.8f
    /** Opacity choices (the web slider is 10–100 %, iOS offers the same steps). */
    val OPACITY_STEPS = listOf(0.4f, 0.6f, 0.75f, 0.9f, 1.0f)
    /** A listing is re-fetched at most this often while the map is on screen. */
    const val POLL_INTERVAL_MS = 120_000L

    fun label(source: String): String = when (source) {
        "opera_dbzh" -> "Radar reflectivity"
        "opera_rate" -> "Radar rain rate"
        SATELLITE_SOURCE -> "Satellite infrared"
        else -> source
    }

    /** Chip name per source. */
    fun shortLabel(source: String): String = when (source) {
        "opera_dbzh", "opera_rate" -> "Radar"
        SATELLITE_SOURCE -> "Sat"
        else -> label(source)
    }

    /** Radar sources this briefing collected: an option that would draw an empty map is not offered. */
    fun availableRadar(observed: ObservedConditions?): List<String> {
        observed ?: return emptyList()
        return RADAR_SOURCES.filter { source ->
            when (source) {
                "opera_dbzh" -> observed.reflectivity != null
                "opera_rate" -> observed.rainRate != null
                else -> false
            }
        }
    }

    /**
     * The radar layer to draw for the pilot's pick. `""` ("None") draws nothing; only a pick this
     * briefing did not collect falls back, to reflectivity, else rain rate, else nothing.
     */
    fun resolveSelection(chosen: String, available: List<String>): String {
        if (chosen.isEmpty()) return ""
        if (chosen in available) return chosen
        return RADAR_SOURCES.firstOrNull { it in available } ?: ""
    }

    /** The frame to draw now, or null: a stale frame is never drawn as the present sky. */
    fun currentFrame(info: ObservedFramesResponse?): ObservedImageryFrame? =
        info?.takeIf { !it.stale }?.frames?.firstOrNull()

    /** Request path for one tile, from the listing's template. */
    fun tilePath(template: String, stamp: String, z: Int, x: Int, y: Int): String =
        template
            .replace("{stamp}", java.net.URLEncoder.encode(stamp, "UTF-8").replace("+", "%20"))
            .replace("{z}", z.toString())
            .replace("{x}", x.toString())
            .replace("{y}", y.toString())

    /** One tile layer to put on the map; satellite underneath, radar on top. */
    data class TileLayer(
        val source: String,
        val stamp: String,
        val template: String,
        val minZoom: Int,
        val maxZoom: Int,
        val opacity: Float,
    ) {
        /** Source + frame: a different frame is a different cache key, never a re-fetch. */
        val key: String get() = "$source|$stamp"
    }

    data class Composed(
        /** Draw order: the satellite underlay first, then the radar. */
        val layers: List<TileLayer>,
        /** Full lines for the details dialog, radar first. */
        val badges: List<String>,
        /** On-map summary chips, radar first. */
        val chips: List<ObservedSummaryChip>,
    )

    /**
     * What the map draws for the current picks. [frames] holds the listings received so far,
     * [failed] the sources whose listing could not be fetched.
     */
    fun compose(
        selection: String,
        showSatellite: Boolean,
        radarOpacity: Float,
        frames: Map<String, ObservedFramesResponse>,
        failed: Set<String>,
        nowMs: Long,
    ): Composed {
        val layers = mutableListOf<TileLayer>()
        val badges = mutableListOf<String>()
        val chips = mutableListOf<ObservedSummaryChip>()
        if (showSatellite) {
            val (layer, badge, chip) = layerAndBadge(SATELLITE_SOURCE, SATELLITE_OPACITY, frames, failed, nowMs)
            layer?.let(layers::add)
            badge?.let(badges::add)
            chip?.let(chips::add)
        }
        if (selection.isNotEmpty()) {
            val (layer, badge, chip) = layerAndBadge(selection, radarOpacity, frames, failed, nowMs)
            layer?.let(layers::add)
            badge?.let { badges.add(0, it) }
            chip?.let { chips.add(0, it) }
        }
        return Composed(layers, badges, chips)
    }

    private fun layerAndBadge(
        source: String,
        opacity: Float,
        frames: Map<String, ObservedFramesResponse>,
        failed: Set<String>,
        nowMs: Long,
    ): Triple<TileLayer?, String?, ObservedSummaryChip?> {
        val name = shortLabel(source)
        val info = frames[source]
            ?: return if (source in failed) {
                // Once the listing failed, say so: an empty map never reads as "no echoes".
                Triple(null, "${label(source)}: imagery unavailable", ObservedSummaryChip(name, "n/a", true))
            } else {
                Triple(null, null, null)
            }
        val frame = currentFrame(info)
            ?: return Triple(null, staleBadge(info, nowMs), ObservedSummaryChip(name, "stale", true))
        val age = frameAgeMinutes(frame, nowMs)
        val layer = TileLayer(source, frame.stamp, info.tileUrlTemplate, info.minZoom, info.maxZoom, opacity)
        return Triple(layer, frameBadge(info, frame, nowMs), ObservedSummaryChip(name, ObservedMapRules.chipAge(age), false))
    }

    private fun parseMs(iso: String?): Long? = iso?.takeIf { it.isNotBlank() }?.let(IsoTime::parseEpochMs)

    /** Age of a drawn frame from its own valid time. */
    fun frameAgeMinutes(frame: ObservedImageryFrame, nowMs: Long): Double {
        val valid = parseMs(frame.validTime) ?: return frame.ageMinutes ?: 0.0
        return max(0.0, (nowMs - valid) / 60_000.0)
    }

    /** "Radar reflectivity 14:05Z · 12 min old · 10 min rolling max · <attribution>". */
    fun frameBadge(info: ObservedFramesResponse, frame: ObservedImageryFrame, nowMs: Long): String {
        var text = ObservedMapRules.ageText(frame.validTime, frameAgeMinutes(frame, nowMs), info.label.ifEmpty { label(info.source) })
        val window = info.windowMinutes
        if (window != null && window > 0) text += " · ${window.roundToInt()} min rolling max"
        info.attribution?.text?.takeIf { it.isNotEmpty() }?.let { text += " · $it" }
        return text
    }

    /** A layer whose newest frame is too old to draw, so "hidden because stale" reads apart from "no echoes". */
    fun staleBadge(info: ObservedFramesResponse, nowMs: Long): String {
        val label = info.label.ifEmpty { label(info.source) }
        val newest = info.frames.firstOrNull()
        val valid = parseMs(newest?.validTime)
        val hhmm = AirportCardRules.hhmmZ(newest?.validTime)
        if (newest == null || valid == null || hhmm.isEmpty()) return "$label: no current frame"
        val age = ((nowMs - valid) / 60_000.0).roundToInt()
        return "$label: not shown, newest frame $hhmm is $age min old"
    }
}
