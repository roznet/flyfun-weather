package aero.flyfun.weather.model.viz

enum class LayerFamily(
    val id: String,
    val label: String,
    val summaryHint: String,
) {
    CLOUDS("clouds", "Clouds", "Cloud bands (NWP or DD)"),
    ICING("icing", "Icing", "Icing risk & SLD zones"),
    TURBULENCE("turbulence", "Turbulence", "CAT, inversions & vertical motion"),
    CONVECTION("convection", "Convection", "Convective columns (Thermo or NWP)"),
    LINES("lines", "Lines", "Reference & thermal altitude lines"),
}

data class CrossSectionLayerDef(
    val id: String,
    val name: String,
    val group: String,
    val defaultEnabled: Boolean,
    val description: String = "",
    val family: LayerFamily = when (group) {
        "clouds" -> LayerFamily.CLOUDS
        "icing" -> LayerFamily.ICING
        "turbulence" -> LayerFamily.TURBULENCE
        "convection" -> LayerFamily.CONVECTION
        else -> LayerFamily.LINES
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
            entries.firstOrNull { it.id.equals(id, ignoreCase = true) } ?: SOFT
    }
}
