package aero.flyfun.weather.model.rules

import aero.flyfun.weather.model.LiveRibbon
import aero.flyfun.weather.model.LiveRibbonSegment
import aero.flyfun.weather.model.RibbonSigmet
import aero.flyfun.weather.model.RibbonStation
import aero.flyfun.weather.model.RibbonWaypoint
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * VIP radar precipitation intensity classification and cockpit palette rules.
 */
enum class VipIntensity(
    val wireValue: String,
    val rank: Int,
    val label: String,
    val hexColor: String,
    val argbHex: Long,
) {
    NONE("none", 0, "No Echo", "#6b7280", 0xFF6B7280L),
    LIGHT("light", 1, "Light Rain", "#22c55e", 0xFF22C55EL),
    MODERATE("moderate", 2, "Moderate Rain", "#eab308", 0xFFEAB308L),
    HEAVY("heavy", 3, "Heavy Rain", "#f97316", 0xFFF97316L),
    VERY_HEAVY("very_heavy", 4, "Very Heavy Rain", "#ef4444", 0xFFEF4444L),
    EXTREME("extreme", 5, "Extreme Convection", "#7f1d1d", 0xFF7F1D1DL);

    companion object {
        fun fromString(raw: String?): VipIntensity = when (raw?.trim()?.lowercase(Locale.US)) {
            "light", "vip1", "vip_1" -> LIGHT
            "moderate", "mod", "vip2", "vip_2" -> MODERATE
            "heavy", "hvy", "vip3", "vip_3", "vip4", "vip_4" -> HEAVY
            "very_heavy", "very-heavy", "veryheavy", "intense", "vip5", "vip_5" -> VERY_HEAVY
            "extreme", "ext", "vip6", "vip_6", "severe" -> EXTREME
            "none", "", null -> NONE
            else -> NONE
        }

        /**
         * Palette hex color for a wire intensity class:
         * `light` -> `#22c55e`, `moderate` -> `#eab308`, `heavy` -> `#f97316`,
         * `very_heavy` -> `#ef4444`, `purple` -> `#a855f7`, `extreme` -> `#7f1d1d`.
         */
        fun radarHexColor(raw: String?): String = when (raw?.trim()?.lowercase(Locale.US)) {
            "none", "", null -> "#22c55e"
            "light", "vip1", "vip_1" -> "#22c55e"
            "moderate", "mod", "vip2", "vip_2" -> "#eab308"
            "heavy", "hvy", "vip3", "vip_3" -> "#f97316"
            "very_heavy", "very-heavy", "veryheavy", "intense" -> "#ef4444"
            "purple", "hail" -> "#a855f7"
            "extreme", "ext", "vip6", "vip_6", "severe" -> "#7f1d1d"
            else -> "#22c55e"
        }

        fun fromDbz(dbz: Double?): VipIntensity {
            if (dbz == null || dbz.isNaN() || dbz < 15.0) return NONE
            return when {
                dbz >= 55.0 -> EXTREME
                dbz >= 50.0 -> VERY_HEAVY
                dbz >= 40.0 -> HEAVY
                dbz >= 30.0 -> MODERATE
                else -> LIGHT
            }
        }
    }
}

/**
 * Normalized route ribbon bin with clamped `[0.0, 1.0]` route fractions and VIP/flight-category metadata.
 */
data class NormalizedRibbonBin(
    val index: Int,
    val startNm: Double,
    val endNm: Double,
    val startFraction: Double,
    val endFraction: Double,
    val widthFraction: Double,
    val worstVipIntensity: String,
    val vipHexColor: String,
    val vipArgb: Long,
    val maxDbz: Double?,
    val radarCoverage: Double?,
    val topsMaxFt: Double?,
    val flashCount: Int,
    val lightningStatus: String,
    val segment: LiveRibbonSegment,
) {
    val midpointNm: Double get() = (startNm + endNm) * 0.5
    val midpointFraction: Double get() = (startFraction + endFraction) * 0.5
    val hasPrecipitation: Boolean
        get() = VipIntensity.fromString(worstVipIntensity) != VipIntensity.NONE ||
            ((radarCoverage ?: 0.0) > 0.0)
}

/**
 * Station projected along the route ribbon in `[0.0, 1.0]` fraction space.
 */
data class ProjectedRibbonStation(
    val icao: String,
    val name: String?,
    val enrouteDistanceNm: Double,
    val offsetNm: Double,
    val routeFraction: Double,
    val flightCategory: String?,
    val station: RibbonStation,
)

/**
 * Clamped SIGMET span along the route ribbon in both nm and `[0.0, 1.0]` fraction space.
 */
data class ClampedSigmetSpan(
    val id: String,
    val hazard: String,
    val startNm: Double,
    val endNm: Double,
    val startFraction: Double,
    val endFraction: Double,
    val widthFraction: Double,
    val sigmet: RibbonSigmet,
)

/**
 * Formatted tap-inspector summary for a ribbon bin, station, or SIGMET.
 */
data class RibbonInspectorSummary(
    val title: String,
    val subtitle: String,
    val badges: List<String>,
    val details: List<String>,
    val category: String? = null,
    val severityColorHex: String? = null,
)

/**
 * Pure geometry and formatting rules for the Observed Route Ribbon and its tap inspector.
 * Direct Kotlin port of iOS `RouteRibbonRules.swift` and `RouteRibbonInspectorRules.swift`.
 */
object RouteRibbonRules {

    /** Default corridor filter threshold (nm) for station dots on the ribbon. */
    const val DEFAULT_CORRIDOR_NM: Double = 25.0

    // -------------------------------------------------------------------------
    // Models mirroring iOS RouteRibbonRules
    // -------------------------------------------------------------------------

    data class RibbonBin(
        val id: Int,
        val startNm: Double,
        val endNm: Double,
        val midpointNm: Double,
        val radarCoverage: Double,
        val maxVip: Int,
        val maxDbz: Double?,
        val flashCount: Int,
        val worstIntensityClass: String,
    ) {
        val lengthNm: Double get() = max(0.0, endNm - startNm)
        val hasPrecipitation: Boolean get() = maxVip >= 1 && radarCoverage > 0.0
    }

    data class StationDot(
        val id: String,
        val icao: String,
        val name: String?,
        val enrouteDistanceNm: Double,
        val offsetNm: Double,
        val flightCategory: String?,
        val ceilingFt: Int?,
        val visibilityMeters: Int?,
        val windAdvisory: String?,
        val hasChange: Boolean,
    )

    data class SigmetTick(
        val id: String,
        val hazard: String,
        val qualifier: String?,
        val startNm: Double,
        val endNm: Double,
        val clampedStartNm: Double,
        val clampedEndNm: Double,
        val validTo: String,
        val baseFt: Int?,
        val topFt: Int?,
        val firId: String,
        val isNew: Boolean,
    ) {
        val midpointNm: Double get() = (clampedStartNm + clampedEndNm) * 0.5
        val spanNm: Double get() = max(0.0, clampedEndNm - clampedStartNm)
    }

    data class WaypointTick(
        val id: String,
        val icao: String,
        val name: String?,
        val distanceNm: Double,
    )

    data class AircraftTick(
        val distanceNm: Double,
        val fractionOfRoute: Double,
    )

    // -------------------------------------------------------------------------
    // VIP Radar Intensity Rules
    // -------------------------------------------------------------------------

    fun vipColorHex(intensity: String?): String = VipIntensity.radarHexColor(intensity)

    fun vipArgb(intensity: String?): Long = VipIntensity.fromString(intensity).argbHex

    fun vipRank(intensity: String?): Int = VipIntensity.fromString(intensity).rank

    fun vipLabel(intensity: String?): String = VipIntensity.fromString(intensity).label

    fun resolveSegmentVipClass(segment: LiveRibbonSegment): String {
        val explicit = segment.radarStatus.trim().lowercase(Locale.US)
        if (explicit.isNotEmpty() && explicit != "none") {
            return VipIntensity.fromString(explicit).wireValue
        }
        val dbzVip = VipIntensity.fromDbz(segment.radarMaxDbz)
        if (dbzVip != VipIntensity.NONE) {
            return dbzVip.wireValue
        }
        return VipIntensity.NONE.wireValue
    }

    // -------------------------------------------------------------------------
    // Normalization & Coordinate Clamping Rules
    // -------------------------------------------------------------------------

    /**
     * Normalize ribbon bins with clamped `[0.0, 1.0]` route fractions, VIP classes, and colors.
     */
    fun normalizeBins(
        segments: List<LiveRibbonSegment>,
        totalRouteNm: Double,
    ): List<NormalizedRibbonBin> {
        val inferredTotal = if (totalRouteNm > 0.0) {
            totalRouteNm
        } else {
            segments.maxOfOrNull { max(it.startNm, it.endNm) } ?: 0.0
        }
        if (segments.isEmpty()) return emptyList()

        return segments.mapIndexed { idx, seg ->
            val rawStart = min(seg.startNm, seg.endNm)
            val rawEnd = max(seg.startNm, seg.endNm)
            val clampedStartNm = if (inferredTotal > 0.0) rawStart.coerceIn(0.0, inferredTotal) else max(0.0, rawStart)
            val clampedEndNm = if (inferredTotal > 0.0) rawEnd.coerceIn(clampedStartNm, inferredTotal) else max(clampedStartNm, rawEnd)
            val startFrac = if (inferredTotal > 0.0) (clampedStartNm / inferredTotal).coerceIn(0.0, 1.0) else 0.0
            val endFrac = if (inferredTotal > 0.0) (clampedEndNm / inferredTotal).coerceIn(startFrac, 1.0) else 0.0
            val vipClass = resolveSegmentVipClass(seg)

            NormalizedRibbonBin(
                index = seg.index ?: idx,
                startNm = clampedStartNm,
                endNm = clampedEndNm,
                startFraction = startFrac,
                endFraction = endFrac,
                widthFraction = (endFrac - startFrac).coerceIn(0.0, 1.0),
                worstVipIntensity = vipClass,
                vipHexColor = VipIntensity.radarHexColor(vipClass),
                vipArgb = VipIntensity.fromString(vipClass).argbHex,
                maxDbz = seg.radarMaxDbz,
                radarCoverage = seg.radarCoverage,
                topsMaxFt = seg.topsMaxFt,
                flashCount = seg.flashCount,
                lightningStatus = seg.lightningStatus,
                segment = seg,
            )
        }
    }

    fun normalizeBins(ribbon: LiveRibbon): List<NormalizedRibbonBin> =
        normalizeBins(ribbon.segments, ribbon.totalDistanceNm)

    /**
     * Project a station distance onto the route as a clamped `[0.0, 1.0]` fraction.
     */
    fun stationFraction(distanceAlongRoute: Double, totalRouteDistance: Double): Double {
        if (totalRouteDistance <= 0.0 || distanceAlongRoute.isNaN()) return 0.0
        return (distanceAlongRoute / totalRouteDistance).coerceIn(0.0, 1.0)
    }

    /**
     * Project stations along the route into `[0.0, 1.0]` fraction coordinates, sorted by route distance.
     */
    fun projectStations(
        stations: List<RibbonStation>,
        totalRouteNm: Double,
        maxOffsetNm: Double? = null,
    ): List<ProjectedRibbonStation> {
        return stations
            .filter { st ->
                maxOffsetNm == null || abs(st.offsetNm) <= maxOffsetNm
            }
            .sortedBy { it.enrouteDistanceNm }
            .map { st ->
                val cat = (st.metarCategory ?: st.tafCategory ?: st.modelCategory)?.uppercase(Locale.US)
                ProjectedRibbonStation(
                    icao = st.icao,
                    name = st.name,
                    enrouteDistanceNm = st.enrouteDistanceNm,
                    offsetNm = st.offsetNm,
                    routeFraction = stationFraction(st.enrouteDistanceNm, totalRouteNm),
                    flightCategory = cat,
                    station = st,
                )
            }
    }

    /**
     * Clamp an arbitrary `[startNm, endNm]` span to `[0.0, totalRouteNm]`.
     * Returns `null` if `totalRouteNm <= 0` or the span lies completely outside `[0, totalRouteNm]`.
     */
    fun clampSpan(startNm: Double, endNm: Double, totalRouteNm: Double): Pair<Double, Double>? {
        if (totalRouteNm <= 0.0) return null
        val lo = min(startNm, endNm)
        val hi = max(startNm, endNm)
        if (hi < 0.0 || lo > totalRouteNm) return null
        val clampedLo = lo.coerceIn(0.0, totalRouteNm)
        val clampedHi = hi.coerceIn(clampedLo, totalRouteNm)
        return clampedLo to clampedHi
    }

    /**
     * Clamp SIGMET spans to `[0.0, totalRouteNm]` and compute `[0.0, 1.0]` fractions.
     * Drops SIGMETs completely outside the route range.
     */
    fun clampSigmetSpans(
        sigmets: List<RibbonSigmet>,
        totalRouteNm: Double,
    ): List<ClampedSigmetSpan> {
        if (totalRouteNm <= 0.0) return emptyList()
        return sigmets.mapNotNull { sig ->
            val rawStart = sig.startNm ?: 0.0
            val rawEnd = sig.endNm ?: totalRouteNm
            val (clampedStart, clampedEnd) = clampSpan(rawStart, rawEnd, totalRouteNm) ?: return@mapNotNull null
            val startFrac = (clampedStart / totalRouteNm).coerceIn(0.0, 1.0)
            val endFrac = (clampedEnd / totalRouteNm).coerceIn(startFrac, 1.0)
            ClampedSigmetSpan(
                id = sig.key,
                hazard = sig.hazard,
                startNm = clampedStart,
                endNm = clampedEnd,
                startFraction = startFrac,
                endFraction = endFrac,
                widthFraction = (endFrac - startFrac).coerceIn(0.0, 1.0),
                sigmet = sig,
            )
        }
    }

    /**
     * Compute aircraft progress fraction (`[0.0, 1.0]`) along the route.
     * Returns `null` when aircraft distance is unknown or `totalRouteDistance <= 0`.
     */
    fun flownFraction(aircraftAlongDistance: Double?, totalRouteDistance: Double): Double? {
        if (aircraftAlongDistance == null || aircraftAlongDistance.isNaN() || totalRouteDistance <= 0.0) {
            return null
        }
        return (aircraftAlongDistance / totalRouteDistance).coerceIn(0.0, 1.0)
    }

    // -------------------------------------------------------------------------
    // Swift-parity extraction helpers
    // -------------------------------------------------------------------------

    fun extractBins(segments: List<LiveRibbonSegment>): List<RibbonBin> {
        return segments.mapIndexed { idx, seg ->
            val lo = min(seg.startNm, seg.endNm)
            val hi = max(seg.startNm, seg.endNm)
            val vipClass = resolveSegmentVipClass(seg)
            val maxVip = VipIntensity.fromString(vipClass).rank
            val coverage = (seg.radarCoverage ?: 0.0).coerceIn(0.0, 1.0)
            RibbonBin(
                id = seg.index ?: idx,
                startNm = lo,
                endNm = hi,
                midpointNm = (lo + hi) * 0.5,
                radarCoverage = coverage,
                maxVip = maxVip,
                maxDbz = seg.radarMaxDbz,
                flashCount = max(0, seg.flashCount),
                worstIntensityClass = vipClass,
            )
        }
    }

    fun extractStations(
        stations: List<RibbonStation>,
        maxOffsetNm: Double = DEFAULT_CORRIDOR_NM,
    ): List<StationDot> {
        return stations
            .filter { abs(it.offsetNm) <= maxOffsetNm }
            .sortedBy { it.enrouteDistanceNm }
            .map { st ->
                StationDot(
                    id = st.icao,
                    icao = st.icao,
                    name = st.name,
                    enrouteDistanceNm = st.enrouteDistanceNm,
                    offsetNm = st.offsetNm,
                    flightCategory = st.metarCategory ?: st.tafCategory ?: st.modelCategory,
                    ceilingFt = st.metarCeilingFt,
                    visibilityMeters = st.metarVisibilityM,
                    windAdvisory = st.windAdvisory,
                    hasChange = st.hasChange,
                )
            }
    }

    fun extractSigmets(
        sigmets: List<RibbonSigmet>,
        totalDistanceNm: Double,
    ): List<SigmetTick> {
        val total = max(0.0, totalDistanceNm)
        return sigmets.mapNotNull { sig ->
            val rawLo = min(sig.startNm ?: 0.0, sig.endNm ?: total)
            val rawHi = max(sig.startNm ?: 0.0, sig.endNm ?: total)
            val clampedLo = rawLo.coerceIn(0.0, total)
            val clampedHi = rawHi.coerceIn(0.0, total)
            if (total > 0.0 && clampedHi <= 0.0 && rawHi <= 0.0) return@mapNotNull null
            if (total > 0.0 && clampedLo >= total && rawLo >= total) return@mapNotNull null
            SigmetTick(
                id = sig.key,
                hazard = sig.hazard.ifEmpty { "SIGMET" },
                qualifier = sig.qualifier,
                startNm = rawLo,
                endNm = rawHi,
                clampedStartNm = clampedLo,
                clampedEndNm = max(clampedLo, clampedHi),
                validTo = sig.validTo,
                baseFt = sig.baseFt,
                topFt = sig.topFt,
                firId = sig.firId,
                isNew = sig.isNew,
            )
        }
    }

    fun extractWaypoints(waypoints: List<RibbonWaypoint>): List<WaypointTick> {
        return waypoints.mapIndexed { idx, wp ->
            WaypointTick(
                id = "${idx}-${wp.icao}",
                icao = wp.icao,
                name = wp.name,
                distanceNm = wp.distanceNm,
            )
        }
    }

    fun extractAircraftTick(distanceNm: Double?, totalDistanceNm: Double): AircraftTick? {
        val frac = flownFraction(distanceNm, totalDistanceNm) ?: return null
        val clamped = distanceNm!!.coerceIn(0.0, totalDistanceNm)
        return AircraftTick(distanceNm = clamped, fractionOfRoute = frac)
    }

    // -------------------------------------------------------------------------
    // Inspector Formatting Rules (inspectBin, inspectStation, inspectSigmet)
    // -------------------------------------------------------------------------

    fun inspectBin(
        segment: LiveRibbonSegment,
        totalRouteNm: Double = 0.0,
        stations: List<RibbonStation> = emptyList(),
        sigmets: List<RibbonSigmet> = emptyList(),
    ): RibbonInspectorSummary {
        val startNm = segment.startNm.roundToInt()
        val endNm = segment.endNm.roundToInt()
        val placeLabel = segment.place?.text?.takeIf { it.isNotBlank() }
        val title = if (placeLabel != null) {
            "$placeLabel ($startNm–$endNm nm)"
        } else {
            "Route Segment $startNm–$endNm nm"
        }

        val vipClass = resolveSegmentVipClass(segment)
        val vipEnum = VipIntensity.fromString(vipClass)
        val headline = vipHeadline(vipEnum.rank)

        val badges = buildList {
            if (vipEnum != VipIntensity.NONE) add(vipEnum.label)
            if (segment.flashCount > 0) add("${segment.flashCount} lightning")
            if (segment.topsStatus != "none" && segment.topsMaxFt != null) {
                add("Tops ${formatAltitudeFl(segment.topsMaxFt.roundToInt())}")
            }
        }

        val details = buildList {
            add(headline)
            if (segment.radarMaxDbz != null) {
                add(String.format(Locale.US, "Max reflectivity: %.0f dBZ", segment.radarMaxDbz))
            }
            if (segment.radarCoverage != null && segment.radarCoverage > 0.0) {
                add(String.format(Locale.US, "Precipitation coverage: %.0f%%", segment.radarCoverage * 100.0))
            }
            if (segment.nearestFlashNm != null) {
                add(String.format(Locale.US, "Nearest lightning: %.1f nm", segment.nearestFlashNm))
            }
            if (totalRouteNm > 0.0) {
                val fracPct = (((segment.startNm + segment.endNm) * 0.5 / totalRouteNm) * 100.0)
                    .coerceIn(0.0, 100.0)
                add(String.format(Locale.US, "Route progress: %.0f%%", fracPct))
            }
            val nearStations = stations.filter {
                it.enrouteDistanceNm in (segment.startNm - 10.0)..(segment.endNm + 10.0)
            }
            if (nearStations.isNotEmpty()) {
                add("Nearby stations: ${nearStations.joinToString(", ") { it.icao }}")
            }
            val activeSigmets = sigmets.filter { sig ->
                (sig.endNm ?: 0.0) >= segment.startNm && (sig.startNm ?: 0.0) <= segment.endNm
            }
            if (activeSigmets.isNotEmpty()) {
                add("Active advisories: ${activeSigmets.joinToString(", ") { it.hazard.ifEmpty { it.key } }}")
            }
        }

        return RibbonInspectorSummary(
            title = title,
            subtitle = headline,
            badges = badges,
            details = details,
            category = null,
            severityColorHex = VipIntensity.radarHexColor(vipClass),
        )
    }

    fun inspectStation(station: RibbonStation): RibbonInspectorSummary {
        val title = if (!station.name.isNullOrBlank()) {
            "${station.icao} — ${station.name}"
        } else {
            station.icao
        }

        val distParts = buildList {
            add(String.format(Locale.US, "%.0f nm along route", station.enrouteDistanceNm))
            if (abs(station.offsetNm) > 0.1) {
                add(String.format(Locale.US, "%.1f nm off track", abs(station.offsetNm)))
            }
        }
        val subtitle = distParts.joinToString(" · ")
        val category = (station.metarCategory ?: station.tafCategory ?: station.modelCategory)?.uppercase(Locale.US)

        val badges = buildList {
            if (!category.isNullOrBlank()) add(category)
            if (!station.windAdvisory.isNullOrBlank() && station.windAdvisory != "none") {
                add("Wind: ${station.windAdvisory}")
            }
            if (station.hasChange && !station.changeDirection.isNullOrBlank()) {
                add(station.changeDirection.replaceFirstChar { it.titlecase(Locale.US) })
            }
        }

        val details = buildList {
            formatCeiling(station.metarCeilingFt)?.let { add("Ceiling: $it") }
            formatVisibility(station.metarVisibilityM)?.let { add("Visibility: $it") }
            if (!station.tafCategory.isNullOrBlank()) {
                add("TAF category: ${station.tafCategory.uppercase(Locale.US)}")
            }
            if (!station.modelCategory.isNullOrBlank()) {
                add("Model category: ${station.modelCategory.uppercase(Locale.US)}")
            }
            val wxLabels = station.weather?.mapNotNull { it.label.ifEmpty { it.code }.takeIf { s -> s.isNotBlank() } }
            if (!wxLabels.isNullOrEmpty()) {
                add("Weather: ${wxLabels.joinToString(", ")}")
            }
            if (station.obsProxy != null) {
                add(String.format(Locale.US, "Obs proxy: %s (%.1f nm)", station.obsProxy.icao, station.obsProxy.distanceNm))
            }
        }

        return RibbonInspectorSummary(
            title = title,
            subtitle = subtitle,
            badges = badges,
            details = details,
            category = category,
            severityColorHex = null,
        )
    }

    fun inspectSigmet(sigmet: RibbonSigmet): RibbonInspectorSummary {
        val title = sigmetHeader(
            hazard = sigmet.hazard,
            qualifier = sigmet.qualifier,
            firId = sigmet.firId,
        )
        val validity = sigmetValidityWindow(null, sigmet.validTo)
        val altBand = sigmetAltitudeBandFt(sigmet.baseFt, sigmet.topFt)
        val subtitle = listOfNotNull(validity, altBand).joinToString(" · ").ifEmpty { "Active route advisory" }

        val badges = buildList {
            add("SIGMET")
            if (sigmet.hazard.isNotBlank()) add(sigmet.hazard)
            if (altBand != null) add(altBand)
            if (sigmet.isNew) add("NEW")
        }

        val details = buildList {
            if (sigmet.startNm != null && sigmet.endNm != null) {
                add(String.format(Locale.US, "Route span: %.0f–%.0f nm", sigmet.startNm, sigmet.endNm))
            }
            if (validity != null) add(validity)
            if (altBand != null) add("Altitude: $altBand")
            if (sigmet.startPlace != null && sigmet.endPlace != null) {
                add("From ${sigmet.startPlace.text} to ${sigmet.endPlace.text}")
            }
        }

        return RibbonInspectorSummary(
            title = title,
            subtitle = subtitle,
            badges = badges,
            details = details,
            category = null,
            severityColorHex = "#ef4444",
        )
    }

    // -------------------------------------------------------------------------
    // Inspector Helper Functions
    // -------------------------------------------------------------------------

    fun binForDistance(bins: List<RibbonBin>, distanceNm: Double): RibbonBin? {
        if (bins.isEmpty()) return null
        bins.firstOrNull { distanceNm >= it.startNm && distanceNm <= it.endNm }?.let { return it }
        return bins.minByOrNull { abs(it.midpointNm - distanceNm) }
    }

    fun formatWind(dir: Int?, speedKt: Int?, gustKt: Int?): String? {
        if (dir == null && speedKt == null) return null
        val spd = speedKt ?: 0
        if (spd == 0 && (dir == null || dir == 0)) return "Calm"
        val dirStr = if (dir != null && dir > 0) String.format(Locale.US, "%03d°", dir) else "VRB"
        return if (gustKt != null && gustKt > spd) {
            "$dirStr ${spd}G${gustKt}kt"
        } else {
            "$dirStr ${spd}kt"
        }
    }

    fun formatVisibility(meters: Int?): String? {
        if (meters == null) return null
        if (meters >= 9999) return "10+ km"
        if (meters >= 5000) {
            val km = meters / 1000.0
            return String.format(Locale.US, "%.0f km", km)
        }
        return "$meters m"
    }

    fun formatCeiling(feet: Int?): String? {
        if (feet == null) return null
        return "$feet ft"
    }

    fun sigmetHeader(hazard: String, qualifier: String?, firId: String?): String {
        val phenom = listOfNotNull(qualifier?.takeIf { it.isNotBlank() }, hazard.takeIf { it.isNotBlank() })
            .joinToString(" ")
            .ifEmpty { "SIGMET" }
        return if (!firId.isNullOrBlank()) {
            "SIGMET · $phenom ($firId)"
        } else {
            "SIGMET · $phenom"
        }
    }

    fun sigmetValidityWindow(validFrom: String?, validTo: String?): String? {
        val fromHm = extractUtcHm(validFrom)
        val toHm = extractUtcHm(validTo)
        return when {
            fromHm != null && toHm != null -> "Valid $fromHm–$toHm"
            toHm != null -> "Valid until $toHm"
            fromHm != null -> "Valid from $fromHm"
            else -> null
        }
    }

    fun sigmetAltitudeBandFt(baseFt: Int?, topFt: Int?): String? {
        return when {
            baseFt != null && topFt != null -> "${formatAltitudeFl(baseFt)}–${formatAltitudeFl(topFt)}"
            topFt != null -> "SFC–${formatAltitudeFl(topFt)}"
            baseFt != null -> "${formatAltitudeFl(baseFt)}+"
            else -> null
        }
    }

    fun formatAltitudeFl(feet: Int): String {
        if (feet <= 0) return "SFC"
        if (feet >= 5000) {
            return String.format(Locale.US, "FL%03d", feet / 100)
        }
        return "${feet}ft"
    }

    fun vipHeadline(maxVip: Int): String = when {
        maxVip <= 0 -> "No radar echoes"
        maxVip == 1 -> "Light precipitation (VIP 1)"
        maxVip == 2 -> "Moderate precipitation (VIP 2)"
        maxVip == 3 -> "Heavy precipitation (VIP 3)"
        maxVip == 4 -> "Very heavy precipitation (VIP 4)"
        else -> "Extreme precipitation (VIP 5+)"
    }

    fun extractUtcHm(iso: String?): String? {
        if (iso.isNullOrBlank()) return null
        val tIndex = iso.indexOf('T')
        if (tIndex >= 0 && iso.length >= tIndex + 6) {
            val hm = iso.substring(tIndex + 1, tIndex + 6)
            return "${hm}Z"
        }
        return iso
    }
}
