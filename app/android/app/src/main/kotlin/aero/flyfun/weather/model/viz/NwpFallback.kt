package aero.flyfun.weather.model.viz

object NwpFallback {
    private val NWP_CLOUD_LAYERS = setOf(
        "clouds-nwp",
        "clouds-icing-nwp",
        "clouds-diag-nwp",
        "clouds-band-nwp",
        "nwp-cloud-bands",
        "soft-nwp-clouds",
        "square-nwp-clouds",
        "soft-nwp-cloud-bands",
        "square-nwp-cloud-bands",
    )

    private val FALLBACK_MAP = mapOf(
        "clouds-nwp" to "clouds",
        "clouds-icing-nwp" to "clouds-icing",
        "clouds-diag-nwp" to "clouds-diag",
        "clouds-band-nwp" to "clouds-band",
        "nwp-cloud-bands" to "dd-cloud-bands",
        "soft-nwp-clouds" to "soft-dd-clouds",
        "square-nwp-clouds" to "square-dd-clouds",
        "convective-nwp" to "convective",
        "nwp-convective-bg" to "thermo-convective-bg",
        "ceiling-nwp" to "ceiling",
        "icing-ogimet-nwp" to "icing",
        "icing-ogimet-nwp-bands" to "icing-bands",
        "icing-sfip" to "icing",
        "sfip-bands" to "icing-bands",
    )

    fun getUnavailableLayers(data: VizRouteData): Set<String> {
        val points = data.points
        if (points.isEmpty()) return emptySet()

        val unavailable = mutableSetOf<String>()

        // Mirror web/ts/visualization/data-extract.ts:711-729:
        // Ogimet-NWP and IENG backends return [] when no native NWP cloud layers
        // exist, OR when the route is genuinely clear of icing on an NWP-capable
        // model. Gate their availability strictly on hasNwpClouds so a clear route
        // on GFS/ICON does not falsely mark Ogimet-NWP/IENG unavailable or swap in
        // Ogimet-DD. Both Android canonical IDs ("icing-ogimet-nwp", "icing-ieng")
        // and Web band aliases ("-bands") are recorded for syncLayerAliases parity.
        val hasNwpClouds = points.any { it.hasNativeNwpClouds || it.nwpCloudLayers.isNotEmpty() }
        if (!hasNwpClouds) {
            unavailable.addAll(NWP_CLOUD_LAYERS)
            unavailable.add("icing-ogimet-nwp")
            unavailable.add("icing-ogimet-nwp-bands")
            unavailable.add("icing-ieng")
            unavailable.add("ieng-icing-bands")
        }

        val hasNwpConvective = points.any { it.nwpConvective != null }
        if (!hasNwpConvective) {
            unavailable.add("convective-nwp")
            unavailable.add("nwp-convective-bg")
        }

        val hasNwpCeiling = points.any { it.nwpCeilingFt != null }
        if (!hasNwpCeiling) {
            unavailable.add("ceiling-nwp")
        }

        val hasSfip = points.any { it.sfipZones.isNotEmpty() }
        if (!hasSfip) {
            unavailable.add("icing-sfip")
            unavailable.add("sfip-bands")
        }

        val hasSld = points.any { it.sldZones.isNotEmpty() }
        if (!hasSld) {
            unavailable.add("sld")
            unavailable.add("sld-bands")
        }

        // Air-mass boundary: only when the server linked a chain for this model.
        if (data.frontChains.isEmpty()) {
            unavailable.add("fronts")
            unavailable.add("fronts-markers")
        }

        val hasEShear = points.any { it.eShearLayers.isNotEmpty() }
        if (!hasEShear) {
            unavailable.add("e-shear")
            unavailable.add("e-shear-bands")
        }

        return unavailable
    }

    fun applyNwpFallback(
        enabled: Map<String, Boolean>,
        unavailable: Set<String>,
    ): Map<String, Boolean> {
        val synced = CrossSectionPresets.syncLayerAliases(enabled)
        if (unavailable.isEmpty()) return synced
        val result = synced.toMutableMap()
        for (layerId in unavailable) {
            if (result[layerId] == true) {
                result[layerId] = false
                val fallbackId = FALLBACK_MAP[layerId]
                if (fallbackId != null && !unavailable.contains(fallbackId)) {
                    result[fallbackId] = true
                }
            }
        }
        return CrossSectionPresets.syncLayerAliases(result)
    }

    fun getSubstitutedLayers(
        stored: Map<String, Boolean>,
        effective: Map<String, Boolean>,
    ): Set<String> {
        val substituted = mutableSetOf<String>()
        for ((nwpLayer, fallbackLayer) in FALLBACK_MAP) {
            if (stored[nwpLayer] == true && effective[nwpLayer] != true && effective[fallbackLayer] == true) {
                substituted.add(fallbackLayer)
            }
        }
        return substituted
    }
}
