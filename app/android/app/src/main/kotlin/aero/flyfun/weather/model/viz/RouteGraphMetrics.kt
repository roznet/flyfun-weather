package aero.flyfun.weather.model.viz

import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.round

enum class RouteGraphRenderType {
    LINE,
    BAR,
}

sealed interface MetricSample {
    data class Value(val value: Double) : MetricSample
    data class AboveScale(val cap: Double) : MetricSample
    data object Unavailable : MetricSample
}

data class RouteGraphMetric(
    val id: String,
    val label: String,
    val unit: String,
    val renderType: RouteGraphRenderType,
    val colorHex: Long,
    val suggestedRange: Pair<Double, Double>? = null,
    val aboveScale: Boolean = false,
    val showZeroLine: Boolean = false,
    val zeroLineLabels: Pair<String, String>? = null,
    /**
     * Metrics with the same axis group share one Y scale when plotted left and
     * right together, so their gap reads directly off the graph (temperature vs
     * dewpoint: the spread, #948). null keeps an independent scale. Mirrors web
     * `axisGroup`.
     */
    val axisGroup: String? = null,
    val getValue: (VizPoint) -> Double?,
    val formatValue: (Double) -> String = { String.format(Locale.US, "%.0f", it) },
)

object RouteGraphMetrics {

    val HEADWIND = RouteGraphMetric(
        id = "headwind",
        label = "Headwind / Tailwind",
        unit = "kt",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFF2563EBL,
        suggestedRange = -30.0 to 30.0,
        showZeroLine = true,
        zeroLineLabels = "Headwind ↑" to "Tailwind ↓",
        getValue = { it.headwindKt },
        formatValue = { v ->
            val rounded = round(v).toInt()
            when {
                rounded > 0 -> "+$rounded kt HW"
                rounded < 0 -> "${-rounded} kt TW"
                else -> "0 kt"
            }
        },
    )

    val CROSSWIND = RouteGraphMetric(
        id = "crosswind",
        label = "Crosswind",
        unit = "kt",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFF06B6D4L,
        suggestedRange = -25.0 to 25.0,
        showZeroLine = true,
        zeroLineLabels = "R" to "L",
        getValue = { it.crosswindKt },
        formatValue = { v -> String.format(Locale.US, "%.0f kt", v) },
    )

    val TEMPERATURE = RouteGraphMetric(
        id = "temperature",
        label = "Temperature (2m)",
        unit = "°C",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFFDC2626L,
        suggestedRange = -10.0 to 30.0,
        showZeroLine = true,
        axisGroup = "surface-temp",
        getValue = { it.temperatureC },
        formatValue = { v -> String.format(Locale.US, "%.1f °C", v) },
    )

    /** 2 m dewpoint (#948): green against temperature's red, as on a Skew-T,
     *  on temperature's axis so the spread is the gap between the lines. */
    val DEWPOINT = RouteGraphMetric(
        id = "dewpoint",
        label = "Dewpoint (2m)",
        unit = "°C",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFF16A34AL,
        suggestedRange = -10.0 to 30.0,
        showZeroLine = true,
        axisGroup = "surface-temp",
        getValue = { it.dewpointC },
        formatValue = { v -> String.format(Locale.US, "%.1f °C", v) },
    )

    val ISA_DEV = RouteGraphMetric(
        id = "isa-dev",
        label = "ISA Deviation",
        unit = "°C",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFFEC4899L,
        suggestedRange = -15.0 to 15.0,
        showZeroLine = true,
        getValue = { it.isaDevC },
        formatValue = { v ->
            val sign = if (v >= 0) "+" else ""
            String.format(Locale.US, "ISA %s%.1f °C", sign, v)
        },
    )

    val PRECIPITATION = RouteGraphMetric(
        id = "precipitation",
        label = "Precipitation",
        unit = "mm",
        renderType = RouteGraphRenderType.BAR,
        colorHex = 0xFF0EA5E9L,
        suggestedRange = 0.0 to 5.0,
        getValue = { it.precipitationMm },
        formatValue = { v -> String.format(Locale.US, "%.1f mm", v) },
    )

    val CLOUD_COVER = RouteGraphMetric(
        id = "cloud-cover",
        label = "Cloud Cover",
        unit = "%",
        renderType = RouteGraphRenderType.BAR,
        colorHex = 0xFF94A3B8L,
        suggestedRange = 0.0 to 100.0,
        getValue = { it.cloudCoverTotalPct },
        formatValue = { v -> String.format(Locale.US, "%.0f%%", v) },
    )

    val CAPE = RouteGraphMetric(
        id = "cape",
        label = "CAPE",
        unit = "J/kg",
        renderType = RouteGraphRenderType.BAR,
        colorHex = 0xFFEAB308L,
        suggestedRange = 0.0 to 1000.0,
        getValue = { it.capeSurfaceJkg },
        formatValue = { v -> String.format(Locale.US, "%.0f J/kg", v) },
    )

    val CIN = RouteGraphMetric(
        id = "cin",
        label = "Convective Inhibition (CIN)",
        unit = "J/kg",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFFA855F7L,
        suggestedRange = -200.0 to 0.0,
        showZeroLine = true,
        getValue = { it.cinSurfaceJkg },
        formatValue = { v -> String.format(Locale.US, "%.0f J/kg", v) },
    )

    val QNH = RouteGraphMetric(
        id = "qnh",
        label = "Sea-Level Pressure (QNH)",
        unit = "hPa",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFF10B981L,
        suggestedRange = 995.0 to 1030.0,
        getValue = { it.qnhHpa },
        formatValue = { v -> String.format(Locale.US, "%.0f hPa", v) },
    )

    val FREEZING_LEVEL = RouteGraphMetric(
        id = "freezing-level",
        label = "Freezing Level (0°C)",
        unit = "ft",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFF06B6D4L,
        suggestedRange = 0.0 to 15000.0,
        aboveScale = true,
        getValue = { it.altitudeLines.freezingLevelFt },
        formatValue = { v -> String.format(Locale.US, "%.0f ft", v) },
    )

    val CEILING_DD = RouteGraphMetric(
        id = "ceiling-dd",
        label = "Ceiling (DD)",
        unit = "ft",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFFF97316L,
        suggestedRange = 0.0 to 15000.0,
        aboveScale = true,
        getValue = { it.soundingCeilingFt },
        formatValue = { v -> String.format(Locale.US, "%.0f ft", v) },
    )

    val CEILING_NWP = RouteGraphMetric(
        id = "ceiling-nwp",
        label = "Ceiling (NWP)",
        unit = "ft",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFFFB923CL,
        suggestedRange = 0.0 to 15000.0,
        aboveScale = true,
        getValue = { it.nwpCeilingFt },
        formatValue = { v -> String.format(Locale.US, "%.0f ft", v) },
    )

    val OBS_RADAR = RouteGraphMetric(
        id = "obs-radar",
        label = "Radar Echo",
        unit = "dBZ",
        renderType = RouteGraphRenderType.BAR,
        colorHex = 0xFF22C55EL,
        suggestedRange = 0.0 to 55.0,
        getValue = { it.observedRadarDbz },
        formatValue = { v -> String.format(Locale.US, "%.0f dBZ", v) },
    )

    val OBS_CLOUD_TOP = RouteGraphMetric(
        id = "obs-cloud-top",
        label = "Satellite Cloud Top",
        unit = "ft",
        renderType = RouteGraphRenderType.LINE,
        colorHex = 0xFF64748BL,
        suggestedRange = 0.0 to 25000.0,
        aboveScale = true,
        getValue = { it.observedCloudTopFt },
        formatValue = { v -> String.format(Locale.US, "%.0f ft", v) },
    )

    val ALL: List<RouteGraphMetric> = listOf(
        HEADWIND,
        CROSSWIND,
        TEMPERATURE,
        DEWPOINT,
        ISA_DEV,
        PRECIPITATION,
        CLOUD_COVER,
        CAPE,
        CIN,
        QNH,
        FREEZING_LEVEL,
        CEILING_DD,
        CEILING_NWP,
        OBS_RADAR,
        OBS_CLOUD_TOP,
    )

    fun byId(id: String?): RouteGraphMetric? {
        if (id.isNullOrBlank() || id.equals("none", ignoreCase = true)) return null
        return ALL.firstOrNull { it.id.equals(id, ignoreCase = true) }
    }

    fun sampleMetric(metric: RouteGraphMetric, point: VizPoint): MetricSample {
        val raw = metric.getValue(point)
        if (raw == null) {
            if (metric.aboveScale && metric.suggestedRange != null) {
                return MetricSample.AboveScale(metric.suggestedRange.second)
            }
            return MetricSample.Unavailable
        }
        if (metric.aboveScale && metric.suggestedRange != null && raw > metric.suggestedRange.second) {
            return MetricSample.AboveScale(metric.suggestedRange.second)
        }
        return MetricSample.Value(raw)
    }

    /**
     * Scales for the left and right metrics. Two metrics in the same [RouteGraphMetric.axisGroup]
     * get one scale fitted to both series; otherwise each is fitted to its own samples.
     * Mirrors web `computeYScales`.
     */
    fun computeYScales(
        left: RouteGraphMetric?,
        right: RouteGraphMetric?,
        points: List<VizPoint>,
    ): Pair<Pair<Double, Double>?, Pair<Double, Double>?> {
        val leftSamples = left?.let { m -> points.map { sampleMetric(m, it) } }.orEmpty()
        val rightSamples = right?.let { m -> points.map { sampleMetric(m, it) } }.orEmpty()
        if (left != null && right != null && left.axisGroup != null && left.axisGroup == right.axisGroup) {
            val shared = computeYScale(left, leftSamples + rightSamples)
            return shared to shared
        }
        return left?.let { computeYScale(it, leftSamples) } to right?.let { computeYScale(it, rightSamples) }
    }

    fun computeYScale(
        metric: RouteGraphMetric,
        samples: List<MetricSample>,
    ): Pair<Double, Double> {
        val numericValues = samples.mapNotNull { sample ->
            when (sample) {
                is MetricSample.Value -> sample.value
                is MetricSample.AboveScale -> sample.cap
                is MetricSample.Unavailable -> null
            }
        }
        val suggestedMin = metric.suggestedRange?.first ?: 0.0
        val suggestedMax = metric.suggestedRange?.second ?: 100.0

        if (numericValues.isEmpty()) {
            return suggestedMin to suggestedMax
        }

        val dataMin = numericValues.minOrNull() ?: suggestedMin
        val dataMax = numericValues.maxOrNull() ?: suggestedMax

        var minVal = min(suggestedMin, floor(dataMin))
        var maxVal = max(suggestedMax, ceil(dataMax))

        if (metric.aboveScale && metric.suggestedRange != null) {
            minVal = metric.suggestedRange.first
            maxVal = metric.suggestedRange.second
        } else if (metric.showZeroLine) {
            val absBound = max(abs(minVal), abs(maxVal))
            minVal = -absBound
            maxVal = absBound
        }

        if (maxVal <= minVal) {
            maxVal = minVal + 1.0
        }
        return minVal to maxVal
    }

    fun formatSample(metric: RouteGraphMetric, sample: MetricSample): String = when (sample) {
        is MetricSample.Value -> metric.formatValue(sample.value)
        is MetricSample.AboveScale -> "Clear (> ${metric.formatValue(sample.cap)})"
        is MetricSample.Unavailable -> "—"
    }
}
