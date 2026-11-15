package aero.flyfun.weather.ui.map

import androidx.compose.ui.graphics.Color
import aero.flyfun.weather.data.RouteMapCatalog
import aero.flyfun.weather.data.RouteMapFormat
import aero.flyfun.weather.data.RouteMapMetricSpec
import aero.flyfun.weather.data.RouteMapWidth
import aero.flyfun.weather.model.viz.VizCatLayer
import aero.flyfun.weather.model.viz.VizCloudLayer
import aero.flyfun.weather.model.viz.VizIcingZone
import aero.flyfun.weather.model.viz.VizPoint
import aero.flyfun.weather.model.viz.VizSfipZone
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
// Briefing route-map metrics (#998)
// ============================================================================
//
// What colours and sizes the briefing map's route line, read from the **served** route catalog
// (`route_maps` in `/api/help/catalog`, source `web/ts/data/route-map-metrics-catalog.json`,
// bundled asset `route-map-metrics-catalog.json`). The catalog is the one source for the metric
// list, labels, groups, colour scales, widths, legends and the default. This file only knows how to
// read a value off a viz point ([RouteMapMetrics.extractors], same names on web and iOS); a catalog
// metric whose extractor this build does not know is skipped rather than drawn empty, so the server
// can add one ahead of an app release.
//
// SYNC: web `web/ts/visualization/route-map/metrics.ts`, iOS `Views/Map/MapMetrics.swift`.

/** One route metric built from a catalog entry: one metric drives both colour and width. */
class RouteMapMetric(
    val id: String,
    val label: String,
    val unit: String,
    val group: String,
    val usesAltitude: Boolean,
    /** Hazards take the worst endpoint so one bad endpoint is never averaged away. */
    val aggregatesWorst: Boolean,
    private val extract: RouteMapExtractor,
    private val colorOf: (Double) -> Color,
    private val widthSpec: RouteMapWidth,
    private val formatSpec: RouteMapFormat,
    val legend: List<MapLegendSwatch>,
) {
    /** The value at a point, or null when the point has none (or an altitude metric has no altitude). */
    fun value(point: VizPoint, altitudeFt: Double?): Double? = extract(point, altitudeFt)

    /** A segment's value from its two endpoints, per the catalog `aggregation`. */
    fun segmentValue(a: VizPoint, b: VizPoint, altitudeFt: Double?): Double? {
        val va = value(a, altitudeFt)
        val vb = value(b, altitudeFt)
        if (va == null) return vb
        if (vb == null) return va
        return if (aggregatesWorst) max(va, vb) else (va + vb) / 2.0
    }

    fun color(value: Double): Color = colorOf(value)

    /** Route-line width (dp) for a value, on the catalog's thin…thick span. */
    fun widthDp(value: Double): Float =
        (RouteMapMetrics.MIN_WIDTH_DP +
            RouteMapMetrics.widthFraction(widthSpec, value) * (RouteMapMetrics.MAX_WIDTH_DP - RouteMapMetrics.MIN_WIDTH_DP)).toFloat()

    fun format(value: Double): String = RouteMapMetrics.format(formatSpec, value)
}

/** A SHOWING menu section: At altitude / Along route. */
data class RouteMapMetricGroup(val key: String, val label: String, val metrics: List<RouteMapMetric>)

typealias RouteMapExtractor = (VizPoint, Double?) -> Double?

/** The metrics of one catalog, plus its groups, default and no-data colour. */
class RouteMapMetrics(catalog: RouteMapCatalog) {
    val all: List<RouteMapMetric> = catalog.metrics.mapNotNull { build(it, catalog) }
    val groups: List<RouteMapMetricGroup> = catalog.groups.mapNotNull { g ->
        val ms = all.filter { it.group == g.key }
        if (ms.isEmpty()) null else RouteMapMetricGroup(g.key, g.label, ms)
    }
    val defaultId: String = catalog.defaultMetric
    val noDataColor: Color = parseCatalogColor(catalog.noDataColor) ?: AirportMapCatalog.MUTED

    fun metric(id: String): RouteMapMetric? = all.firstOrNull { it.id == id }

    /** A stored id resolved to a metric: an unknown (renamed, dropped) id falls back to the default. */
    fun resolve(id: String?): RouteMapMetric? = id?.let(::metric) ?: metric(defaultId) ?: all.firstOrNull()

    companion object {
        /** Route-line width range (dp), the same 3…18 as the web's px and iOS's pt. */
        const val MIN_WIDTH_DP = 3.0
        const val MAX_WIDTH_DP = 18.0

        /** Fraction 0 (thinnest) … 1 (thickest) on a metric's width spec (either order). */
        fun widthFraction(w: RouteMapWidth, v: Double): Double {
            val span = w.thickAt - w.thinAt
            if (span == 0.0) return 0.0
            return ((v - w.thinAt) / span).coerceIn(0.0, 1.0)
        }

        fun format(f: RouteMapFormat, v: Double): String {
            val labels = f.labels
            if (f.kind == "rank" && !labels.isNullOrEmpty()) {
                return labels[v.roundToInt().coerceIn(0, labels.lastIndex)]
            }
            val n = v * (f.multiply ?: 1.0)
            return (f.prefix ?: "") + String.format(Locale.US, "%.${f.decimals ?: 0}f", n) + (f.suffix ?: "")
        }

        private fun build(spec: RouteMapMetricSpec, cat: RouteMapCatalog): RouteMapMetric? {
            val extract = extractors[spec.value] ?: return null
            val band = cat.scales.bands[spec.scale] ?: return null
            val colorOf: (Double) -> Color = { AirportMapCatalog.bandColor(band, it, null) }
            return RouteMapMetric(
                id = spec.key,
                label = spec.label,
                unit = spec.unit,
                group = spec.group,
                usesAltitude = spec.usesAltitude,
                aggregatesWorst = spec.aggregation == "max",
                extract = extract,
                colorOf = colorOf,
                widthSpec = spec.width,
                formatSpec = spec.format,
                legend = spec.legend.map { MapLegendSwatch(it.label, colorOf(it.value)) },
            )
        }

        // MARK: Extractors (the only per-metric code; same names on web and iOS)

        private val riskRank = mapOf(
            "none" to 0.0, "marginal" to 1.0, "low" to 1.0, "light" to 1.0,
            "moderate" to 2.0, "high" to 3.0, "severe" to 3.0, "extreme" to 4.0,
        )

        private fun rank(risk: String?): Double = riskRank[risk?.lowercase(Locale.US)] ?: 0.0

        private fun agreementRank(s: String): Double = when (s.lowercase(Locale.US)) {
            "poor" -> 2.0
            "moderate" -> 1.0
            else -> 0.0
        }

        private fun worstIcingAt(zones: List<VizIcingZone>, altFt: Double): Double =
            zones.filter { altFt >= it.baseFt && altFt <= it.topFt }.maxOfOrNull { rank(it.risk) } ?: 0.0

        private fun worstCatAt(layers: List<VizCatLayer>, altFt: Double): Double =
            layers.filter { altFt >= it.baseFt && altFt <= it.topFt }.maxOfOrNull { rank(it.risk) } ?: 0.0

        private fun sfipAt(zones: List<VizSfipZone>, altFt: Double): Double? =
            zones.filter { altFt >= it.baseFt && altFt <= it.topFt }.mapNotNull { it.meanSfip }.maxOrNull()

        private val coverageWeight = mapOf("sct" to 0.3, "bkn" to 0.7, "ovc" to 1.0)

        private fun cloudAt(layers: List<VizCloudLayer>, altFt: Double): Double {
            var maxCov = 0.0
            for (l in layers) {
                if (altFt >= l.baseFt && altFt <= l.topFt) {
                    maxCov = max(maxCov, coverageWeight[l.coverage.lowercase(Locale.US)] ?: 0.5)
                }
            }
            return maxCov * 100.0
        }

        /**
         * Temperature (°C) at an altitude from the isotherm levels (0 / −10 / −20 °C), interpolated
         * between them, extrapolated at ~2 °C per 1000 ft.
         */
        internal fun tempAtAltitude(p: VizPoint, altFt: Double): Double? {
            val fz = p.altitudeLines.freezingLevelFt ?: return null
            val refs = mutableListOf(fz to 0.0)
            p.altitudeLines.minus10cLevelFt?.let { refs.add(it to -10.0) }
            p.altitudeLines.minus20cLevelFt?.let { refs.add(it to -20.0) }
            refs.sortBy { it.first }
            val first = refs.first()
            if (altFt <= first.first) return first.second + (first.first - altFt) * 2.0 / 1000.0
            val last = refs.last()
            if (altFt >= last.first) return last.second - (altFt - last.first) * 2.0 / 1000.0
            for (i in 0 until refs.lastIndex) {
                val (a0, t0) = refs[i]
                val (a1, t1) = refs[i + 1]
                if (altFt >= a0 && altFt <= a1) {
                    val frac = if (a1 == a0) 0.0 else (altFt - a0) / (a1 - a0)
                    return t0 + frac * (t1 - t0)
                }
            }
            return null
        }

        /** Gives a lambda its extractor type, so `"name" to ex { p, alt -> … }` infers its parameters. */
        private fun ex(f: RouteMapExtractor): RouteMapExtractor = f

        /** Catalog `value` → how to read it off a viz point. Altitude extractors return null without one. */
        val extractors: Map<String, RouteMapExtractor> = mapOf(
            "cloud_cover_total_pct" to ex { p, _ -> p.cloudCoverTotalPct },
            "cloud_cover_low_pct" to ex { p, _ -> p.cloudCoverLowPct },
            "convective_risk_rank" to ex { p, _ -> rank(p.thermoConvective.riskLevel) },
            "headwind_kt" to ex { p, _ -> p.headwindKt?.let { max(0.0, it) } },
            "tailwind_kt" to ex { p, _ -> p.headwindKt?.let { max(0.0, -it) } },
            "wind_speed_kt" to ex { p, _ -> p.windSpeedKt },
            "crosswind_kt" to ex { p, _ -> p.crosswindKt?.let { abs(it) } },
            "cape_surface_jkg" to ex { p, _ -> p.capeSurfaceJkg },
            "nwp_ceiling_ft" to ex { p, _ -> p.nwpCeilingFt },
            "visibility_m" to ex { p, _ -> p.visibilityMeters },
            "model_agreement_rank" to ex { p, _ -> agreementRank(p.worstModelAgreement) },
            "temperature_at_level_c" to ex { p, alt -> alt?.let { tempAtAltitude(p, it) } },
            // The diagnostic icing zones, as web and iOS.
            "icing_risk_at_level_rank" to ex { p, alt -> alt?.let { worstIcingAt(p.icingZones, it) } },
            "sfip_at_level" to ex { p, alt -> alt?.let { sfipAt(p.sfipZones, it) } },
            "cat_risk_at_level_rank" to ex { p, alt -> alt?.let { worstCatAt(p.catLayers, it) } },
            "cloud_at_level_pct" to ex { p, alt -> alt?.let { cloudAt(p.cloudLayers, it) } },
        )

        // MARK: Altitude stepper (web `stepAltitude` / `formatFlightLevel`, iOS `stepAltitude`)

        const val ALTITUDE_STEP_FT = 500.0

        /** One stepper press: snapped to the 500 ft grid, clamped to 0 … [ceilingFt]. */
        fun stepAltitude(ft: Double, direction: Int, ceilingFt: Double): Double {
            val snapped = Math.round(ft / ALTITUDE_STEP_FT) * ALTITUDE_STEP_FT
            return max(0.0, min(ceilingFt, snapped + direction * ALTITUDE_STEP_FT))
        }

        /** "FL080" for 8000 ft, "SFC" below 100 ft. */
        fun formatFlightLevel(ft: Double): String =
            if (ft < 100.0) "SFC" else "FL" + (ft / 100.0).roundToInt().toString().padStart(3, '0')
    }
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
