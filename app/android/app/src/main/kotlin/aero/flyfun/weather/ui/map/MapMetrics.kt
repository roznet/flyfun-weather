package aero.flyfun.weather.ui.map

import androidx.compose.ui.graphics.Color
import aero.flyfun.weather.model.viz.ColorScales
import aero.flyfun.weather.model.viz.VizPoint
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Single swatch item rendered in the horizontal map legend strip.
 */
data class MapLegendSwatch(
    val label: String,
    val color: Color,
)

// ============================================================================
// 1. Briefing Route Map Segment Metrics (13 metrics + web/iOS ID aliases)
// ============================================================================

enum class RouteMetricGroup(val label: String) {
    HAZARDS("Hazards & Flight Cat"),
    WIND("Wind"),
    CLOUDS_SURFACE("Clouds & Surface"),
    LEVEL("At Altitude"),
}

/**
 * All 13 Briefing Route Map segment metrics:
 * `flight_category`, `wind_speed`, `headwind`, `crosswind`, `icing`, `turbulence`,
 * `convective`, `cloud_cover`, `ceiling`, `visibility`, `temperature`, `precipitation`, `agreement`
 * plus level-dependent variants (`sfip_at_level`, `cloud_at_level`, `cape`).
 */
enum class RouteMapMetric(
    val id: String,
    val label: String,
    val shortLabel: String,
    val unit: String,
    val group: RouteMetricGroup,
    val usesAltitude: Boolean = false,
    val aliases: List<String> = emptyList(),
) {
    FLIGHT_CATEGORY(
        id = "flight_category",
        label = "Flight Category (VFR/IFR)",
        shortLabel = "Flight Cat",
        unit = "",
        group = RouteMetricGroup.HAZARDS,
        aliases = listOf("flight-category", "category"),
    ),
    ICING(
        id = "icing",
        label = "Icing Risk",
        shortLabel = "Icing",
        unit = "risk",
        group = RouteMetricGroup.HAZARDS,
        usesAltitude = true,
        aliases = listOf("icing-risk-at-level", "icing_risk", "sfip-at-level"),
    ),
    TURBULENCE(
        id = "turbulence",
        label = "Turbulence / CAT",
        shortLabel = "Turbulence",
        unit = "risk",
        group = RouteMetricGroup.HAZARDS,
        usesAltitude = true,
        aliases = listOf("cat-risk-at-level", "cat_risk", "cat"),
    ),
    CONVECTIVE(
        id = "convective",
        label = "Convective Risk",
        shortLabel = "Convective",
        unit = "risk",
        group = RouteMetricGroup.HAZARDS,
        aliases = listOf("convective-risk", "convective_risk"),
    ),
    WIND_SPEED(
        id = "wind_speed",
        label = "Wind Speed",
        shortLabel = "Wind Spd",
        unit = "kt",
        group = RouteMetricGroup.WIND,
        aliases = listOf("wind-speed", "wind_speed_kt"),
    ),
    HEADWIND(
        id = "headwind",
        label = "Headwind / Tailwind",
        shortLabel = "Headwind",
        unit = "kt",
        group = RouteMetricGroup.WIND,
        aliases = listOf("tailwind", "headwind_kt"),
    ),
    CROSSWIND(
        id = "crosswind",
        label = "Crosswind",
        shortLabel = "Crosswind",
        unit = "kt",
        group = RouteMetricGroup.WIND,
        aliases = listOf("crosswind-kt", "crosswind_kt"),
    ),
    CLOUD_COVER(
        id = "cloud_cover",
        label = "Cloud Cover",
        shortLabel = "Clouds",
        unit = "%",
        group = RouteMetricGroup.CLOUDS_SURFACE,
        usesAltitude = true,
        aliases = listOf("cloud-cover-total", "cloud-cover-low", "cloud-at-level", "cloud_cover_pct"),
    ),
    CEILING(
        id = "ceiling",
        label = "Ceiling (AGL)",
        shortLabel = "Ceiling",
        unit = "ft",
        group = RouteMetricGroup.CLOUDS_SURFACE,
        aliases = listOf("nwp-ceiling", "ceiling_ft"),
    ),
    VISIBILITY(
        id = "visibility",
        label = "Visibility",
        shortLabel = "Visibility",
        unit = "km",
        group = RouteMetricGroup.CLOUDS_SURFACE,
        aliases = listOf("visibility_m", "vis"),
    ),
    TEMPERATURE(
        id = "temperature",
        label = "Temperature",
        shortLabel = "Temp",
        unit = "°C",
        group = RouteMetricGroup.LEVEL,
        usesAltitude = true,
        aliases = listOf("temp-at-level", "temp"),
    ),
    PRECIPITATION(
        id = "precipitation",
        label = "Precipitation / CAPE",
        shortLabel = "Precip",
        unit = "mm",
        group = RouteMetricGroup.CLOUDS_SURFACE,
        aliases = listOf("precip", "cape"),
    ),
    AGREEMENT(
        id = "agreement",
        label = "Model Agreement",
        shortLabel = "Agreement",
        unit = "",
        group = RouteMetricGroup.HAZARDS,
        aliases = listOf("model-agreement", "consensus"),
    );

    /**
     * Extract numeric value at a [VizPoint] for the given [altitudeFt].
     */
    fun extractValue(point: VizPoint, altitudeFt: Double): Double {
        return when (this) {
            FLIGHT_CATEGORY -> derivePointFlightCategoryIndex(point, altitudeFt)
            WIND_SPEED -> point.windSpeedKt ?: 0.0
            HEADWIND -> point.headwindKt ?: 0.0 // Positive = headwind, negative = tailwind
            CROSSWIND -> abs(point.crosswindKt ?: 0.0)
            ICING -> {
                val allIcing = point.icingZones + point.icingOgimetNwpZones + point.iengIcingZones
                val zoneAtAlt = allIcing
                    .filter { altitudeFt in (it.baseFt - 1500.0)..(it.topFt + 1500.0) }
                    .maxOfOrNull { riskIndex(it.risk) } ?: 0.0
                val sfipAtAlt = point.sfipZones
                    .filter { altitudeFt in (it.baseFt - 1500.0)..(it.topFt + 1500.0) }
                    .maxOfOrNull { riskIndex(it.risk) } ?: 0.0
                max(zoneAtAlt, sfipAtAlt)
            }
            TURBULENCE -> {
                val allCat = point.catLayers + point.eShearLayers
                allCat
                    .filter { altitudeFt in (it.baseFt - 1500.0)..(it.topFt + 1500.0) }
                    .maxOfOrNull { riskIndex(it.risk) } ?: 0.0
            }
            CONVECTIVE -> {
                val zoneRisk = max(
                    riskIndex(point.thermoConvective.riskLevel),
                    riskIndex(point.nwpConvective?.riskLevel ?: "none"),
                )
                if (zoneRisk > 0.0) zoneRisk else capeToRiskIndex(point.capeSurfaceJkg ?: 0.0)
            }
            CLOUD_COVER -> {
                val allClouds = point.nwpCloudLayers.ifEmpty { point.cloudLayers }
                val atLevel = allClouds
                    .filter { altitudeFt in (it.baseFt - 1000.0)..(it.topFt + 1000.0) }
                    .maxOfOrNull { it.coveragePct ?: ColorScales.coverageToPct(it.coverage) } ?: 0.0
                if (atLevel > 0.0) {
                    atLevel
                } else {
                    max(point.cloudCoverTotalPct ?: 0.0, point.cloudCoverLowPct ?: 0.0)
                }
            }
            CEILING -> {
                val candidates = listOfNotNull(
                    point.nwpCeilingFt,
                    point.soundingCeilingFt,
                    point.cloudLayers
                        .filter {
                            val cov = it.coverage.lowercase(Locale.US)
                            cov == "bkn" || cov == "broken" || cov == "ovc" || cov == "overcast" || (it.coveragePct ?: 0.0) >= 50.0
                        }
                        .minOfOrNull { max(0.0, it.baseFt - point.terrainElevationFt) },
                ).filter { it > 0.0 }
                candidates.minOrNull() ?: 99999.0
            }
            VISIBILITY -> {
                val visKm = point.visibilityMeters?.let { it / 1000.0 }
                if (visKm != null && visKm > 0.0) {
                    visKm.coerceAtMost(10.0)
                } else {
                    val ceilingFt = CEILING.extractValue(point, altitudeFt)
                    val precip = point.precipitationMm ?: 0.0
                    when {
                        ceilingFt < 500.0 || precip > 6.0 -> 1.2
                        ceilingFt < 1000.0 || precip > 3.0 -> 3.5
                        ceilingFt < 2500.0 || precip > 1.0 -> 6.5
                        (point.cloudCoverLowPct ?: 0.0) > 75.0 -> 8.5
                        else -> 10.0
                    }
                }
            }
            TEMPERATURE -> {
                val fzl = point.altitudeLines.freezingLevelFt
                if (fzl != null) {
                    (fzl - altitudeFt) / 1000.0 * 2.0
                } else {
                    point.temperatureCruiseC ?: point.temperatureC ?: 0.0
                }
            }
            PRECIPITATION -> {
                val precip = point.precipitationMm ?: 0.0
                if (precip > 0.0) {
                    precip
                } else {
                    ((point.capeSurfaceJkg ?: 0.0) / 250.0).coerceAtMost(10.0)
                }
            }
            AGREEMENT -> 1.0
        }
    }

    /**
     * Map extracted numeric value to segment stroke [Color].
     */
    fun color(value: Double): Color {
        return when (this) {
            FLIGHT_CATEGORY -> when (value.roundToInt()) {
                0 -> Color(0xFF22C55E) // VFR
                1 -> Color(0xFF3B82F6) // MVFR
                2 -> Color(0xFFEF4444) // IFR
                else -> Color(0xFFA855F7) // LIFR
            }
            WIND_SPEED -> when {
                value < 10.0 -> Color(0xFF93C5FD)
                value < 20.0 -> Color(0xFF3B82F6)
                value < 30.0 -> Color(0xFFF59E0B)
                value < 45.0 -> Color(0xFFEF4444)
                else -> Color(0xFF991B1B)
            }
            HEADWIND -> when {
                // Positive = headwind (warm/amber/red), Negative = tailwind (green)
                value <= -15.0 -> Color(0xFF15803D)
                value <= -5.0 -> Color(0xFF22C55E)
                value < 5.0 -> Color(0xFF94A3B8)
                value < 15.0 -> Color(0xFFFBBF24)
                value < 25.0 -> Color(0xFFF97316)
                else -> Color(0xFFDC2626)
            }
            CROSSWIND -> when {
                value < 8.0 -> Color(0xFF22C55E)
                value < 15.0 -> Color(0xFFFBBF24)
                value < 22.0 -> Color(0xFFF97316)
                else -> Color(0xFFDC2626)
            }
            ICING -> when (value.roundToInt()) {
                0 -> Color(0xFF22C55E)
                1 -> Color(0xFF93C5FD)
                2 -> Color(0xFFF59E0B)
                else -> Color(0xFFEF4444)
            }
            TURBULENCE -> when (value.roundToInt()) {
                0 -> Color(0xFF22C55E)
                1 -> Color(0xFFFBBF24)
                2 -> Color(0xFFF97316)
                else -> Color(0xFFDC2626)
            }
            CONVECTIVE -> when (value.roundToInt()) {
                0 -> Color(0xFF22C55E)
                1 -> Color(0xFFFDE047)
                2 -> Color(0xFFF59E0B)
                3 -> Color(0xFFEF4444)
                else -> Color(0xFF9333EA)
            }
            CLOUD_COVER -> when {
                value < 15.0 -> Color(0xFF22C55E)
                value < 45.0 -> Color(0xFF93C5FD)
                value < 75.0 -> Color(0xFF60A5FA)
                else -> Color(0xFF475569)
            }
            CEILING -> when {
                value >= 10000.0 -> Color(0xFF22C55E)
                value >= 3000.0 -> Color(0xFF4ADE80)
                value >= 1000.0 -> Color(0xFF3B82F6)
                value >= 500.0 -> Color(0xFFEF4444)
                else -> Color(0xFFA855F7)
            }
            VISIBILITY -> when {
                value >= 8.0 -> Color(0xFF22C55E)
                value >= 5.0 -> Color(0xFF3B82F6)
                value >= 1.5 -> Color(0xFFEF4444)
                else -> Color(0xFFA855F7)
            }
            TEMPERATURE -> when {
                value <= -20.0 -> Color(0xFF2563EB)
                value <= -10.0 -> Color(0xFF38BDF8)
                value <= 0.0 -> Color(0xFF93C5FD)
                value <= 15.0 -> Color(0xFFFBBF24)
                else -> Color(0xFFDC2626)
            }
            PRECIPITATION -> when {
                value < 0.2 -> Color(0xFF22C55E)
                value < 1.5 -> Color(0xFF38BDF8)
                value < 4.0 -> Color(0xFF2563EB)
                else -> Color(0xFF7C3AED)
            }
            AGREEMENT -> when (value.roundToInt()) {
                1 -> Color(0xFF22C55E) // Good
                2 -> Color(0xFFF59E0B) // Moderate
                else -> Color(0xFFEF4444) // Poor
            }
        }
    }

    /**
     * Map extracted numeric value to segment width in dp.
     */
    fun strokeWidthDp(value: Double): Float {
        val base = 5.5f
        val maxW = 13.0f
        return when (this) {
            FLIGHT_CATEGORY -> base + (value.toFloat().coerceIn(0f, 3f) / 3f) * (maxW - base)
            WIND_SPEED -> base + (value.toFloat().coerceIn(0f, 50f) / 50f) * (maxW - base)
            HEADWIND -> base + (abs(value).toFloat().coerceIn(0f, 35f) / 35f) * (maxW - base)
            CROSSWIND -> base + (value.toFloat().coerceIn(0f, 25f) / 25f) * (maxW - base)
            ICING, TURBULENCE -> base + (value.toFloat().coerceIn(0f, 3f) / 3f) * (maxW - base)
            CONVECTIVE -> base + (value.toFloat().coerceIn(0f, 4f) / 4f) * (maxW - base)
            CLOUD_COVER -> base + (value.toFloat().coerceIn(0f, 100f) / 100f) * (maxW - base)
            CEILING -> {
                if (value >= 10000.0) base
                else {
                    val t = 1f - (value.toFloat().coerceIn(0f, 5000f) / 5000f)
                    base + t * (maxW - base)
                }
            }
            VISIBILITY -> {
                val t = 1f - (value.toFloat().coerceIn(0f, 10f) / 10f)
                base + t * (maxW - base)
            }
            TEMPERATURE -> base + 2f
            PRECIPITATION -> base + (value.toFloat().coerceIn(0f, 8f) / 8f) * (maxW - base)
            AGREEMENT -> base + ((value.toFloat().coerceIn(1f, 3f) - 1f) / 2f) * (maxW - base)
        }
    }

    /**
     * Human-readable formatted value for waypoint callouts.
     */
    fun formatValue(value: Double): String {
        return when (this) {
            FLIGHT_CATEGORY -> when (value.roundToInt()) {
                0 -> "VFR"
                1 -> "MVFR"
                2 -> "IFR"
                else -> "LIFR"
            }
            WIND_SPEED -> "${value.roundToInt()} kt"
            HEADWIND -> {
                val kt = value.roundToInt()
                when {
                    kt > 0 -> "+${kt} kt HW"
                    kt < 0 -> "${abs(kt)} kt TW"
                    else -> "0 kt"
                }
            }
            CROSSWIND -> "${value.roundToInt()} kt XW"
            ICING, TURBULENCE -> when (value.roundToInt()) {
                0 -> "None"
                1 -> "Light"
                2 -> "Moderate"
                else -> "Severe"
            }
            CONVECTIVE -> when (value.roundToInt()) {
                0 -> "None"
                1 -> "Marginal"
                2 -> "Low"
                3 -> "Moderate"
                else -> "High"
            }
            CLOUD_COVER -> "${value.roundToInt()}%"
            CEILING -> if (value >= 10000.0) "Unlimited" else "${(value / 100.0).roundToInt() * 100} ft"
            VISIBILITY -> if (value >= 9.9) "10+ km" else String.format(Locale.US, "%.1f km", value)
            TEMPERATURE -> String.format(Locale.US, "%+.0f°C", value)
            PRECIPITATION -> String.format(Locale.US, "%.1f mm", value)
            AGREEMENT -> when (value.roundToInt()) {
                1 -> "Good"
                2 -> "Moderate"
                else -> "Poor"
            }
        }
    }

    /**
     * Legend swatches displayed in the Route Map bottom legend bar.
     */
    fun legendSwatches(): List<MapLegendSwatch> {
        return when (this) {
            FLIGHT_CATEGORY -> listOf(
                MapLegendSwatch("VFR", Color(0xFF22C55E)),
                MapLegendSwatch("MVFR", Color(0xFF3B82F6)),
                MapLegendSwatch("IFR", Color(0xFFEF4444)),
                MapLegendSwatch("LIFR", Color(0xFFA855F7)),
            )
            WIND_SPEED -> listOf(
                MapLegendSwatch("<10 kt", Color(0xFF93C5FD)),
                MapLegendSwatch("10–20 kt", Color(0xFF3B82F6)),
                MapLegendSwatch("20–30 kt", Color(0xFFF59E0B)),
                MapLegendSwatch("30–45 kt", Color(0xFFEF4444)),
                MapLegendSwatch(">45 kt", Color(0xFF991B1B)),
            )
            HEADWIND -> listOf(
                MapLegendSwatch(">15 TW", Color(0xFF15803D)),
                MapLegendSwatch("5–15 TW", Color(0xFF22C55E)),
                MapLegendSwatch("±5 kt", Color(0xFF94A3B8)),
                MapLegendSwatch("5–15 HW", Color(0xFFFBBF24)),
                MapLegendSwatch("15–25 HW", Color(0xFFF97316)),
                MapLegendSwatch(">25 HW", Color(0xFFDC2626)),
            )
            CROSSWIND -> listOf(
                MapLegendSwatch("<8 kt", Color(0xFF22C55E)),
                MapLegendSwatch("8–15 kt", Color(0xFFFBBF24)),
                MapLegendSwatch("15–22 kt", Color(0xFFF97316)),
                MapLegendSwatch(">22 kt", Color(0xFFDC2626)),
            )
            ICING -> listOf(
                MapLegendSwatch("None", Color(0xFF22C55E)),
                MapLegendSwatch("Light", Color(0xFF93C5FD)),
                MapLegendSwatch("Mod", Color(0xFFF59E0B)),
                MapLegendSwatch("Sev", Color(0xFFEF4444)),
            )
            TURBULENCE -> listOf(
                MapLegendSwatch("None", Color(0xFF22C55E)),
                MapLegendSwatch("Light", Color(0xFFFBBF24)),
                MapLegendSwatch("Mod", Color(0xFFF97316)),
                MapLegendSwatch("Sev", Color(0xFFDC2626)),
            )
            CONVECTIVE -> listOf(
                MapLegendSwatch("None", Color(0xFF22C55E)),
                MapLegendSwatch("Marginal", Color(0xFFFDE047)),
                MapLegendSwatch("Low", Color(0xFFF59E0B)),
                MapLegendSwatch("Mod", Color(0xFFEF4444)),
                MapLegendSwatch("High", Color(0xFF9333EA)),
            )
            CLOUD_COVER -> listOf(
                MapLegendSwatch("Clear", Color(0xFF22C55E)),
                MapLegendSwatch("SCT", Color(0xFF93C5FD)),
                MapLegendSwatch("BKN", Color(0xFF60A5FA)),
                MapLegendSwatch("OVC", Color(0xFF475569)),
            )
            CEILING -> listOf(
                MapLegendSwatch(">3000 ft", Color(0xFF22C55E)),
                MapLegendSwatch("1000–3000", Color(0xFF3B82F6)),
                MapLegendSwatch("500–1000", Color(0xFFEF4444)),
                MapLegendSwatch("<500 ft", Color(0xFFA855F7)),
            )
            VISIBILITY -> listOf(
                MapLegendSwatch(">8 km", Color(0xFF22C55E)),
                MapLegendSwatch("5–8 km", Color(0xFF3B82F6)),
                MapLegendSwatch("1.5–5 km", Color(0xFFEF4444)),
                MapLegendSwatch("<1.5 km", Color(0xFFA855F7)),
            )
            TEMPERATURE -> listOf(
                MapLegendSwatch("-20°C", Color(0xFF2563EB)),
                MapLegendSwatch("-10°C", Color(0xFF38BDF8)),
                MapLegendSwatch("0°C", Color(0xFFE0F2FE)),
                MapLegendSwatch("+10°C", Color(0xFFFBBF24)),
                MapLegendSwatch("+25°C", Color(0xFFDC2626)),
            )
            PRECIPITATION -> listOf(
                MapLegendSwatch("Dry", Color(0xFF22C55E)),
                MapLegendSwatch("Light", Color(0xFF38BDF8)),
                MapLegendSwatch("Mod", Color(0xFF2563EB)),
                MapLegendSwatch("Heavy", Color(0xFF7C3AED)),
            )
            AGREEMENT -> listOf(
                MapLegendSwatch("Good", Color(0xFF22C55E)),
                MapLegendSwatch("Moderate", Color(0xFFF59E0B)),
                MapLegendSwatch("Poor", Color(0xFFEF4444)),
            )
        }
    }

    companion object {
        fun fromId(rawId: String?): RouteMapMetric {
            if (rawId.isNullOrBlank()) return FLIGHT_CATEGORY
            val normalized = rawId.trim().lowercase(Locale.US)
            return entries.firstOrNull { metric ->
                metric.id == normalized || metric.aliases.any { it == normalized }
            } ?: FLIGHT_CATEGORY
        }
    }
}

private fun derivePointFlightCategoryIndex(point: VizPoint, altitudeFt: Double): Double {
    val ceilingFt = RouteMapMetric.CEILING.extractValue(point, altitudeFt)
    val precip = point.precipitationMm ?: 0.0
    return when {
        ceilingFt < 500.0 || precip > 7.0 -> 3.0 // LIFR
        ceilingFt < 1000.0 || precip > 3.5 -> 2.0 // IFR
        ceilingFt < 3000.0 || (point.cloudCoverLowPct ?: 0.0) > 75.0 -> 1.0 // MVFR
        else -> 0.0 // VFR
    }
}

private fun closestAltitudeValue(dict: Map<Int, Double>, altitudeFt: Double): Double {
    if (dict.isEmpty()) return 0.0
    val target = altitudeFt.roundToInt()
    val closest = dict.entries.minByOrNull { abs(it.key - target) } ?: return 0.0
    return if (abs(closest.key - target) <= 2500) closest.value else 0.0
}

private fun riskIndex(risk: String): Double = when (risk.lowercase(Locale.US)) {
    "light", "marginal" -> 1.0
    "moderate", "low" -> 2.0
    "severe", "high", "extreme" -> 3.0
    else -> 0.0
}

private fun capeToRiskIndex(cape: Double): Double = when {
    cape >= 2000.0 -> 4.0
    cape >= 1000.0 -> 3.0
    cape >= 300.0 -> 2.0
    cape >= 50.0 -> 1.0
    else -> 0.0
}

fun flightCategoryColor(cat: String?): Color = when (cat?.uppercase(Locale.US)) {
    "VFR" -> Color(0xFF22C55E)
    "MVFR" -> Color(0xFF3B82F6)
    "IFR" -> Color(0xFFEF4444)
    "LIFR" -> Color(0xFFA855F7)
    else -> Color(0xFF9CA3AF)
}

fun parseHexColor(hex: String): Color {
    val clean = hex.trim().removePrefix("#")
    return runCatching {
        when (clean.length) {
            6 -> Color((0xFF000000L or clean.toLong(16)).toInt())
            8 -> Color(clean.toLong(16).toInt())
            else -> Color(0xFF9CA3AF)
        }
    }.getOrDefault(Color(0xFF9CA3AF))
}
