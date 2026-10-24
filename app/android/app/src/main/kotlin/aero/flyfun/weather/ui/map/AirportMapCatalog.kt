// SYNC: iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/MapMetricsCatalog.swift
//       iOS app/flyfun-weather/flyfun-weather/Views/ForecastMap/ForecastAirportCard.swift (compactCell, altLabel)
//       web web/ts/visualization/weather-map-format.ts
package aero.flyfun.weather.ui.map

import androidx.compose.ui.graphics.Color
import aero.flyfun.weather.data.MapBandScale
import aero.flyfun.weather.data.MapLegend
import aero.flyfun.weather.data.MapMetricColor
import aero.flyfun.weather.data.MapMetricsCatalog
import aero.flyfun.weather.model.AltRequired
import aero.flyfun.weather.model.ForecastCellData
import aero.flyfun.weather.model.ForecastMapAirport
import aero.flyfun.weather.model.ForecastModelMode
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Kotlin interpreter of the **served** forecast-map metric catalog
 * (`web/ts/data/map-metrics-catalog.json`, delivered in the `maps` section of `/api/help/catalog`,
 * bundled as an asset). Colour ramps, thresholds, labels and legends are data, not code, so the same
 * weather never shows in two colours on two devices (#419 "clients render, server decides").
 *
 * The forecast and historical maps both colour through here: a historical source is presented as a
 * [ForecastMapAirport] read in a [ForecastModelMode].
 */
object AirportMapCatalog {
    /** Fallback grey for missing data / unknown scale (web `MUTED`). */
    val MUTED = Color(0xFF888888)

    /** The fill colour for one airport under the active metric + model mode. */
    fun color(catalog: MapMetricsCatalog?, metric: String, airport: ForecastMapAirport, mode: ForecastModelMode): Color =
        color(catalog, metric, airport.cell(mode), airport, mode)

    /**
     * Colour a bare consensus/model cell (the card's matrix cells colour one already-selected cell
     * rather than an airport + mode). `alternate_needed` still reads the airport's model flags.
     */
    fun color(
        catalog: MapMetricsCatalog?,
        metric: String,
        cell: ForecastCellData?,
        airport: ForecastMapAirport,
        mode: ForecastModelMode,
    ): Color {
        val spec = catalog?.metrics?.get(metric)?.color ?: return MUTED
        return when (spec.kind) {
            "alternate_needed" -> alternateColor(airport, mode)
            "categorical" -> categoricalColor(catalog, spec, cell)
            "band" -> bandColorFor(catalog, spec, cell)
            else -> MUTED
        }
    }

    private fun categoricalColor(catalog: MapMetricsCatalog, spec: MapMetricColor, cell: ForecastCellData?): Color {
        if (cell == null || spec.scale.isBlank()) return MUTED
        var key = cell.categoryField(spec.scale)
        if (key.isNullOrEmpty()) key = spec.default
        val table = catalog.scales.categorical[spec.scale]
        if (key == null || table == null) return fallbackColor(spec)
        return table[key]?.let(::parseCatalogColor) ?: fallbackColor(spec)
    }

    private fun bandColorFor(catalog: MapMetricsCatalog, spec: MapMetricColor, cell: ForecastCellData?): Color {
        val field = spec.field
        val band = catalog.scales.bands[spec.scale]
        if (cell == null || field == null || band == null) return MUTED
        return bandColor(band, cell.numericField(field), spec.fallbackNumber)
    }

    /** Value → colour for a band scale (web `bandColor`). */
    fun bandColor(band: MapBandScale, raw: Double?, fallbackNumber: Double?): Color {
        val value = raw ?: run {
            band.nullColor?.let { return parseCatalogColor(it) ?: MUTED }
            fallbackNumber ?: return MUTED
        }
        if (band.kind == "gray_ramp") return grayRamp(band, value)
        val v = if (band.convert == "m_to_sm") value / 1609.34 else value
        if (band.kind == "threshold_desc") {
            band.stops.firstOrNull { stop -> stop.gte?.let { v >= it } == true }
                ?.let { return parseCatalogColor(it.color) ?: MUTED }
            return band.default?.let(::parseCatalogColor) ?: MUTED
        }
        // threshold_asc (default)
        band.stops.firstOrNull { stop -> stop.lt?.let { v < it } == true }
            ?.let { return parseCatalogColor(it.color) ?: MUTED }
        return band.default?.let(::parseCatalogColor) ?: MUTED
    }

    private fun grayRamp(band: MapBandScale, pct: Double): Color {
        val base = band.base ?: 220.0
        val span = band.span ?: 160.0
        val boost = band.blueBoost ?: 10.0
        val g = Math.round(base - (pct / 100.0) * span).toDouble()
        fun channel(x: Double) = (x.coerceIn(0.0, 255.0) / 255.0).toFloat()
        return Color(channel(g), channel(g), channel(g + boost))
    }

    private fun alternateColor(airport: ForecastMapAirport, mode: ForecastModelMode): Color {
        val flag: AltRequired = when (mode) {
            is ForecastModelMode.Model -> airport.models[mode.name]?.altRequired
            else -> airport.aggregatedAltRequired(mode)
        } ?: return MUTED
        return when ((if (flag.faa) 1 else 0) + (if (flag.easa) 1 else 0)) {
            0 -> Color(0xFF22C55E)
            1 -> Color(0xFFEAB308)
            else -> Color(0xFFEF4444)
        }
    }

    private fun fallbackColor(spec: MapMetricColor): Color =
        spec.fallbackColor?.let(::parseCatalogColor) ?: MUTED

    // -------------------------------------------------------------------------
    // Labels, legends, agreement
    // -------------------------------------------------------------------------

    fun label(catalog: MapMetricsCatalog?, metric: String): String =
        catalog?.metrics?.get(metric)?.label?.takeIf { it.isNotBlank() } ?: metric

    fun legend(catalog: MapMetricsCatalog?, metric: String): MapLegend? = catalog?.metrics?.get(metric)?.legend

    /** The agreement bucket colour for a label (categorical `agreement` scale). */
    fun agreementColor(catalog: MapMetricsCatalog?, label: String?): Color {
        val hex = label?.let { catalog?.scales?.categorical?.get("agreement")?.get(it) } ?: return MUTED
        return parseCatalogColor(hex) ?: MUTED
    }

    // -------------------------------------------------------------------------
    // Metric picker: "a list of questions, not variable names" (#420)
    // -------------------------------------------------------------------------

    data class MetricItem(val label: String, val metric: String)

    data class MetricSection(val title: String, val items: List<MetricItem>)

    /** Each metric framed as a pilot's question. Entries the catalog lacks are hidden at render. */
    val metricSections: List<MetricSection> = listOf(
        MetricSection("Can I get in?", listOf(MetricItem("Flight category", "flight_category"))),
        MetricSection("Can I land?", listOf(MetricItem("Crosswind (best runway)", "crosswind_kt"))),
        MetricSection(
            "Will I see the ground?",
            listOf(MetricItem("Ceiling", "ceiling_ft"), MetricItem("Visibility", "visibility_m")),
        ),
        MetricSection(
            "Will it be rough?",
            listOf(MetricItem("Convective risk", "convective_risk"), MetricItem("CAPE", "cape_jkg")),
        ),
        MetricSection("Will it take forever?", listOf(MetricItem("Headwind", "headwind_kt"))),
        MetricSection("Do I need an alternate?", listOf(MetricItem("FAA / EASA", "alternate_needed"))),
        MetricSection(
            "More",
            listOf(MetricItem("Wind", "wind_speed_kt"), MetricItem("Cloud cover", "cloud_cover_pct")),
        ),
    )

    // -------------------------------------------------------------------------
    // Cell text (port of the card's `compactCell` / `altLabel`)
    // -------------------------------------------------------------------------

    fun altLabel(flag: AltRequired?): String = when {
        flag == null -> "—"
        flag.faa && flag.easa -> "FAA + EASA"
        flag.easa -> "EASA"
        flag.faa -> "FAA"
        else -> "None"
    }

    /** Narrow matrix-cell value (units live in the row label). */
    fun compactCell(data: ForecastCellData?, metric: String): String {
        if (data == null) return "—"
        return when (metric) {
            "flight_category" -> data.categoryField("flight_category") ?: "—"
            "convective_risk" -> data.categoryField("convective_risk") ?: "none"
            "ceiling_ft" -> data.numericField("ceiling_ft")?.let { if (it >= 10_000) "CAVOK" else "${it.roundToInt()}" } ?: "—"
            "visibility_m" -> data.numericField("visibility_m")?.let { m ->
                // Web `formatVisibility` (metric/EU): >10 km / N km / N m
                when {
                    m >= 10_000 -> ">10 km"
                    m >= 5_000 -> "${(m / 1000).roundToInt()} km"
                    else -> "${m.roundToInt()} m"
                }
            } ?: "—"
            "wind_speed_kt" -> MapWindFormat.wind(
                speed = data.numericField("wind_speed_kt"),
                dir = data.numericField("wind_dir_deg"),
                gust = data.numericField("wind_gust_kt"),
            ) ?: "—"
            "crosswind_kt" -> MapWindFormat.component(
                data.numericField("crosswind_kt"),
                gust = data.numericField("gust_crosswind_kt"),
            ) ?: "—"
            "headwind_kt" -> MapWindFormat.component(
                data.numericField("headwind_kt"),
                gust = data.numericField("gust_headwind_kt"),
            ) ?: "—"
            "cape_jkg" -> data.numericField("cape_jkg")?.let { "${it.roundToInt()}" } ?: "—"
            "cloud_cover_pct" -> data.numericField("cloud_cover_pct")?.let { "${it.roundToInt()}%" } ?: "—"
            else -> "—"
        }
    }
}

/**
 * The shared `dir@speedGgust` wind notation (iOS `WindFormat`, web `formatWind`): a gust shows only
 * when it beats the sustained value by at least [GUST_DISPLAY_MIN_EXCESS_KT] (rounded numbers).
 */
object MapWindFormat {
    const val GUST_DISPLAY_MIN_EXCESS_KT = 4

    private fun gustSuffix(base: Double, gust: Double?): String {
        val g = gust?.roundToInt() ?: return ""
        return if (g - base.roundToInt() >= GUST_DISPLAY_MIN_EXCESS_KT) "G$g" else ""
    }

    /** e.g. "273@15G22"; missing direction drops the `dir@` prefix, missing speed returns null. */
    fun wind(speed: Double?, dir: Double?, gust: Double?): String? {
        speed ?: return null
        val dirStr = dir?.let { String.format(Locale.US, "%03d@", it.roundToInt()) } ?: ""
        return "$dirStr${speed.roundToInt()}${gustSuffix(speed, gust)}"
    }

    /** A single wind component (crosswind/headwind) as `valueGgust`, e.g. "12G18". */
    fun component(value: Double?, gust: Double?): String? {
        value ?: return null
        return "${value.roundToInt()}${gustSuffix(value, gust)}"
    }
}

/** Parse a catalog colour string: `#rgb`, `#rrggbb`, or `rgb(r,g,b)`; null when malformed. */
fun parseCatalogColor(string: String): Color? {
    val s = string.trim()
    if (s.startsWith("#")) {
        val hex = s.drop(1)
        val full = when (hex.length) {
            3 -> hex.map { "$it$it" }.joinToString("")
            6 -> hex
            else -> return null
        }
        val v = full.toLongOrNull(16) ?: return null
        return Color(0xFF000000 or v)
    }
    if (s.lowercase().startsWith("rgb(") && s.endsWith(")")) {
        val parts = s.substring(4, s.length - 1).split(",").mapNotNull { it.trim().toDoubleOrNull() }
        if (parts.size != 3) return null
        return Color((parts[0] / 255).toFloat(), (parts[1] / 255).toFloat(), (parts[2] / 255).toFloat())
    }
    return null
}

/** Black or white, whichever reads on this background (iOS `readableText`: perceived luma > 0.6). */
fun Color.readableText(): Color =
    if (0.299f * red + 0.587f * green + 0.114f * blue > 0.6f) Color(0xFF121212) else Color.White
