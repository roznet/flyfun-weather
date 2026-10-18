package aero.flyfun.weather.ui.skewt

import aero.flyfun.rzskewt.Thermodynamics
import aero.flyfun.weather.model.SoundingProfileLevel
import aero.flyfun.weather.model.SoundingProfileResponse

/**
 * Display groups for Skew-T side-panel variables, mirroring `VARIABLE_GROUPS` in
 * `web/ts/visualization/skewt/variable-panel.ts` and `SkewTVariableCatalog.swift`.
 */
enum class SkewTVariableGroup(val title: String) {
    WIND("Wind"),
    MOISTURE_CLOUD("Moisture & Cloud"),
    ICING("Icing"),
    STABILITY_VERTICAL("Stability & Vertical"),
}

/**
 * Definition of a single Skew-T side-panel variable.
 */
data class SkewTVariableDef(
    val id: String,
    val label: String,
    val shortLabel: String,
    val unit: String,
    val group: SkewTVariableGroup,
    val colorHex: Long,
    val metricCatalogId: String? = null,
    val showZeroLine: Boolean = false,
    val fixedRange: Pair<Double, Double>? = null,
    val secondaryColorHex: Long? = null,
    val secondaryShortLabel: String? = null,
)

/**
 * Overlay band categories that can be shaded horizontally on the Skew-T plot.
 */
enum class SkewTOverlayBand(
    val id: String,
    val label: String,
    val defaultEnabled: Boolean,
    val metricCatalogId: String? = null,
) {
    CLOUDS_NWP("clouds-nwp", "NWP Clouds", true, "skewt_clouds_nwp"),
    CLOUDS_DD("clouds-dd", "DD Clouds", false, "skewt_clouds_dd"),
    ICING_OGIMET_NWP("icing-nwp", "Ogimet-NWP", true, "icing_ogimet_nwp_risk"),
    ICING_OGIMET_DD("icing-dd", "Ogimet-DD", false, "icing_risk"),
    ICING_SFIP("icing-sfip", "SFIP", false, "sfip_risk"),
    INVERSIONS("inversions", "Inversions", true, "skewt_inversions"),
    CONVECTIVE("convective", "Convective", false, "skewt_convective");

    fun isEnabledIn(enabledOverlays: Set<String>): Boolean {
        return id in enabledOverlays || name in enabledOverlays || name.lowercase() in enabledOverlays
    }

    companion object {
        fun defaultEnabledIds(): Set<String> =
            entries.filter { it.defaultEnabled }.map { it.id }.toSet()
    }
}

/**
 * Registry of all 14 Skew-T side-panel variables and series extractors.
 */
object SkewTVariableCatalog {

    val ALL_VARIABLES: List<SkewTVariableDef> = listOf(
        // Wind
        SkewTVariableDef(
            id = "hw_xw",
            label = "Headwind / Crosswind",
            shortLabel = "HW/XW",
            unit = "kt",
            group = SkewTVariableGroup.WIND,
            colorHex = 0xFFD04040,
            metricCatalogId = "skewt_headwind_crosswind",
            showZeroLine = true,
            secondaryColorHex = 0xFF2080D0,
            secondaryShortLabel = "XW",
        ),
        SkewTVariableDef(
            id = "wind_speed",
            label = "Wind Speed",
            shortLabel = "Wind",
            unit = "kt",
            group = SkewTVariableGroup.WIND,
            colorHex = 0xFF6060C0,
            metricCatalogId = "wind_speed_kt",
        ),
        // Moisture & Cloud
        SkewTVariableDef(
            id = "dd",
            label = "Dewpoint Depression",
            shortLabel = "DD",
            unit = "°C",
            group = SkewTVariableGroup.MOISTURE_CLOUD,
            colorHex = 0xFFE07020,
            metricCatalogId = "dewpoint_depression_c",
            fixedRange = 0.0 to 15.0,
        ),
        SkewTVariableDef(
            id = "cc",
            label = "Cloud Cover",
            shortLabel = "CC",
            unit = "%",
            group = SkewTVariableGroup.MOISTURE_CLOUD,
            colorHex = 0xFF20C0E0,
            metricCatalogId = "skewt_cloud_area_fraction",
            fixedRange = 0.0 to 100.0,
        ),
        SkewTVariableDef(
            id = "rh",
            label = "Relative Humidity",
            shortLabel = "RH",
            unit = "%",
            group = SkewTVariableGroup.MOISTURE_CLOUD,
            colorHex = 0xFF2090D0,
            metricCatalogId = "skewt_relative_humidity",
            fixedRange = 0.0 to 100.0,
        ),
        SkewTVariableDef(
            id = "clw",
            label = "Cloud Liquid Water",
            shortLabel = "CLW",
            unit = "g/kg",
            group = SkewTVariableGroup.MOISTURE_CLOUD,
            colorHex = 0xFF20A0A0,
            metricCatalogId = "skewt_cloud_liquid_water",
        ),
        SkewTVariableDef(
            id = "ice",
            label = "Ice Mixing Ratio",
            shortLabel = "ICE",
            unit = "g/kg",
            group = SkewTVariableGroup.MOISTURE_CLOUD,
            colorHex = 0xFF8080D0,
            metricCatalogId = "skewt_ice_mixing_ratio",
        ),
        // Icing
        SkewTVariableDef(
            id = "icing_dd",
            label = "Icing (Ogimet-DD)",
            shortLabel = "Ice-DD",
            unit = "",
            group = SkewTVariableGroup.ICING,
            colorHex = 0xFF6495ED,
            metricCatalogId = "icing_risk",
            fixedRange = 0.0 to 100.0,
        ),
        SkewTVariableDef(
            id = "icing_nwp",
            label = "Icing (Ogimet-NWP)",
            shortLabel = "Ice-NWP",
            unit = "",
            group = SkewTVariableGroup.ICING,
            colorHex = 0xFF4080D0,
            metricCatalogId = "icing_ogimet_nwp_risk",
            fixedRange = 0.0 to 100.0,
        ),
        SkewTVariableDef(
            id = "sfip",
            label = "SFIP Index",
            shortLabel = "SFIP",
            unit = "%",
            group = SkewTVariableGroup.ICING,
            colorHex = 0xFFD08020,
            metricCatalogId = "sfip_risk",
            fixedRange = 0.0 to 100.0,
        ),
        // Stability & Vertical
        SkewTVariableDef(
            id = "lapse_rate",
            label = "Lapse Rate",
            shortLabel = "Γ",
            unit = "°C/km",
            group = SkewTVariableGroup.STABILITY_VERTICAL,
            colorHex = 0xFFC04040,
            metricCatalogId = "lapse_rate_c_km",
            showZeroLine = true,
        ),
        SkewTVariableDef(
            id = "theta_e",
            label = "Equiv. Pot. Temp.",
            shortLabel = "θe",
            unit = "K",
            group = SkewTVariableGroup.STABILITY_VERTICAL,
            colorHex = 0xFFA04080,
            metricCatalogId = "equivalent_potential_temperature_k",
        ),
        SkewTVariableDef(
            id = "ri",
            label = "Richardson Number",
            shortLabel = "Ri",
            unit = "",
            group = SkewTVariableGroup.STABILITY_VERTICAL,
            colorHex = 0xFFD0A020,
            metricCatalogId = "richardson_number",
        ),
        SkewTVariableDef(
            id = "w_fpm",
            label = "Vertical Velocity",
            shortLabel = "w",
            unit = "fpm",
            group = SkewTVariableGroup.STABILITY_VERTICAL,
            colorHex = 0xFF40A040,
            metricCatalogId = "skewt_vertical_velocity",
            showZeroLine = true,
        ),
    )

    private val aliasMap: Map<String, String> = mapOf(
        "headwind" to "hw_xw",
        "dewpoint_depression" to "dd",
        "cloud_area_fraction" to "cc",
        "cloud" to "cc",
        "relative_humidity" to "rh",
        "cloud_liquid_water" to "clw",
        "ice_mixing_ratio" to "ice",
        "icing_index" to "icing_dd",
        "icing-dd" to "icing_dd",
        "icing_index_nwp" to "icing_nwp",
        "icing-nwp" to "icing_nwp",
        "lapse" to "lapse_rate",
        "thetae" to "theta_e",
        "richardson" to "ri",
        "vertical_velocity" to "w_fpm",
        "w" to "w_fpm",
    )

    fun canonicalId(variableId: String): String = aliasMap[variableId] ?: variableId

    fun findById(variableId: String): SkewTVariableDef? {
        val canonical = canonicalId(variableId)
        return ALL_VARIABLES.firstOrNull { it.id == canonical }
    }

    fun groupedVariables(): Map<SkewTVariableGroup, List<SkewTVariableDef>> =
        SkewTVariableGroup.entries.associateWith { group ->
            ALL_VARIABLES.filter { it.group == group }
        }

    /**
     * Extracts `(pressureHpa, value)` pairs for [variableId] from [profile], sorted from high pressure (surface)
     * to low pressure (top).
     */
    fun extractSeries(variableId: String, profile: SoundingProfileResponse): List<Pair<Double, Double>> {
        val canonical = canonicalId(variableId)
        val sortedLevels = profile.levels.sortedByDescending { it.pressureHpa }
        return sortedLevels.mapNotNull { level ->
            val value = extractLevelValue(canonical, level, sortedLevels, profile.trackDeg)
            if (value != null && value.isFinite()) {
                Pair(level.pressureHpa, value)
            } else {
                null
            }
        }
    }

    /**
     * Extracts the secondary series `(pressureHpa, crosswindKt)` when [variableId] is `hw_xw`,
     * or an empty list for single-line variables.
     */
    fun extractSecondarySeries(variableId: String, profile: SoundingProfileResponse): List<Pair<Double, Double>> {
        val canonical = canonicalId(variableId)
        if (canonical != "hw_xw") return emptyList()
        return extractCrosswindSeries(profile)
    }

    /**
     * Extracts `(pressureHpa, crosswindKt)` pairs from [profile].
     */
    fun extractCrosswindSeries(profile: SoundingProfileResponse): List<Pair<Double, Double>> {
        val track = profile.trackDeg ?: 0.0
        return profile.levels
            .sortedByDescending { it.pressureHpa }
            .mapNotNull { level ->
                val ws = level.windSpeedKt ?: return@mapNotNull null
                val wd = level.windDirectionDeg ?: return@mapNotNull null
                val (_, xw) = Thermodynamics.headwindCrosswindKt(ws, wd, track)
                if (xw.isFinite()) Pair(level.pressureHpa, xw) else null
            }
    }

    /**
     * Extracts a single level's value for [canonicalId]. Falls back to client-side thermodynamic formulas
     * when optional derived fields are omitted on the wire.
     */
    fun extractLevelValue(
        canonicalId: String,
        level: SoundingProfileLevel,
        allLevels: List<SoundingProfileLevel> = emptyList(),
        trackDeg: Double? = null,
    ): Double? {
        return when (canonicalId) {
            "hw_xw" -> {
                val ws = level.windSpeedKt ?: return null
                val wd = level.windDirectionDeg ?: return null
                val track = trackDeg ?: 0.0
                Thermodynamics.headwindCrosswindKt(ws, wd, track).first
            }
            "wind_speed" -> level.windSpeedKt
            "dd" -> level.dewpointDepressionC ?: level.dewpointC?.let { (level.temperatureC - it).coerceAtLeast(0.0) }
            "cc" -> level.cloudAreaFractionPct
            "rh" -> level.relativeHumidityPct ?: level.dewpointC?.let {
                Thermodynamics.relativeHumidityPct(level.temperatureC, it)
            }
            "clw" -> level.cloudLiquidWaterGKg ?: level.cloudLiquidWaterGM3
            "ice" -> level.iceMixingRatioGKg
            "icing_dd" -> level.icingIndex
            "icing_nwp" -> level.icingIndexNwp
            "sfip" -> level.sfip100
            "lapse_rate" -> level.lapseRateCPerKm ?: computeLapseRateFallback(level, allLevels)
            "theta_e" -> level.thetaEK ?: level.dewpointC?.let {
                Thermodynamics.equivalentPotentialTemperatureK(level.temperatureC, it, level.pressureHpa)
            }
            "ri" -> level.richardsonNumber?.takeIf { it < 100.0 }
            "w_fpm" -> level.wFpm ?: (level.omegaPaS ?: level.verticalVelocityPaS)?.let { omega ->
                // Approximate conversion from Pa/s to ft/min: w ≈ -omega / (rho * g)
                -omega * 160.0
            }
            else -> null
        }
    }

    private fun computeLapseRateFallback(
        level: SoundingProfileLevel,
        allLevels: List<SoundingProfileLevel>,
    ): Double? {
        val idx = allLevels.indexOfFirst { it.pressureHpa == level.pressureHpa }
        if (idx < 0 || allLevels.size < 2) return null
        val lower = if (idx > 0) allLevels[idx - 1] else level
        val upper = if (idx < allLevels.size - 1) allLevels[idx + 1] else level
        val altLowerFt = lower.altitudeFt ?: Thermodynamics.pressureToAltitudeFt(lower.pressureHpa)
        val altUpperFt = upper.altitudeFt ?: Thermodynamics.pressureToAltitudeFt(upper.pressureHpa)
        val dzKm = (altUpperFt - altLowerFt) / 3280.84
        if (dzKm <= 0.05) return null
        return (lower.temperatureC - upper.temperatureC) / dzKm
    }
}
