package aero.flyfun.weather.model.viz

enum class LayerFamily(
    val id: String,
    val label: String,
    val summaryHint: String,
    val dotColorHex: Long,
) {
    CLOUDS("clouds", "Clouds", "Cloud bands (NWP or DD)", 0xFF60A5FAL),
    CONVECTION("convection", "Convection", "Convective columns (NWP or Thermo)", 0xFFF59E0BL),
    ICING("icing", "Icing", "Icing risk & SLD zones", 0xFF818CF8L),
    TURBULENCE("turbulence", "Turbulence", "CAT & vertical motion", 0xFFFB923CL),
    LEVELS("levels", "Levels", "0°C, −10°C, −20°C & cruise levels", 0xFF00BCD4L),
    STABILITY("stability", "Stability", "LCL, LFC, EL & inversions", 0xFF4CAF50L),
    OBSERVED("observed", "Observed", "Airports, radar, cloud tops & night", 0xFF38BDF8L);

    companion object {
        val LINES: LayerFamily get() = LEVELS
    }
}

data class CrossSectionLayerDef(
    val id: String,
    val name: String,
    val group: String,
    val defaultEnabled: Boolean,
    val description: String = "",
    val family: LayerFamily = when (group) {
        "clouds" -> LayerFamily.CLOUDS
        "convection" -> LayerFamily.CONVECTION
        "icing" -> LayerFamily.ICING
        "turbulence" -> LayerFamily.TURBULENCE
        "temperature", "reference" -> LayerFamily.LEVELS
        "stability" -> LayerFamily.STABILITY
        "conditions", "terrain" -> LayerFamily.OBSERVED
        else -> LayerFamily.LEVELS
    },
) {
    val label: String get() = name
}

enum class CloudStyle(
    val id: String,
    val label: String,
) {
    SQUARE("square", "Square"),
    SOFT("soft", "Soft"),
    NATURAL("natural", "Natural");

    companion object {
        fun byId(id: String): CloudStyle =
            entries.firstOrNull { it.id.equals(id, ignoreCase = true) } ?: SQUARE
    }
}
