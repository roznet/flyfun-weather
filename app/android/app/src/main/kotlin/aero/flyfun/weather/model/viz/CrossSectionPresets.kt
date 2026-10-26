package aero.flyfun.weather.model.viz

data class EmulationPreset(
    val id: String,
    val label: String,
    val themeId: String,
    val cloudStyle: CloudStyle,
    val enabledLayers: Map<String, Boolean>,
)

data class AdvisoryPreset(
    val id: String,
    val label: String,
    val caption: String,
    val interpretation: String,
    val groups: List<String>,
    val lines: List<String>,
    val routeGraphLeft: String? = null,
    val routeGraphRight: String? = null,
    val skewtOverlays: List<String> = emptyList(),
    val skewtPrimaryVar: String = "hw_xw",
    val description: String = caption,
)

data class GradedMethods(
    val cloudExtent: String = "nwp",
    val cloudScale: String = "coverage",
    val icing: String = "ogimet_nwp",
    val convective: String = "nwp",
)

/**
 * Cross-section layer registry, family mappings, emulation presets, and focus presets.
 *
 * SYNC: Kept in lockstep with `web/ts/visualization/cross-section/advisory-presets.ts`
 * (focus presets) and `web/ts/visualization/cross-section/layer-registry.ts`
 * (PREFERRED_METHOD_LAYER / METHOD_GROUPS). `Basic / Learn` shows one layer per
 * method group on its preferred method; for convection that is NWP
 * (`nwp-convective-bg`, Web's default-on layer), and `NwpFallback` swaps in Thermo
 * where the model has no native convective field. iOS `CrossSectionPresets.swift`
 * (`methodGroups`) and `LayerFamily.swift` follow the same model.
 */
object CrossSectionPresets {

    val ALL_LAYERS: List<CrossSectionLayerDef> = listOf(
        // 1. Background / day-night
        CrossSectionLayerDef("day-night", "Day / Night Shading", "reference", false, "Solar elevation day, twilight & night background tint."),
        // 2. Terrain
        CrossSectionLayerDef("terrain", "Terrain Profile", "terrain", true, "Route terrain elevation profile in feet MSL."),
        // 3. Clouds (mutually exclusive)
        CrossSectionLayerDef("clouds", "Cloud Layers (DD)", "clouds", false, "Sounding dewpoint-depression derived cloud bands."),
        CrossSectionLayerDef("clouds-nwp", "Cloud Layers (NWP)", "clouds", true, "Model native cloud-fraction bands."),
        CrossSectionLayerDef("clouds-icing", "Icing Regime Clouds (DD)", "clouds", false, "DD clouds colored by temperature regime."),
        CrossSectionLayerDef("clouds-icing-nwp", "Icing Regime Clouds (NWP)", "clouds", false, "NWP clouds colored by temperature regime."),
        CrossSectionLayerDef("clouds-diag", "Cloud Diagnostics (DD)", "clouds", false, "Low / Mid / High diagnostic cloud bands (DD)."),
        CrossSectionLayerDef("clouds-diag-nwp", "Cloud Diagnostics (NWP)", "clouds", false, "Low / Mid / High diagnostic cloud bands (NWP)."),
        CrossSectionLayerDef("clouds-band", "NWP Cloud Bands (DD)", "clouds", false, "Continuous DD cloud band profile."),
        CrossSectionLayerDef("clouds-band-nwp", "NWP Cloud Bands (NWP)", "clouds", false, "Continuous NWP cloud band profile."),
        // 4. Icing (mutually exclusive except SLD)
        CrossSectionLayerDef("icing", "Icing (Ogimet-DD)", "icing", false, "Ogimet icing index using sounding dewpoint depression."),
        CrossSectionLayerDef("icing-ogimet-nwp", "Icing (Ogimet-NWP)", "icing", true, "Ogimet icing index gated by NWP cloud fraction."),
        CrossSectionLayerDef("icing-sfip", "Icing (SFIP)", "icing", false, "Simplified Forecast Icing Potential (SFIP)."),
        CrossSectionLayerDef("icing-ieng", "Icing (IENG)", "icing", false, "IENG icing index from sounding profile."),
        CrossSectionLayerDef("sld", "SLD Zones", "icing", false, "Supercooled Large Droplet warm-nose hazard zones."),
        // 5. Turbulence
        CrossSectionLayerDef("cat", "Clear Air Turbulence (CAT)", "turbulence", true, "Richardson-number vertical wind shear turbulence."),
        CrossSectionLayerDef("inversions", "Temperature Inversions", "stability", false, "Stable temperature inversion layers."),
        CrossSectionLayerDef("vertical-motion", "Vertical Motion", "turbulence", false, "Synoptic vertical velocity / E-Shear layers."),
        // 6. Convection (mutually exclusive)
        CrossSectionLayerDef("convective", "Convective Risk (Thermo)", "convection", false, "Thermodynamic CAPE / LFC-to-EL convective towers."),
        CrossSectionLayerDef("convective-nwp", "Convective Clouds (NWP)", "convection", true, "Model parameterized convective cloud column."),
        // 7. Levels & Stability Lines
        CrossSectionLayerDef("cruise", "Cruise Altitude", "reference", true, "Planned cruise altitude and flight ceiling reference line."),
        CrossSectionLayerDef("freezing", "0°C Freezing Level", "temperature", true, "0°C isotherm altitude along the route."),
        CrossSectionLayerDef("minus-10", "-10°C Isotherm", "temperature", false, "-10°C prime dendritic icing growth isotherm."),
        CrossSectionLayerDef("minus-20", "-20°C Isotherm", "temperature", false, "-20°C upper icing boundary isotherm."),
        CrossSectionLayerDef("lcl", "LCL (Cloud Base)", "stability", false, "Lifted Condensation Level (convective cloud base)."),
        CrossSectionLayerDef("ceiling", "DD Ceiling", "temperature", false, "Sounding dewpoint-depression BKN/OVC ceiling."),
        CrossSectionLayerDef("ceiling-nwp", "NWP Ceiling", "temperature", false, "Model NWP diagnostic ceiling altitude."),
        CrossSectionLayerDef("lfc", "LFC (Free Convection)", "stability", false, "Level of Free Convection."),
        CrossSectionLayerDef("el", "EL (Equilibrium Level)", "stability", false, "Convective Equilibrium Level (anvil top)."),
        // 8. Observed / Overlays
        CrossSectionLayerDef("current-conditions", "Current Conditions (METAR/TAF/SIGMET)", "conditions", false, "Airport flight category dots and active SIGMET boxes."),
        CrossSectionLayerDef("fronts", "Air-mass boundary", "stability", false, "Model air-mass boundary (thermal front) markers."),
    )

    /**
     * Bidirectional alias mapping between web canonical layer IDs and iOS/Android band IDs.
     */
    val CANONICAL_TO_ALIAS: Map<String, List<String>> = mapOf(
        "clouds-nwp" to listOf("nwp-cloud-bands", "soft-nwp-clouds", "square-nwp-clouds", "soft-nwp-cloud-bands", "square-nwp-cloud-bands"),
        "clouds" to listOf("dd-cloud-bands", "cloud-bands", "soft-dd-clouds", "square-dd-clouds", "soft-cloud-bands", "square-cloud-bands"),
        "icing-ogimet-nwp" to listOf("icing-ogimet-nwp-bands"),
        "icing" to listOf("icing-bands"),
        "icing-sfip" to listOf("sfip-bands"),
        "icing-ieng" to listOf("ieng-icing-bands"),
        "sld" to listOf("sld-bands"),
        "cat" to listOf("cat-bands"),
        "vertical-motion" to listOf("e-shear-bands", "e-shear"),
        "inversions" to listOf("inversion-bands"),
        "convective" to listOf("thermo-convective-bg"),
        "convective-nwp" to listOf("nwp-convective-bg"),
        "freezing" to listOf("freezing-level"),
        "minus-10" to listOf("minus-10c"),
        "minus-20" to listOf("minus-20c"),
        "cruise" to listOf("cruise-altitude", "reference-lines", "ceiling-line"),
        "fronts" to listOf("fronts-markers"),
        "day-night" to listOf("night-shading"),
    )

    val ALIAS_TO_CANONICAL: Map<String, String> = buildMap {
        for ((canonical, aliases) in CANONICAL_TO_ALIAS) {
            for (alias in aliases) {
                put(alias, canonical)
            }
        }
    }

    fun normalizeLayerId(layerId: String): String =
        ALIAS_TO_CANONICAL[layerId] ?: layerId

    fun syncLayerAliases(layers: Map<String, Boolean>): Map<String, Boolean> {
        val out = layers.toMutableMap()
        for ((canonical, aliases) in CANONICAL_TO_ALIAS) {
            val active = out[canonical] == true || aliases.any { out[it] == true }
            if (canonical in out || aliases.any { it in out }) {
                out[canonical] = active
                for (alias in aliases) {
                    out[alias] = active
                }
            }
        }
        return out
    }

    val MUTUALLY_EXCLUSIVE_GROUPS: Map<String, Set<String>> = mapOf(
        "clouds" to setOf(
            "clouds", "clouds-nwp",
            "clouds-icing", "clouds-icing-nwp",
            "clouds-diag", "clouds-diag-nwp",
            "clouds-band", "clouds-band-nwp",
            "nwp-cloud-bands", "soft-nwp-clouds", "square-nwp-clouds", "soft-nwp-cloud-bands", "square-nwp-cloud-bands",
            "dd-cloud-bands", "cloud-bands", "soft-dd-clouds", "square-dd-clouds", "soft-cloud-bands", "square-cloud-bands",
        ),
        "icing" to setOf(
            "icing", "icing-ogimet-nwp", "icing-sfip", "icing-ieng",
            "icing-bands", "icing-ogimet-nwp-bands", "sfip-bands", "ieng-icing-bands",
        ),
        "convection" to setOf(
            "convective", "convective-nwp",
            "thermo-convective-bg", "nwp-convective-bg",
        ),
    )

    val CLOUD_LAYER_BY_AXES: Map<String, Map<String, String>> = mapOf(
        "dd" to mapOf(
            "coverage" to "clouds",
            "icing" to "clouds-icing",
            "diag" to "clouds-diag",
            "band" to "clouds-band",
        ),
        "nwp" to mapOf(
            "coverage" to "clouds-nwp",
            "icing" to "clouds-icing-nwp",
            "diag" to "clouds-diag-nwp",
            "band" to "clouds-band-nwp",
        ),
    )

    val PREFERRED_METHOD_LAYER: Map<String, String> = mapOf(
        "clouds" to "clouds-nwp",
        "icing" to "icing-ogimet-nwp",
        "convection" to "convective-nwp",
        "turbulence" to "cat",
    )

    fun defaultEnabledLayers(): Map<String, Boolean> =
        syncLayerAliases(ALL_LAYERS.associate { it.id to it.defaultEnabled })

    fun deriveGradedMethods(enabledLayers: Map<String, Boolean>): GradedMethods {
        var cloudExtent = "nwp"
        var cloudScale = "coverage"
        for ((extent, scaleMap) in CLOUD_LAYER_BY_AXES) {
            for ((scale, layerId) in scaleMap) {
                if (enabledLayers[layerId] == true) {
                    cloudExtent = extent
                    cloudScale = scale
                }
            }
        }
        val icing = when {
            enabledLayers["icing"] == true -> "ogimet_dd"
            enabledLayers["icing-sfip"] == true -> "sfip_nwp"
            enabledLayers["icing-ieng"] == true -> "ieng_nwp"
            else -> "ogimet_nwp"
        }
        val convective = if (enabledLayers["convective"] == true && enabledLayers["convective-nwp"] != true) "thermo" else "nwp"
        return GradedMethods(
            cloudExtent = cloudExtent,
            cloudScale = cloudScale,
            icing = icing,
            convective = convective,
        )
    }

    val LEVEL_LAYER_IDS: Set<String> = setOf("freezing", "minus-10", "minus-20", "cruise", "ceiling", "ceiling-nwp")
    // The air-mass boundary (`fronts`) is a model diagnostic, not an observation,
    // so it sits under Stability, matching the web bar.
    val STABILITY_LAYER_IDS: Set<String> = setOf("lcl", "lfc", "el", "inversions", "fronts")
    val OBSERVED_LAYER_IDS: Set<String> = setOf("current-conditions", "day-night")

    fun familyLayerIds(family: LayerFamily): Set<String> = when (family) {
        LayerFamily.CLOUDS -> MUTUALLY_EXCLUSIVE_GROUPS["clouds"].orEmpty()
        LayerFamily.ICING -> MUTUALLY_EXCLUSIVE_GROUPS["icing"].orEmpty() + setOf("sld", "sld-bands")
        LayerFamily.TURBULENCE -> setOf("cat", "vertical-motion", "cat-bands", "e-shear-bands", "e-shear")
        LayerFamily.CONVECTION -> MUTUALLY_EXCLUSIVE_GROUPS["convection"].orEmpty()
        LayerFamily.LEVELS -> LEVEL_LAYER_IDS + setOf("freezing-level", "minus-10c", "minus-20c", "cruise-altitude")
        LayerFamily.STABILITY -> STABILITY_LAYER_IDS + setOf("inversion-bands", "fronts-markers")
        LayerFamily.OBSERVED -> OBSERVED_LAYER_IDS + setOf("night-shading")
    }

    fun getCompactLayerOverrides(
        family: LayerFamily,
        currentlyEnabled: Map<String, Boolean>,
        preferredMethods: GradedMethods = deriveGradedMethods(currentlyEnabled),
    ): Map<String, Boolean> {
        val next = currentlyEnabled.toMutableMap()
        val isFamilyActive = isFamilyEnabled(family, currentlyEnabled)
        when (family) {
            LayerFamily.CLOUDS -> {
                for (id in familyLayerIds(LayerFamily.CLOUDS)) {
                    next[id] = false
                }
                if (!isFamilyActive) {
                    val target = CLOUD_LAYER_BY_AXES[preferredMethods.cloudExtent]?.get(preferredMethods.cloudScale)
                        ?: PREFERRED_METHOD_LAYER["clouds"]
                        ?: "clouds-nwp"
                    next[target] = true
                }
            }
            LayerFamily.ICING -> {
                for (id in familyLayerIds(LayerFamily.ICING)) {
                    next[id] = false
                }
                if (!isFamilyActive) {
                    val target = when (preferredMethods.icing) {
                        "ogimet_dd" -> "icing"
                        "sfip_nwp" -> "icing-sfip"
                        "ieng_nwp" -> "icing-ieng"
                        else -> "icing-ogimet-nwp"
                    }
                    next[target] = true
                }
            }
            LayerFamily.TURBULENCE -> {
                val active = !isFamilyActive
                next["cat"] = active
                if (!active) next["vertical-motion"] = false
            }
            LayerFamily.CONVECTION -> {
                for (id in familyLayerIds(LayerFamily.CONVECTION)) {
                    next[id] = false
                }
                if (!isFamilyActive) {
                    val target = if (preferredMethods.convective == "thermo") "convective" else "convective-nwp"
                    next[target] = true
                }
            }
            LayerFamily.LEVELS -> {
                if (isFamilyActive) {
                    for (id in LEVEL_LAYER_IDS) next[id] = false
                } else {
                    next["freezing"] = true
                    next["minus-10"] = true
                    next["minus-20"] = true
                    next["cruise"] = true
                }
            }
            LayerFamily.STABILITY -> {
                if (isFamilyActive) {
                    for (id in familyLayerIds(LayerFamily.STABILITY)) next[id] = false
                } else {
                    next["lcl"] = true
                    next["lfc"] = true
                    next["el"] = true
                }
            }
            LayerFamily.OBSERVED -> {
                if (isFamilyActive) {
                    for (id in familyLayerIds(LayerFamily.OBSERVED)) next[id] = false
                } else {
                    next["current-conditions"] = true
                    next["day-night"] = true
                }
            }
        }
        return syncLayerAliases(next)
    }

    fun isFamilyEnabled(family: LayerFamily, enabled: Map<String, Boolean>): Boolean =
        familyLayerIds(family).any { enabled[it] == true }

    val GRAMET = EmulationPreset(
        id = "gramet",
        label = "GRAMET",
        themeId = "gramet",
        cloudStyle = CloudStyle.NATURAL,
        enabledLayers = syncLayerAliases(
            ALL_LAYERS.associate { it.id to false }.toMutableMap().apply {
                this["day-night"] = true
                this["terrain"] = true
                this["clouds-nwp"] = true
                this["icing-ogimet-nwp"] = true
                this["cat"] = true
                this["convective-nwp"] = true
                this["cruise"] = true
                this["freezing"] = true
            }
        ),
    )

    val WINDY = EmulationPreset(
        id = "windy",
        label = "Windy",
        themeId = "windy",
        cloudStyle = CloudStyle.NATURAL,
        enabledLayers = syncLayerAliases(
            ALL_LAYERS.associate { it.id to false }.toMutableMap().apply {
                this["day-night"] = true
                this["terrain"] = true
                this["clouds-nwp"] = true
                this["cruise"] = true
                this["freezing"] = true
                this["ceiling-nwp"] = true
                this["current-conditions"] = true
            }
        ),
    )

    val FOREFLIGHT = EmulationPreset(
        id = "foreflight",
        label = "ForeFlight",
        themeId = "standard",
        cloudStyle = CloudStyle.SQUARE,
        enabledLayers = syncLayerAliases(
            ALL_LAYERS.associate { it.id to false }.toMutableMap().apply {
                this["day-night"] = true
                this["terrain"] = true
                this["clouds-nwp"] = true
                this["icing-ogimet-nwp"] = true
                this["cat"] = true
                this["cruise"] = true
                this["freezing"] = true
            }
        ),
    )

    val EMULATION_PRESETS: List<EmulationPreset> = listOf(GRAMET, WINDY, FOREFLIGHT)

    val BASIC = AdvisoryPreset(
        id = "basic",
        label = "Basic / Learn",
        caption = "Clouds, icing, turbulence & freezing level",
        interpretation = "Standard cross-section overview with NWP clouds, Ogimet-NWP icing, CAT, convection, and 0°C freezing level.",
        groups = listOf("clouds", "icing", "turbulence", "convection"),
        lines = listOf("cruise", "freezing"),
        routeGraphLeft = "headwind",
        routeGraphRight = "temperature",
        skewtOverlays = listOf("clouds-nwp", "icing-ogimet-nwp"),
        skewtPrimaryVar = "hw_xw",
    )

    val ICING = AdvisoryPreset(
        id = "icing",
        label = "Icing Focus",
        caption = "Icing zones, SLD & freezing isotherms",
        interpretation = "Highlights airframe icing potential, SLD risk, icing regime clouds, and the 0°C / -10°C / -20°C growth region.",
        groups = listOf("clouds", "icing"),
        lines = listOf("cruise", "freezing", "minus-10", "minus-20"),
        routeGraphLeft = "freezing-level",
        routeGraphRight = "temperature",
        skewtOverlays = listOf("icing-ogimet-nwp", "sld", "clouds-nwp"),
        skewtPrimaryVar = "temp_dewp",
    )

    val CLOUDS = AdvisoryPreset(
        id = "clouds",
        label = "Cloud & Ceiling Focus",
        caption = "Cloud layers, LCL & NWP/DD ceilings",
        interpretation = "Compares cloud bands with LCL, DD ceiling, and NWP ceiling along the route.",
        groups = listOf("clouds"),
        lines = listOf("cruise", "lcl", "ceiling", "ceiling-nwp", "freezing"),
        routeGraphLeft = "cloud-cover",
        routeGraphRight = "ceiling-nwp",
        skewtOverlays = listOf("clouds-nwp", "clouds"),
        skewtPrimaryVar = "temp_dewp",
    )

    val CONVECTIVE = AdvisoryPreset(
        id = "convective",
        label = "Convective Focus",
        caption = "Convective towers, LCL / LFC / EL & CAPE",
        interpretation = "Shows convective risk columns alongside thermodynamic stability levels (LCL, LFC, EL) and CAPE/precipitation.",
        groups = listOf("clouds", "convection"),
        lines = listOf("cruise", "lcl", "lfc", "el", "freezing"),
        routeGraphLeft = "cape",
        routeGraphRight = "precipitation",
        skewtOverlays = listOf("convective", "parcel"),
        skewtPrimaryVar = "temp_dewp",
    )

    val TURBULENCE = AdvisoryPreset(
        id = "turbulence",
        label = "Turbulence & Wind",
        caption = "CAT, inversions & vertical motion",
        interpretation = "Highlights Clear Air Turbulence (CAT) shear zones, temperature inversions, and vertical velocity.",
        groups = listOf("clouds", "turbulence"),
        lines = listOf("cruise", "freezing"),
        routeGraphLeft = "headwind",
        routeGraphRight = "crosswind",
        skewtOverlays = listOf("cat", "inversions"),
        skewtPrimaryVar = "wind_speed",
    )

    val VFR = AdvisoryPreset(
        id = "vfr",
        label = "VFR Feasibility",
        caption = "Terrain clearance, clouds, ceiling & visibility",
        interpretation = "Focuses on cloud base clearance above terrain, LCL/ceiling lines, and surface conditions.",
        groups = listOf("clouds", "convection"),
        lines = listOf("cruise", "lcl", "ceiling", "ceiling-nwp"),
        routeGraphLeft = "ceiling-nwp",
        routeGraphRight = "cloud-cover",
        skewtOverlays = listOf("clouds-nwp"),
        skewtPrimaryVar = "hw_xw",
    )

    val IFR = AdvisoryPreset(
        id = "ifr",
        label = "IFR Feasibility",
        caption = "Icing, IMC cloud layers, convection & freezing level",
        interpretation = "Focuses on IMC exposure, icing risk relative to freezing level, and embedded convection.",
        groups = listOf("clouds", "icing", "convection", "turbulence"),
        lines = listOf("cruise", "freezing", "minus-10", "minus-20", "ceiling-nwp"),
        routeGraphLeft = "freezing-level",
        routeGraphRight = "headwind",
        skewtOverlays = listOf("clouds-nwp", "icing-ogimet-nwp"),
        skewtPrimaryVar = "hw_xw",
    )

    val ADVISORY_PRESETS: List<AdvisoryPreset> = listOf(
        BASIC, ICING, CLOUDS, CONVECTIVE, TURBULENCE, VFR, IFR,
    )

    val ADVISORY_TO_PRESET: Map<String, String> = mapOf(
        "aircraft_icing" to "icing",
        "icing_escape" to "icing",
        "fiki_icing" to "icing",
        "icing" to "icing",
        "freezing_level" to "icing",
        "cloud_top" to "clouds",
        "cloud_layers" to "clouds",
        "imc_exposure" to "clouds",
        "ceiling" to "clouds",
        "convective" to "convective",
        "thunderstorm" to "convective",
        "turbulence" to "turbulence",
        "mountain_wave" to "turbulence",
        "wind" to "turbulence",
        "vfr_feasibility" to "vfr",
        "terrain_clearance" to "vfr",
        "ifr_feasibility" to "ifr",
    )

    fun resolveAdvisoryPreset(
        presetIdOrAdvisoryId: String,
        preferredMethods: GradedMethods = GradedMethods(),
    ): Map<String, Boolean> {
        val normalizedId = ADVISORY_TO_PRESET[presetIdOrAdvisoryId.lowercase()]
            ?: presetIdOrAdvisoryId.lowercase()
        val preset = ADVISORY_PRESETS.firstOrNull { it.id == normalizedId } ?: BASIC

        val layers = ALL_LAYERS.associate { it.id to false }.toMutableMap()
        layers["terrain"] = true
        if (normalizedId == "vfr") {
            layers["current-conditions"] = true
            layers["day-night"] = true
        }

        for (group in preset.groups) {
            when (group) {
                "clouds" -> {
                    val cloudLayer = if (normalizedId == "icing") {
                        if (preferredMethods.cloudExtent == "dd") "clouds-icing" else "clouds-icing-nwp"
                    } else {
                        CLOUD_LAYER_BY_AXES[preferredMethods.cloudExtent]?.get(preferredMethods.cloudScale)
                            ?: "clouds-nwp"
                    }
                    layers[cloudLayer] = true
                }
                "icing" -> {
                    val icingLayer = when (preferredMethods.icing) {
                        "ogimet_dd" -> "icing"
                        "sfip_nwp" -> "icing-sfip"
                        "ieng_nwp" -> "icing-ieng"
                        else -> "icing-ogimet-nwp"
                    }
                    layers[icingLayer] = true
                    if (normalizedId == "icing") {
                        layers["sld"] = true
                    }
                }
                "turbulence" -> {
                    layers["cat"] = true
                    if (normalizedId == "turbulence") {
                        layers["inversions"] = true
                        layers["vertical-motion"] = true
                    }
                }
                "convection" -> {
                    val convLayer = if (normalizedId == "convective" || preferredMethods.convective == "thermo") {
                        "convective"
                    } else {
                        "convective-nwp"
                    }
                    layers[convLayer] = true
                }
            }
        }

        for (lineId in preset.lines) {
            layers[lineId] = true
        }

        return syncLayerAliases(layers)
    }
}
